from __future__ import annotations

import bisect
import json
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import logging
import math
import re
import shutil
import subprocess
import textwrap
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.core.files import File
from django.core.files.storage import storages
from django.db import transaction
from django.utils import timezone
from safedelete.models import HARD_DELETE

from website.ai import AIServiceError, TranscriptionSegment, get_ai_service
from website.models.external_media import (
    ExternalMediaJob,
    ExternalMediaProject,
    GlossaryTerm,
    MediaAsset,
    MediaTemplatePlugin,
    ProjectPipelineStep,
    SubtitleCue,
    SubtitleStyle,
    SubtitleTrack,
)

from .audio_mastering import AudioMasteringService, MasteringTarget
from .audio_mixing import AudioMixingService, DuckingSettings, group_speech_blocks
from .audio_noise import (
    AudioCleanupService,
    AudioNoiseAnalysisService,
    NoiseCleanupSettings,
    NoiseReductionDecision,
    NoiseReductionDecisionBuilder,
    ReductionMode,
)
from .audio_validation import AudioValidationService
from .auto_reframe import AUTO_REFRAME_PLAN_VERSION, AutoReframePlan, AutoReframeService
from .background_voice import BackgroundVoiceRemovalService
from .canonical import (
    EditDecisionSetBuilder,
    EffectiveCapabilities,
    ProjectProcessingState,
    SourceManifestBuilder,
)
from .dialogue_processing import DialogueProcessor, DialogueSettings
from .exceptions import ExternalMediaError
from .ffmpeg_runner import FFmpegRunner
from .media_input import MediaInput, RemoteMediaSource, StoredMediaFile, media_input_factory
from .quality_control import MediaQualityService
from .speech_edit import SpeechCut, SpeechEditAnalyzer, SpeechEditPlan, SpeechEditService
from .workspace import JobWorkspace

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AssemblySource:
    path: str | Path
    label: str = ''
    block_id: int | None = None
    block_key: str = ''
    block_name: str = ''
    skip_extra_processing: bool = False
    remove_background_voice: bool = False
    trim_start_ms: int = 0
    trim_end_ms: int | None = None
    temporary_storage_name: str = ''


class timed_step:
    def __init__(self, label, **context):
        self.label = label
        self.context = context
        self.started = None

    def __enter__(self):
        self.started = time.perf_counter()
        logger.info('Iniciando %s%s', self.label, self._context_suffix())
        return self

    def __exit__(self, exc_type, exc, traceback):
        elapsed = time.perf_counter() - self.started
        if exc_type:
            logger.exception('Falha em %s depois de %.2fs%s', self.label, elapsed, self._context_suffix())
        else:
            logger.info('Finalizado %s em %.2fs%s', self.label, elapsed, self._context_suffix())
        return False

    def _context_suffix(self):
        if not self.context:
            return ''
        details = ', '.join(f'{key}={value}' for key, value in self.context.items())
        return f' ({details})'


def hard_delete_track_cues(track):
    for cue in SubtitleCue.all_objects.filter(track=track):
        cue.delete(force_policy=HARD_DELETE)


def replace_file_safely(instance, field_name, source_path, filename, update_fields=()):
    """Persist a versioned replacement before deleting the previous storage object."""
    field = getattr(instance, field_name)
    old_name = field.name
    storage = field.storage
    generated = Path(field.field.generate_filename(instance, filename))
    versioned = generated.with_name(f'{generated.stem}-{uuid.uuid4().hex[:12]}{generated.suffix}')
    with Path(source_path).open('rb') as source:
        new_name = storage.save(str(versioned), File(source))
    setattr(instance, field_name, new_name)
    fields = list(dict.fromkeys([field_name, *update_fields]))
    try:
        instance.save(update_fields=fields or None)
    except Exception:
        storage.delete(new_name)
        setattr(instance, field_name, old_name)
        raise
    if old_name and old_name != new_name:
        storage.delete(old_name)
    return new_name


@dataclass(frozen=True)
class AudioChunk:
    path: Path
    offset_ms: int


@dataclass(frozen=True)
class VideoMetadata:
    color_space: str = ''
    color_transfer: str = ''
    color_primaries: str = ''
    width: int = 0
    height: int = 0

    @property
    def is_hdr(self):
        values = {self.color_space, self.color_transfer, self.color_primaries}
        return bool({'bt2020nc', 'bt2020', 'smpte2084', 'arib-std-b67'} & values)


class AudioExtractor:
    def __init__(self, runner=None):
        self.runner = runner or FFmpegRunner()

    def extract(self, video_path: Path, workdir: Path) -> list[AudioChunk]:
        output = self.runner.run([
            settings.FFPROBE_BINARY, '-v', 'error', '-show_entries',
            'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', FFmpegRunner.input_arg(video_path),
        ])
        try:
            duration = max(0.1, float(output.strip()))
        except ValueError as exc:
            raise ExternalMediaError('Não foi possível identificar a duração do vídeo.') from exc

        chunk_seconds = settings.EXTERNAL_MEDIA_AUDIO_CHUNK_SECONDS
        chunks = []
        for index in range(max(1, math.ceil(duration / chunk_seconds))):
            offset = index * chunk_seconds
            path = workdir / f'audio_{index:04d}.mp3'
            self.runner.run([
                settings.FFMPEG_BINARY, '-y', '-ss', str(offset), '-i', FFmpegRunner.input_arg(video_path),
                '-t', str(chunk_seconds), '-vn', '-ac', '1', '-ar', '16000',
                '-b:a', '64k', str(path),
            ])
            chunks.append(AudioChunk(path=path, offset_ms=offset * 1000))
        return chunks


class TranscriptionService:
    PROMPT = (
        'Sermão cristão evangélico. Preserve nomes próprios, '
        'referências bíblicas e termos teológicos com pontuação natural.'
    )
    DISFLUENCY_PROMPT = (
        'Transcrição literal para edição de fala em português brasileiro. '
        'Não omita nem corrija hesitações e sons de preenchimento. '
        'Registre como palavras separadas exatamente quando forem ouvidos: '
        'eh, eee, hum, hmm, hamm, ahn, hã e ah.'
    )

    def __init__(self, ai_service=None):
        self.ai_service = ai_service or get_ai_service()

    def transcribe(self, chunks: list[AudioChunk], language: str | None) -> list[TranscriptionSegment]:
        detailed = self.transcribe_detailed(chunks, language)
        return self.group_for_subtitles(detailed)

    def transcribe_detailed(
        self, chunks: list[AudioChunk], language: str | None, *, preserve_disfluencies=False,
    ) -> list[TranscriptionSegment]:
        result = []
        prompt = self.DISFLUENCY_PROMPT if preserve_disfluencies else self.PROMPT
        for chunk in chunks:
            segments = self.ai_service.transcribe_segments(
                chunk.path,
                filename=chunk.path.name,
                language=language,
                prompt=prompt,
            )
            result.extend(
                TranscriptionSegment(
                    start_ms=item.start_ms + chunk.offset_ms,
                    end_ms=item.end_ms + chunk.offset_ms,
                    text=item.text,
                    granularity=item.granularity,
                )
                for item in segments
            )
        clean = self._remove_chunk_overlap(result)
        return clean

    @classmethod
    def group_for_subtitles(cls, detailed, block_boundaries_ms=None):
        if not detailed:
            return detailed
        if not all(item.granularity == 'word' for item in detailed):
            return detailed
        cues = []
        for bucket in cls._bucket_by_boundaries(detailed, block_boundaries_ms):
            # Grouping each block's words independently guarantees no cue can straddle
            # a block boundary, so the last cue of a block never leaks into the next one's speech.
            cues.extend(cls._group_words(bucket))
        return cues

    @staticmethod
    def exclude_protected_ranges(items, protected_ranges):
        """Drops transcript items that overlap a block intentionally kept intact.

        Those blocks are included in the final video as supplied, so burning a caption
        over them would violate the promise to leave them untouched. Dropping an item
        that straddles the boundary is safer than clipping its timing and letting text
        flash over even a few frames of the protected video.
        """
        ranges = [
            (int(item.get('start_ms') or 0), int(item.get('end_ms') or 0))
            for item in (protected_ranges or [])
            if int(item.get('end_ms') or 0) > int(item.get('start_ms') or 0)
        ]
        if not ranges:
            return list(items)
        return [
            item for item in items
            if not any(item.start_ms < end_ms and item.end_ms > start_ms for start_ms, end_ms in ranges)
        ]

    @staticmethod
    def _bucket_by_boundaries(items, block_boundaries_ms):
        boundaries = sorted({int(value) for value in (block_boundaries_ms or []) if value and value > 0})
        if not boundaries:
            return [list(items)]
        buckets = [[] for _ in range(len(boundaries) + 1)]
        for item in items:
            anchor = (item.start_ms + item.end_ms) / 2
            index = bisect.bisect_right(boundaries, anchor)
            buckets[index].append(item)
        return [bucket for bucket in buckets if bucket]

    @staticmethod
    def _remove_chunk_overlap(segments):
        clean = []
        for segment in segments:
            if clean and segment.start_ms < clean[-1].end_ms:
                segment = TranscriptionSegment(
                    start_ms=clean[-1].end_ms,
                    end_ms=max(clean[-1].end_ms + 1, segment.end_ms),
                    text=segment.text,
                    granularity=segment.granularity,
                )
            clean.append(segment)
        return clean

    @staticmethod
    def _group_words(words):
        cues = []
        current = []

        def joined(items):
            value = ''
            for item in items:
                token = item.text
                if not value or token[:1] in '.,!?;:)]}' or token.startswith("'"):
                    value += token
                else:
                    value += ' ' + token
            return value.strip()

        def flush():
            if not current:
                return
            cues.append(TranscriptionSegment(
                start_ms=current[0].start_ms,
                end_ms=current[-1].end_ms,
                text=joined(current),
                granularity='segment',
            ))
            current.clear()

        for word in words:
            if current and word.start_ms - current[-1].end_ms > 900:
                flush()
            current.append(word)
            text = joined(current)
            duration = current[-1].end_ms - current[0].start_ms
            if len(text) >= 42 or duration >= 4500 or (duration >= 1200 and text.endswith(('.', '!', '?'))):
                flush()
        flush()
        return cues


class TranslationService:
    LANGUAGE_NAMES = {
        'pt': 'português brasileiro',
        'en': 'inglês americano',
        'es': 'espanhol',
        'fr': 'francês',
        'it': 'italiano',
    }
    PROTECTED_BRAND_TERMS = (
        'Igreja Filadélfia',
        'Filadélfia',
    )

    def __init__(self, ai_service=None):
        self.ai_service = ai_service or get_ai_service()

    def translate_track(self, source_track: SubtitleTrack, target_language: str, model: str):
        reviewed_track = SubtitleTrack.objects.filter(
            job=source_track.job,
            language=target_language,
            human_reviewed=True,
        ).first()
        if reviewed_track:
            reviewed_track.translation_status = SubtitleTrack.TranslationStatus.SOURCE_CHANGED
            reviewed_track.save(update_fields=['translation_status', 'update_at'])
            return reviewed_track
        source_cues = list(source_track.cues.all())
        translated = []
        batch_size = settings.EXTERNAL_MEDIA_TRANSLATION_BATCH_SIZE
        for start in range(0, len(source_cues), batch_size):
            translated.extend(self._translate_batch(
                source_cues[start:start + batch_size],
                source_track.language,
                target_language,
                model,
            ))

        with transaction.atomic():
            track, _ = SubtitleTrack.objects.get_or_create(
                job=source_track.job,
                language=target_language,
                defaults={'is_source': False},
            )
            if track.human_reviewed:
                track.translation_status = SubtitleTrack.TranslationStatus.SOURCE_CHANGED
                track.save(update_fields=['translation_status', 'update_at'])
                return track
            hard_delete_track_cues(track)
            SubtitleCue.objects.bulk_create([
                SubtitleCue(
                    track=track,
                    cue_index=source.cue_index,
                    start_ms=source.start_ms,
                    end_ms=source.end_ms,
                    text=text,
                )
                for source, text in zip(source_cues, translated)
            ])
        return track

    def _translate_batch(self, cues, source_language, target_language, model):
        glossary = list(GlossaryTerm.objects.filter(
            source_language=source_language,
            target_language=target_language,
            is_active=True,
        ).values('source_text', 'translated_text'))
        payload = [{'cue_id': cue.cue_index, 'text': cue.text} for cue in cues]
        payload, protected_terms = self._inject_protected_terms(payload, glossary)
        prompt = json.dumps(
            {'glossary': glossary, 'protected_terms': protected_terms, 'cues': payload},
            ensure_ascii=False,
        )
        instructions = f"""Você é um tradutor e editor nativo especializado em sermões, anúncios de igreja,
ministérios evangélicos e conteúdo cristão de contexto carismático/pentecostal.

Traduza de {self.LANGUAGE_NAMES[source_language]} para {self.LANGUAGE_NAMES[target_language]}
contemporâneo, natural e idiomático.

OBJETIVO PRINCIPAL

O resultado deve soar como se tivesse sido originalmente escrito e falado por uma
igreja evangélica/carismática americana, e não como um texto traduzido do português.

Preserve integralmente o significado, a intenção, o tom pastoral e a mensagem do
original, mas não preserve estruturas do português quando elas soarem artificiais
em inglês.

Traduza a intenção e o sentido da fala, e não palavras ou estruturas individualmente.

NATURALIDADE E CONTEXTO CRISTÃO

Use vocabulário, construções e expressões naturalmente utilizadas por falantes
nativos de inglês americano em igrejas evangélicas/carismáticas.

Quando houver várias traduções semanticamente corretas, prefira aquela que um
falante nativo americano provavelmente usaria naquele contexto.

Exemplos de princípio de tradução:

- "vem estar com a gente" pode naturalmente se tornar "come join us";
- "expandir o Reino de Deus" pode se tornar "advance God's Kingdom";
- "orar pelos aniversariantes" pode se tornar "pray over those celebrating birthdays";
- expressões de convite devem soar calorosas e naturais, e não como traduções literais.

Estes exemplos demonstram o estilo desejado e não devem ser tratados como
substituições obrigatórias.

Evite:

- traduções palavra por palavra;
- estruturas que revelem sintaxe portuguesa;
- expressões gramaticalmente corretas, mas pouco naturais para um americano;
- linguagem excessivamente formal quando a fala original for casual;
- linguagem excessivamente acadêmica ou arcaica;
- adaptar desnecessariamente termos teológicos que já possuem uso estabelecido
  no contexto cristão americano.

CONTEXTO ENTRE BLOCOS

Os cues pertencem a uma fala contínua. Leia e interprete todos os cues recebidos
no lote como partes do mesmo discurso. Uma frase, ideia ou construção gramatical
pode começar em um cue e continuar no seguinte. Use os cues anteriores e posteriores
como contexto para compreender corretamente cada trecho. Não trate cada cue como
uma frase independente.

Apesar disso, preserve rigorosamente a correspondência entre os blocos: cada cue_id
de entrada deve gerar exatamente um cue_id de saída.

Não junte, divida, remova, reordene ou acrescente blocos. Não mova informação de um
cue para outro apenas para melhorar a escrita. A tradução deve continuar
semanticamente alinhada ao trecho correspondente.

CRITICAL FIDELITY RULE

Never invent, infer, complete, or add information that is not explicitly present
in the source text, even when doing so would make the translation sound smoother
or make a cue read better on its own.

Some cues may contain only the end or beginning of a sentence because subtitle
segmentation follows timestamps. This is expected.

If a cue contains only a sentence fragment, translate only that fragment.
Do not add new content to make the cue feel complete.

GLOSSÁRIO E TERMINOLOGIA

Priorize o glossário fornecido. Use traduções oficiais quando definidas pelo glossário
e respeite a terminologia estabelecida pela Igreja Filadélfia.

Quando o glossário fornecer uma orientação terminológica que permita flexibilidade
gramatical ou contextual, incorpore o termo da maneira mais natural possível na
frase em inglês. Não deixe uma substituição terminológica tornar a frase artificial
se a regra permitir adaptação contextual.

TERMOS PROTEGIDOS

Preserve nomes próprios, marcas e termos explicitamente protegidos. Qualquer item
listado em protected_terms ou qualquer token no formato __TERM_X__ deve ser mantido
EXATAMENTE igual. Nunca traduza, adapte, pluralize, flexione, reformule ou substitua
esses tokens.

REFERÊNCIAS BÍBLICAS E TEOLOGIA

Preserve o significado teológico do original. Use terminologia cristã naturalmente
reconhecida no inglês americano. Preserve referências bíblicas e não altere
deliberadamente seu significado. Não acrescente interpretações teológicas,
explicações ou informações que não estejam presentes no original.

FIDELIDADE

Naturalidade não significa liberdade para reescrever a mensagem. Não acrescente
informações, remova informações relevantes, intensifique ou enfraqueça afirmações,
altere doutrina ou intenção, invente explicações ou transforme a tradução em uma
paráfrase livre.

A tradução pode reorganizar a construção linguística dentro do mesmo cue quando
necessário para soar natural em inglês, desde que preserve o significado original.

PONTUAÇÃO DE LEGENDAS

Não adicione reticências ("..." ou "…"), travessões, hífens isolados ou hífens
duplicados ("--") para indicar continuidade, pausa ou conteúdo omitido. Se o cue
for apenas um fragmento, traduza somente o fragmento, sem usar essa pontuação para
fazê-lo parecer uma frase completa.

FORMATO DE SAÍDA

Para cada cue_id de entrada, devolva exatamente um item correspondente. Não devolva
timestamps. Responda SOMENTE JSON válido no formato:

{{"cues":[{{"cue_id":1,"text":"translated cue"}}]}}

Não inclua comentários, explicações, Markdown ou qualquer texto fora do JSON."""
        expected_ids = [cue.cue_index for cue in cues]
        for attempt in range(2):
            raw = self.ai_service.generate_text(
                prompt,
                instructions=instructions,
                model=model,
                max_output_tokens=max(1200, len(cues) * 100),
            )
            try:
                translated = self._parse_translated_cues(raw, expected_ids)
                restored = self._restore_protected_terms(translated, protected_terms)
                return [self._clean_translated_subtitle_text(text) for text in restored]
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                logger.warning('Resposta de tradução inválida; tentativa %s: %s', attempt + 1, exc)
        if len(cues) > 1:
            logger.warning(
                'Batch de tradução falhou para %s blocos; quebrando em lotes menores.',
                len(cues),
            )
            midpoint = len(cues) // 2
            if midpoint > 0:
                return (
                    self._translate_batch(cues[:midpoint], source_language, target_language, model)
                    + self._translate_batch(cues[midpoint:], source_language, target_language, model)
                )
        raise ExternalMediaError('A tradução não preservou todos os blocos de legenda.')

    @staticmethod
    def _strip_code_fence(value):
        value = value.strip()
        match = re.fullmatch(r'```(?:json)?\s*(.*?)\s*```', value, flags=re.DOTALL)
        return match.group(1) if match else value

    def _parse_translated_cues(self, raw, expected_ids):
        cleaned_raw = self._strip_code_fence(raw)
        if len(expected_ids) == 1:
            plain_text = self._plain_text_translation(cleaned_raw)
            if plain_text:
                return [plain_text]
        data = json.loads(self._extract_json_object(cleaned_raw))
        items = data['cues']
        if not isinstance(items, list):
            raise ValueError('Lista de cues inválida')
        translated_by_id = {}
        for item in items:
            cue_id = int(item['cue_id'])
            text = self._extract_translated_text(item)
            if not text:
                raise ValueError('Texto traduzido vazio')
            translated_by_id[cue_id] = text
        returned_ids = sorted(translated_by_id.keys())
        if returned_ids != sorted(expected_ids):
            raise ValueError('IDs não correspondem aos blocos esperados')
        return [translated_by_id[cue_id] for cue_id in expected_ids]

    @staticmethod
    def _plain_text_translation(value):
        value = value.strip()
        if not value or value.startswith('{') or value.startswith('['):
            return ''
        if re.search(r'\b(cue_id|translated_text|translation|content)\b', value):
            return ''
        return value

    @staticmethod
    def _extract_json_object(value):
        decoder = json.JSONDecoder()
        try:
            _, end = decoder.raw_decode(value)
            return value[:end]
        except json.JSONDecodeError:
            match = re.search(r'\{.*\}', value, flags=re.DOTALL)
            if match:
                return match.group(0)
            raise

    @staticmethod
    def _extract_translated_text(item):
        for key in ('text', 'translated_text', 'translation', 'content'):
            value = item.get(key)
            if value is None:
                continue
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    return cleaned
            if isinstance(value, list):
                parts = []
                for entry in value:
                    if isinstance(entry, str):
                        if entry.strip():
                            parts.append(entry.strip())
                    elif isinstance(entry, dict):
                        text = str(entry.get('text', '')).strip()
                        if text:
                            parts.append(text)
                cleaned = ' '.join(parts).strip()
                if cleaned:
                    return cleaned
        raise ValueError('Campo de texto traduzido ausente')

    @staticmethod
    def _clean_translated_subtitle_text(value):
        """Remove marcadores de continuação que não devem aparecer na legenda final."""
        text = re.sub(r'\s+', ' ', str(value or '')).strip()
        text = re.sub(r'\s*(?:\.{3,}|…)\s*', ' ', text)
        text = re.sub(r'\s*(?:--+|—|–)\s*', ' ', text)
        return re.sub(r'\s{2,}', ' ', text).strip()

    @classmethod
    def _inject_protected_terms(cls, payload, glossary):
        protected_sources = {
            item['source_text'].strip()
            for item in glossary
            if str(item.get('source_text', '')).strip()
            and str(item.get('translated_text', '')).strip()
            and str(item.get('source_text', '')).strip() == str(item.get('translated_text', '')).strip()
        }
        protected_sources.update(term for term in cls.PROTECTED_BRAND_TERMS if term)
        protected_sources = sorted(protected_sources, key=len, reverse=True)
        protected_terms = []
        token_map = {}
        for index, source_text in enumerate(protected_sources, start=1):
            token = f'__TERM_{index}__'
            protected_terms.append({'token': token, 'text': source_text})
            token_map[source_text] = token

        if not token_map:
            return payload, protected_terms

        protected_payload = []
        for item in payload:
            text = item['text']
            for source_text, token in token_map.items():
                text = text.replace(source_text, token)
            protected_payload.append({**item, 'text': text})
        return protected_payload, protected_terms

    @staticmethod
    def _restore_protected_terms(translated, protected_terms):
        if not protected_terms:
            return translated
        restored = []
        for text in translated:
            for item in protected_terms:
                text = text.replace(item['token'], item['text'])
            restored.append(text)
        return restored


class SubtitleService:
    @staticmethod
    def milliseconds_to_srt(value):
        hours, rest = divmod(value, 3_600_000)
        minutes, rest = divmod(rest, 60_000)
        seconds, milliseconds = divmod(rest, 1000)
        return f'{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}'

    @staticmethod
    def milliseconds_to_vtt(value):
        return SubtitleService.milliseconds_to_srt(value).replace(',', '.')

    @staticmethod
    def wrap_text(value, max_characters, max_lines):
        if '\n' in value:
            lines = value.splitlines()
        else:
            lines = textwrap.wrap(value, width=max_characters, break_long_words=False) or ['']
        if len(lines) <= max_lines:
            return '\n'.join(lines)
        head = lines[:max_lines - 1]
        head.append(' '.join(lines[max_lines - 1:]))
        return '\n'.join(head)

    def write_srt(self, track, path, style):
        blocks = []
        for cue in track.cues.all():
            text = self.wrap_text(cue.text, style.max_characters, style.max_lines)
            blocks.append(
                f'{cue.cue_index}\n{self.milliseconds_to_srt(cue.start_ms)} --> '
                f'{self.milliseconds_to_srt(cue.end_ms)}\n{text}'
            )
        path.write_text('\n\n'.join(blocks) + '\n', encoding='utf-8')

    def write_vtt(self, track, path, style):
        blocks = ['WEBVTT']
        for cue in track.cues.all():
            text = self.wrap_text(cue.text, style.max_characters, style.max_lines)
            blocks.append(
                f'{self.milliseconds_to_vtt(cue.start_ms)} --> '
                f'{self.milliseconds_to_vtt(cue.end_ms)}\n{text}'
            )
        path.write_text('\n\n'.join(blocks) + '\n', encoding='utf-8')

    def write_ass(self, track, path, style, width=1920, height=1080):
        styles = self._ass_styles_for_role('Default', style, style.margin_bottom)
        header = self._ass_header(width, height, styles)
        rows = []
        for cue in track.cues.all():
            text = self.wrap_text(cue.text, style.max_characters, style.max_lines)
            text = self._ass_escape(text).replace('\n', r'\N')
            rows.extend(self._ass_dialogue_rows(
                start_ms=cue.start_ms,
                end_ms=cue.end_ms,
                role='Default',
                style=style,
                text=text,
                prefix='',
                width=width,
                height=height,
                margin_bottom=style.margin_bottom,
            ))
        path.write_text(header + '\n'.join(rows) + '\n', encoding='utf-8-sig')

    def write_dual_ass(
        self,
        tracks,
        path,
        original_style,
        width=1920,
        height=1080,
        source_language='pt',
        translated_style=None,
    ):
        ordered_tracks = self._ordered_dual_tracks(tracks, source_language)
        translated_style = translated_style or original_style
        if len(ordered_tracks) < 2:
            self.write_ass(ordered_tracks[0], path, original_style, width, height)
            return
        original_margin, translated_margin = self._dual_style_margins(original_style, translated_style)
        styles = [
            *self._ass_styles_for_role('Original', original_style, original_margin),
            *self._ass_styles_for_role('Translated', translated_style, translated_margin),
        ]
        header = self._ass_header(width, height, styles)
        rows = []
        # Uma legenda bilíngue é sempre um par. O cue original é a referência de
        # tempo para os dois idiomas, evitando que cada idioma seja repartido em
        # intervalos diferentes quando um deles tem mais caracteres.
        original_cues = {
            cue.cue_index: cue for cue in ordered_tracks[0].cues.all()
        }
        translated_cues = {
            cue.cue_index: cue for cue in ordered_tracks[1].cues.all()
        }
        for cue_index, original_cue in original_cues.items():
            translated_cue = translated_cues.get(cue_index)
            paired_rows = (
                (original_cue, 'Original', original_style, original_margin),
                (translated_cue, 'Translated', translated_style, translated_margin),
            )
            for cue, role, style, role_margin in paired_rows:
                if cue is None:
                    continue
                text = self._ass_escape(self._single_line_text(cue.text))
                rows.extend(self._ass_dialogue_rows(
                    start_ms=original_cue.start_ms,
                    end_ms=original_cue.end_ms,
                    role=role,
                    style=style,
                    text=text,
                    prefix=r'{\q2}',
                    width=width,
                    height=height,
                    margin_bottom=role_margin,
                ))
        path.write_text(header + '\n'.join(rows) + '\n', encoding='utf-8-sig')

    def _ass_header(self, width, height, styles):
        style_lines = '\n'.join(styles)
        return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style_lines}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    @classmethod
    def _ass_styles_for_role(cls, role, style, margin_bottom):
        styles = [cls._ass_style_line(role, style, margin_bottom, kind='main')]
        if cls._needs_shadow_layer(style):
            styles.append(cls._ass_style_line(f'{role}Shadow', style, margin_bottom, kind='shadow'))
        if cls._needs_dedicated_background_layer(style):
            styles.append(cls._ass_style_line(f'{role}BG', style, margin_bottom, kind='background'))
        return styles

    @staticmethod
    def _role_layer_base(role):
        # libass faz anti-colisão só dentro do mesmo layer. Legendas duplas usam margens
        # independentes e podem ficar visualmente próximas; separar os layers evita que
        # o render empurre a tradução para cima com espaçamento "padrão".
        if role == 'Translated':
            return 10
        return 0

    @classmethod
    def _ass_dialogue_rows(
        cls,
        start_ms,
        end_ms,
        role,
        style,
        text,
        prefix='',
        width=0,
        height=0,
        margin_bottom=None,
    ):
        start = cls._ass_time(start_ms)
        end = cls._ass_time(end_ms)
        position_prefix = ''
        if width > 0 and height > 0 and margin_bottom is not None:
            position_prefix = cls._ass_position_override(style, margin_bottom, width, height)
        full_prefix = f'{prefix}{position_prefix}'
        rows = []
        layer = cls._role_layer_base(role)
        dedicated_background = cls._needs_dedicated_background_layer(style)
        if dedicated_background:
            bg_override = cls._ass_background_layer_override(style)
            rows.append(
                f'Dialogue: {layer},{start},{end},{role}BG,,0,0,0,,{full_prefix}{bg_override}{text}'
            )
            layer += 1
        if cls._needs_shadow_layer(style):
            # A sombra tem que ficar SEMPRE entre o fundo (se houver) e o texto principal —
            # senão a caixa de fundo é desenhada por cima e "engole" a sombra por completo.
            for shadow_override in cls._shadow_layer_overrides(style):
                rows.append(
                    f'Dialogue: {layer},{start},{end},{role}Shadow,,0,0,0,,{full_prefix}{shadow_override}{text}'
                )
            layer += 1
        if dedicated_background:
            rows.append(
                f'Dialogue: {layer},{start},{end},{role},,0,0,0,,{full_prefix}{text}'
            )
            return rows
        background_override = cls._ass_background_override(style)
        rows.append(
            f'Dialogue: {layer},{start},{end},{role},,0,0,0,,{full_prefix}{background_override}{text}'
        )
        return rows

    @staticmethod
    def _needs_shadow_layer(style):
        # Sombra avançada (ângulo/distância/tamanho/desfoque) sempre em camada própria:
        # BorderStyle=4 substitui a sombra pela caixa, e o Style.Shadow do ASS só aceita
        # um deslocamento diagonal simples — sem ângulo, tamanho ou blur.
        opacity = max(0, min(100, int(getattr(style, 'shadow_opacity', 70) or 0)))
        if opacity <= 0:
            return False
        distance = max(0, int(getattr(style, 'shadow', 0) or 0))
        size = max(0, int(getattr(style, 'shadow_size', 0) or 0))
        blur = max(0, int(getattr(style, 'shadow_blur', 0) or 0))
        return distance > 0 or size > 0 or blur > 0

    @classmethod
    def _needs_dedicated_background_layer(cls, style):
        if not getattr(style, 'background_enabled', False):
            return False
        # Precisa de camada própria se a altura for reduzida (via \fscy) OU se houver
        # sombra — assim a sombra entra ENTRE a caixa e o texto, em vez de ficar embutida
        # no mesmo evento do texto (onde a caixa acabaria desenhada por cima dela).
        return cls._needs_background_layer(style) or cls._needs_shadow_layer(style)

    @staticmethod
    def _needs_background_layer(style):
        if not getattr(style, 'background_enabled', False):
            return False
        height_percent = max(40, min(100, int(getattr(style, 'background_height_percent', 100) or 100)))
        return height_percent < 100

    @staticmethod
    def _shadow_offset_xy(style):
        distance = max(0, float(getattr(style, 'shadow', 0) or 0))
        try:
            raw_angle = getattr(style, 'shadow_angle', 45)
            angle = 45 if raw_angle is None else int(raw_angle) % 360
        except (TypeError, ValueError):
            angle = 45
        # 0° = direita, 90° = baixo (eixo Y do ASS cresce para baixo).
        radians = math.radians(angle)
        dx = round(distance * math.cos(radians), 2)
        dy = round(distance * math.sin(radians), 2)
        return dx, dy

    @staticmethod
    def _format_ass_number(value):
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        text = f'{value:.2f}'.rstrip('0').rstrip('.')
        return text or '0'

    @staticmethod
    def _ass_style_line(name, style, margin_bottom, kind='main'):
        color = style.primary_color.lstrip('#').zfill(6)
        background = getattr(style, 'background_color', '#000000').lstrip('#').zfill(6)
        outline = style.outline_color.lstrip('#').zfill(6)
        primary_opacity = max(0, min(100, int(getattr(style, 'primary_opacity', 100) or 100)))
        primary_alpha_hex = SubtitleService._ass_alpha_from_opacity(primary_opacity)
        primary_color = f'&H{primary_alpha_hex}{color[4:6]}{color[2:4]}{color[0:2]}'
        outline_color = f'&H00{outline[4:6]}{outline[2:4]}{outline[0:2]}'
        shadow_opacity = max(0, min(100, int(getattr(style, 'shadow_opacity', 70) or 0)))
        shadow_alpha_hex = SubtitleService._ass_alpha_from_opacity(shadow_opacity)
        background_opacity = max(0, min(100, int(getattr(style, 'background_opacity', 70) or 0)))
        background_alpha_hex = SubtitleService._ass_alpha_from_opacity(background_opacity)
        background_enabled = bool(getattr(style, 'background_enabled', False))
        font_weight = int(getattr(style, 'font_weight', 700) or 700)
        bold_flag = -1 if font_weight >= 600 else 0
        ass_margin_v = SubtitleService._ass_margin_v(style, margin_bottom)

        if kind == 'shadow':
            # Camada fantasma: offsets/tamanho/blur vêm do override no Dialogue.
            back_colour = f'&H{shadow_alpha_hex}000000'
            return (
                f'Style: {name},{style.font_name},{style.font_size},&HFF000000,&H000000FF,'
                f'&HFF000000,{back_colour},{bold_flag},0,0,0,100,100,0,0,1,'
                f'0,0,{SubtitleService._ass_alignment(style)},40,40,{ass_margin_v},1'
            )

        if kind == 'background':
            # Camada só da caixa (BS=4); texto invisível; altura controlada via \fscy no override.
            back_colour = f'&H{background_alpha_hex}{background[4:6]}{background[2:4]}{background[0:2]}'
            return (
                f'Style: {name},{style.font_name},{style.font_size},&HFF000000,&H000000FF,'
                f'&HFF000000,{back_colour},{bold_flag},0,0,0,100,100,0,0,4,'
                f'0,0,{SubtitleService._ass_alignment(style)},40,40,{ass_margin_v},1'
            )

        # kind == main — sombra fica na camada Shadow quando ativa.
        style_shadow = 0
        if background_enabled and SubtitleService._needs_dedicated_background_layer(style):
            border_style = 1
            back_colour = f'&HFF000000'
            effective_outline = style.outline_width
        elif background_enabled:
            # BorderStyle=4: uma caixa por evento (evita soma de alpha do BS=3 em multilinha).
            border_style = 4
            back_colour = f'&H{background_alpha_hex}{background[4:6]}{background[2:4]}{background[0:2]}'
            effective_outline = style.outline_width
        else:
            border_style = 1
            back_colour = f'&HFF000000'
            effective_outline = style.outline_width

        return (
            f'Style: {name},{style.font_name},{style.font_size},{primary_color},&H000000FF,'
            f'{outline_color},{back_colour},{bold_flag},0,0,0,100,100,0,0,{border_style},'
            f'{effective_outline},{style_shadow},{SubtitleService._ass_alignment(style)},40,40,{ass_margin_v},1'
        )

    @staticmethod
    def _ass_margin_v(style, margin_bottom):
        # BorderStyle=4 desenha a caixa de fundo com padding via \yshad (ver
        # _ass_background_override), mas esse padding NÃO desloca o texto nem entra no
        # cálculo de alinhamento/margem do libass. Inflamos o MarginV para a borda da
        # caixa terminar no margin_bottom configurado, batendo com o preview.
        if not getattr(style, 'background_enabled', False):
            return margin_bottom
        alignment = SubtitleService._ass_alignment(style)
        if 4 <= alignment <= 6:
            return margin_bottom
        pad_y = max(0, int(getattr(style, 'background_padding_y', 0) or 0))
        return margin_bottom + pad_y

    @staticmethod
    def _ass_position_override(style, margin_bottom, width, height):
        # \pos fixa o ponto de ancoragem em pixels do preset — espelha o preview CSS
        # (bottom = margin_bottom + padding_y para o texto; caixa desce via padding).
        alignment = SubtitleService._ass_alignment(style)
        margin_v = SubtitleService._ass_margin_v(style, margin_bottom)
        side_margin = 40
        column = alignment % 3 or 3
        row = (alignment - 1) // 3
        if column == 1:
            x = side_margin
        elif column == 2:
            x = width // 2
        else:
            x = max(side_margin, width - side_margin)
        if row == 0:
            y = max(0, height - margin_v)
        elif row == 1:
            y = height // 2
        else:
            y = margin_v
        return f'{{\\an{alignment}\\pos({x},{y})}}'

    @staticmethod
    def _ass_background_override(style):
        if not getattr(style, 'background_enabled', False):
            return ''
        if SubtitleService._needs_dedicated_background_layer(style):
            return ''
        background = getattr(style, 'background_color', '#000000').lstrip('#').zfill(6)
        background_opacity = max(0, min(100, int(getattr(style, 'background_opacity', 70) or 0)))
        alpha_hex = SubtitleService._ass_alpha_from_opacity(background_opacity)
        box_color = f'{background[4:6]}{background[2:4]}{background[0:2]}'
        pad_x = max(0, int(getattr(style, 'background_padding_x', 14) or 0))
        pad_y = max(0, int(getattr(style, 'background_padding_y', 8) or 0))
        return f'{{\\4c&H{box_color}&\\4a&H{alpha_hex}&\\xshad{pad_x}\\yshad{pad_y}}}'

    @staticmethod
    def _ass_background_layer_override(style):
        background = getattr(style, 'background_color', '#000000').lstrip('#').zfill(6)
        background_opacity = max(0, min(100, int(getattr(style, 'background_opacity', 70) or 0)))
        alpha_hex = SubtitleService._ass_alpha_from_opacity(background_opacity)
        box_color = f'{background[4:6]}{background[2:4]}{background[0:2]}'
        pad_x = max(0, int(getattr(style, 'background_padding_x', 14) or 0))
        pad_y = max(0, int(getattr(style, 'background_padding_y', 8) or 0))
        height_percent = max(40, min(100, int(getattr(style, 'background_height_percent', 100) or 100)))
        # Texto invisível + escala vertical da caixa (libass não encolhe BS=4 abaixo da fonte
        # só com yshad negativo; \fscy na camada BG permite faixa mais baixa que o texto).
        return (
            f'{{\\1a&HFF&\\3a&HFF&\\4c&H{box_color}&\\4a&H{alpha_hex}&'
            f'\\fscy{height_percent}\\xshad{pad_x}\\yshad{pad_y}}}'
        )

    @staticmethod
    def _shadow_layer_overrides(style):
        # libass desenha a sombra como glifo na cor primária (\1c/\1a), NÃO via BackColour
        # (\4c/\4a com \1a&HFF& no texto principal — essa combinação fica invisível).
        # Empilhamos cópias nítidas do glifo preto, cada uma um pouco mais longe no mesmo
        # ângulo, espelhando os múltiplos `text-shadow` do preview (version_form.html).
        opacity = max(0, min(100, int(getattr(style, 'shadow_opacity', 70) or 0)))
        alpha_hex = SubtitleService._ass_alpha_from_opacity(opacity)
        dx, dy = SubtitleService._shadow_offset_xy(style)
        blur = max(0, int(getattr(style, 'shadow_blur', 0) or 0))
        blur_tag = f'\\blur{blur}' if blur > 0 else ''
        size = max(0, int(getattr(style, 'shadow_size', 0) or 0))
        steps = min(4, max(1, round(size / 3))) if size > 0 else 0
        overrides = []
        for index in range(steps + 1):
            growth = 1 + ((index / steps) * (size * 0.04)) if steps else 1
            step_dx = SubtitleService._format_ass_number(round(dx * growth, 2))
            step_dy = SubtitleService._format_ass_number(round(dy * growth, 2))
            overrides.append(
                f'{{\\1c&H000000&\\1a&H{alpha_hex}&\\3a&HFF&\\bord0{blur_tag}'
                f'\\xshad{step_dx}\\yshad{step_dy}}}'
            )
        return overrides

    @staticmethod
    def _ass_alpha_from_opacity(opacity):
        return format(round((100 - opacity) * 255 / 100), '02X')

    @staticmethod
    def _ass_alignment(style):
        try:
            alignment = int(getattr(style, 'alignment', 2) or 2)
        except (TypeError, ValueError):
            return 2
        return alignment if 1 <= alignment <= 9 else 2

    @classmethod
    def _dual_style_margins(cls, original_style, translated_style):
        # A margem mais baixa continua sendo a âncora escolhida pelo usuário. A outra
        # legenda é limitada a um empilhamento compacto acima dela: sem isso, duas
        # margens válidas porém distantes (por exemplo 30px e 121px) viram um vão
        # visual muito maior no libass do que no preview.
        original_margin = max(0, int(getattr(original_style, 'margin_bottom', 60) or 0))
        translated_margin = max(0, int(getattr(translated_style, 'margin_bottom', 60) or 0))
        original_alignment = cls._ass_alignment(original_style)
        translated_alignment = cls._ass_alignment(translated_style)
        if original_alignment <= 3 and translated_alignment <= 3:
            if original_margin <= translated_margin:
                compact_translated_margin = original_margin + cls._subtitle_stack_gap(
                    translated_style, original_style,
                )
                translated_margin = min(translated_margin, compact_translated_margin)
            else:
                compact_original_margin = translated_margin + cls._subtitle_stack_gap(
                    original_style, translated_style,
                )
                original_margin = min(original_margin, compact_original_margin)
        return original_margin, translated_margin

    @staticmethod
    def _subtitle_stack_gap(original_style, translated_style):
        font_size = max(
            int(getattr(original_style, 'font_size', 48) or 48),
            int(getattr(translated_style, 'font_size', 48) or 48),
        )
        outline = max(
            int(getattr(original_style, 'outline_width', 0) or 0),
            int(getattr(translated_style, 'outline_width', 0) or 0),
        )
        shadow = max(
            int(getattr(original_style, 'shadow', 0) or 0)
            + int(getattr(original_style, 'shadow_size', 0) or 0)
            + int(getattr(original_style, 'shadow_blur', 0) or 0),
            int(getattr(translated_style, 'shadow', 0) or 0)
            + int(getattr(translated_style, 'shadow_size', 0) or 0)
            + int(getattr(translated_style, 'shadow_blur', 0) or 0),
        )
        # A caixa (BorderStyle=4) da legenda traduzida (a de baixo, âncora fixa) se estende
        # `padding_y` para CIMA e para BAIXO do próprio texto sem mover seu MarginV (ver
        # _ass_margin_v). Altura do fundo < 100% reduz a caixa via \fscy na camada BG.
        height_percent = max(
            40,
            min(100, int(getattr(translated_style, 'background_height_percent', 100) or 100)),
        ) if getattr(translated_style, 'background_enabled', False) else 100
        translated_padding = (
            int(getattr(translated_style, 'background_padding_y', 0) or 0) * 2
            if getattr(translated_style, 'background_enabled', False) else 0
        )
        box_height = math.ceil(font_size * 1.0 * (height_percent / 100))
        return box_height + outline + shadow + translated_padding + 8

    @staticmethod
    def _ass_escape(text):
        return text.replace('\\', r'\\').replace('{', r'\{').replace('}', r'\}')

    @staticmethod
    def _single_line_text(text):
        return re.sub(r'\s+', ' ', str(text or '').replace('\n', ' ')).strip()

    @classmethod
    def _split_dual_text_chunks(cls, text, max_characters):
        # Legendas duplas ficam em uma linha empilhada; quando passam do limite do estilo,
        # quebramos em vários trechos (palavra inteira) que viram legendas sequenciais com
        # tempo repartido — nunca omitimos texto com reticências.
        single_line = cls._single_line_text(text)
        limit = max(1, int(max_characters or 42))
        if len(single_line) <= limit:
            return [single_line]
        chunks = textwrap.wrap(single_line, width=limit, break_long_words=False)
        return chunks or [single_line]

    @staticmethod
    def _split_cue_segments(start_ms, end_ms, chunks):
        if not chunks:
            return []
        if len(chunks) == 1:
            return [(start_ms, end_ms, chunks[0])]
        duration = max(1, end_ms - start_ms)
        total_chars = sum(len(chunk) for chunk in chunks)
        segments = []
        cursor = start_ms
        for index, chunk in enumerate(chunks):
            if index == len(chunks) - 1:
                segments.append((cursor, end_ms, chunk))
                continue
            chunk_duration = max(1, round(duration * len(chunk) / total_chars))
            segment_end = min(end_ms - 1, cursor + chunk_duration)
            if segment_end <= cursor:
                segment_end = cursor + 1
            segments.append((cursor, segment_end, chunk))
            cursor = segment_end
        return segments

    @staticmethod
    def _ordered_dual_tracks(tracks, source_language):
        return sorted(
            list(tracks),
            key=lambda track: (0 if track.language == source_language else 1, track.language),
        )

    @staticmethod
    def _ass_time(value):
        hours, rest = divmod(value, 3_600_000)
        minutes, rest = divmod(rest, 60_000)
        seconds, milliseconds = divmod(rest, 1000)
        return f'{hours}:{minutes:02d}:{seconds:02d}.{milliseconds // 10:02d}'


class RenderService:
    def __init__(self, runner=None, subtitle_service=None):
        self.runner = runner or FFmpegRunner()
        self.subtitle_service = subtitle_service or SubtitleService()

    def render(self, video_path, track, output_path, preset, style, workdir):
        return self.render_tracks(video_path, [track], output_path, preset, style, workdir)

    @staticmethod
    def ass_play_res(preset, metadata):
        # PlayRes must match the frame size when ffmpeg burns subtitles, not necessarily
        # the probed source dimensions (scale/crop may run first in the filter chain).
        if preset.width and preset.height:
            return preset.width, preset.height
        return metadata.width or 1920, metadata.height or 1080

    def render_tracks(
        self,
        video_path,
        tracks,
        output_path,
        preset,
        style,
        workdir,
        source_language='pt',
        translated_style=None,
    ):
        metadata = self.probe_video(video_path)
        width, height = self.ass_play_res(preset, metadata)
        tracks = list(tracks)
        suffix = '_'.join(track.language for track in tracks)
        ass_path = workdir / f'{suffix}.ass'
        if len(tracks) > 1:
            self.subtitle_service.write_dual_ass(
                tracks,
                ass_path,
                style,
                width,
                height,
                source_language,
                translated_style,
            )
        else:
            self.subtitle_service.write_ass(tracks[0], ass_path, style, width, height)
        escaped_ass_path = str(ass_path).replace('\\', r'\\').replace(':', r'\:').replace("'", r"\'")
        filters = self.build_video_filters(preset, escaped_ass_path, metadata)
        command = [
            settings.FFMPEG_BINARY, '-y', '-i', FFmpegRunner.input_arg(video_path), '-vf', ','.join(filters),
            '-c:v', preset.video_codec,
        ]
        if preset.video_codec == 'libx264' and settings.EXTERNAL_MEDIA_RENDER_PRESET:
            command.extend(['-preset', settings.EXTERNAL_MEDIA_RENDER_PRESET])
        command.extend([
            '-crf', str(preset.video_crf),
            '-pix_fmt', 'yuv420p',
            '-colorspace', 'bt709',
            '-color_primaries', 'bt709',
            '-color_trc', 'bt709',
            '-c:a', preset.audio_codec,
            '-movflags', '+faststart',
        ])
        command.extend(str(arg) for arg in preset.extra_ffmpeg_args)
        command.append(str(output_path))
        try:
            self.runner.run(command)
        except ExternalMediaError:
            if preset.audio_codec != 'copy':
                raise
            logger.warning('Falha ao copiar audio; tentando renderizar novamente com AAC.')
            fallback_command = list(command)
            audio_codec_index = fallback_command.index('-c:a') + 1
            fallback_command[audio_codec_index] = 'aac'
            self.runner.run(fallback_command)

    def probe_video(self, video_path):
        try:
            output = self.runner.run([
                settings.FFPROBE_BINARY, '-v', 'error', '-select_streams', 'v:0',
                '-show_entries', 'stream=width,height,color_space,color_transfer,color_primaries',
                '-of', 'json', FFmpegRunner.input_arg(video_path),
            ])
            data = json.loads(output or '{}')
            stream = (data.get('streams') or [{}])[0]
            return VideoMetadata(
                color_space=stream.get('color_space') or '',
                color_transfer=stream.get('color_transfer') or '',
                color_primaries=stream.get('color_primaries') or '',
                width=int(stream.get('width') or 0),
                height=int(stream.get('height') or 0),
            )
        except (ExternalMediaError, json.JSONDecodeError, IndexError, TypeError):
            logger.warning('Nao foi possivel identificar o perfil de cor do video; usando SDR padrao.')
            return VideoMetadata()

    @staticmethod
    def subtitle_fonts_dir():
        return Path(settings.BASE_DIR) / 'static' / 'fonts' / 'subtitles'

    @staticmethod
    def build_video_filters(preset, escaped_ass_path, metadata):
        filters = []
        if metadata.is_hdr:
            filters.extend([
                'format=gbrpf32le',
                'tonemap=tonemap=hable:desat=0:peak=100',
                'format=yuv420p',
            ])
        if preset.width and preset.height:
            filters.extend([
                f'scale={preset.width}:{preset.height}:force_original_aspect_ratio=increase:flags=lanczos',
                f'crop={preset.width}:{preset.height}:(iw-ow)/2:(ih-oh)/2',
            ])
        fonts_dir = RenderService.subtitle_fonts_dir()
        if fonts_dir.is_dir() and any(fonts_dir.glob('*.[ot]tf')):
            escaped_fonts_dir = str(fonts_dir).replace('\\', r'\\').replace(':', r'\:').replace("'", r"\'")
            ass_filter = f"ass='{escaped_ass_path}':fontsdir='{escaped_fonts_dir}'"
        else:
            ass_filter = f"ass='{escaped_ass_path}'"
        filters.extend([
            ass_filter,
            'format=yuv420p',
        ])
        return filters


class StorageService:
    @staticmethod
    def input(field_file):
        return media_input_factory(field_file)

    def ffmpeg_input(self, field_file):
        return self.input(field_file).get_ffmpeg_input()

    def copy_to_local(self, field_file, destination, *, purpose='explicit-fallback'):
        return self.input(field_file).materialize(destination, purpose=purpose)

    def stage_temporary(self, source_path, namespace):
        """Upload a scratch output and return a signed FFmpeg input plus its key."""
        source_path = Path(source_path)
        if not settings.USE_S3:
            return source_path, None
        storage = storages['external_media']
        suffix = source_path.suffix.lower() or '.mp4'
        name = f'external_media/tmp/{namespace}/{uuid.uuid4().hex}{suffix}'
        size = source_path.stat().st_size
        with source_path.open('rb') as source:
            stored_name = storage.save(name, File(source))
        reference = StoredMediaFile(storage=storage, name=stored_name, size=size)
        try:
            staged_input = self.input(reference).get_ffmpeg_input()
        except Exception:
            storage.delete(stored_name)
            raise
        source_path.unlink()
        logger.info(
            'media_temporary_staged storage_name=%s output_bytes=%s',
            stored_name,
            size,
        )
        return staged_input, stored_name

    @staticmethod
    def delete_temporary(name):
        if not name:
            return
        try:
            storages['external_media'].delete(name)
        except Exception:
            logger.exception('media_temporary_delete_failed storage_name=%s', name)

    @contextmanager
    def staged_processing_input(self, source_path, namespace):
        """Bound local peak usage to the next output when S3 streaming is active."""
        if isinstance(source_path, RemoteMediaSource) or not settings.USE_S3:
            yield source_path
            return
        staged, temporary_name = self.stage_temporary(source_path, namespace)
        try:
            yield staged
        finally:
            self.delete_temporary(temporary_name)

    def save_asset(self, job, kind, language, source_path, filename):
        asset = MediaAsset.objects.filter(job=job, kind=kind, language=language).first()
        if not asset:
            asset = MediaAsset(job=job, kind=kind, language=language)
        asset.file_size = source_path.stat().st_size
        if asset.pk:
            replace_file_safely(asset, 'file', source_path, filename, ('file_size', 'update_at'))
        else:
            with source_path.open('rb') as source:
                asset.file.save(filename, File(source), save=False)
            asset.save()
        return asset

    def save_asset_from_field(self, job, kind, language, source_field, filename):
        """Stream a storage object into an asset without a worker-disk copy."""
        asset = MediaAsset.objects.filter(job=job, kind=kind, language=language).first()
        if not asset:
            asset = MediaAsset(job=job, kind=kind, language=language)
        old_name = asset.file.name if asset.pk and asset.file else ''
        asset.file_size = int(getattr(source_field, 'size', 0) or 0)
        with source_field.open('rb') as source:
            asset.file.save(filename, File(source), save=False)
        asset.save()
        if old_name and old_name != asset.file.name:
            asset.file.storage.delete(old_name)
        return asset


class TemplateService:
    """Reads the immutable template snapshot attached to a project."""

    @staticmethod
    def enabled_plugins(project):
        return EffectiveCapabilities.resolve(project)


class ProjectService:
    STEP_SEQUENCE = (
        'upload',
        'assembly',
        MediaTemplatePlugin.Code.AUTO_TRACKING,
        MediaTemplatePlugin.Code.SILENCE_REMOVAL,
        MediaTemplatePlugin.Code.FILLER_REMOVAL,
        'dialogue_processing',
        'audio_noise_cleanup',
        MediaTemplatePlugin.Code.SUBTITLE_PT,
        MediaTemplatePlugin.Code.TRANSLATION_EN,
        MediaTemplatePlugin.Code.LUT,
        MediaTemplatePlugin.Code.MUSIC,
        'audio_mixing',
        'audio_mastering',
        'render',
        'storage',
    )
    STEP_LABELS = {
        'upload': 'Uploads validados',
        'assembly': 'Montando blocos',
        'silence_removal': 'Cortando silêncio',
        'filler_removal': 'Removendo vícios de fala',
        'auto_tracking': 'Aplicando Auto Reframe',
        'lut': 'Aplicando LUT',
        'subtitle_pt': 'Gerando legenda PT',
        'translation_en': 'Traduzindo para inglês',
        'intro': 'Aplicando intro',
        'outro': 'Aplicando tela final',
        'music': 'Aplicando música',
        'dialogue_processing': 'Tratando diálogo',
        'audio_noise_cleanup': 'Analisando ruído',
        'audio_mixing': 'Mixando áudio',
        'audio_mastering': 'Masterizando áudio',
        'render': 'Renderizando',
        'storage': 'Salvando arquivos',
    }

    @staticmethod
    def validate_uploads(project):
        from .overlays import OverlayTimelineService

        counts = {}
        errors = []
        for item in project.block_media.all():
            if ProjectService.file_exists(item.file):
                counts[item.block_id] = counts.get(item.block_id, 0) + 1
            else:
                errors.append(
                    f'{(item.block or item.custom_block).name}: o vídeo enviado não está mais disponível. Envie-o novamente.'
                )
        for block in project.template_version.blocks.all():
            count = counts.get(block.pk, 0)
            has_default = ProjectService.file_exists(block.default_video)
            if block.default_video and not has_default and not count:
                errors.append(
                    f'{block.name}: o vídeo padrão do template não está disponível. '
                    'Envie um vídeo para este bloco ou restaure o arquivo no template.'
                )
            effective_count = count or (1 if has_default else 0)
            minimum = block.min_occurrences if block.is_required else 0
            if effective_count < minimum:
                errors.append(f'{block.name}: envie pelo menos {minimum} vídeo(s).')
            if block.max_occurrences > 0 and count > block.max_occurrences:
                errors.append(f'{block.name}: máximo de {block.max_occurrences} vídeo(s).')
        for overlay in OverlayTimelineService.ensure_project_overlays(project):
            if not overlay.is_required and not OverlayTimelineService.has_renderable_content(
                overlay.overlay_type, overlay.content, overlay.image_file,
            ):
                continue
            try:
                OverlayTimelineService.validate_content(
                    overlay.overlay_type, overlay.content_schema, overlay.content, overlay.image_file,
                )
            except ValueError as exc:
                errors.append(f'{overlay.block.name if overlay.block else "Overlay"}: {exc}')
        version = project.template_version
        track = getattr(version, 'background_music', None)
        if track and track.audio_file and not ProjectService.file_exists(track.audio_file):
            errors.append(
                'A música de fundo do template não está disponível. '
                'Restaure ou substitua a música no cadastro do template.'
            )
        elif track and track.audio_file and ProjectService.file_size(track.audio_file) < 1024:
            errors.append(
                'A música de fundo do template está vazia ou inválida. '
                'Envie novamente um arquivo de áudio válido no template.'
            )
        elif version.music_file and not ProjectService.file_exists(version.music_file):
            errors.append(
                'O arquivo de música de fundo do template não está disponível. '
                'Restaure ou substitua a música no cadastro do template.'
            )
        elif version.music_file and ProjectService.file_size(version.music_file) < 1024:
            errors.append(
                'O arquivo de música de fundo do template está vazio ou inválido. '
                'Envie novamente um arquivo de áudio válido no template.'
            )
        selected_lut = version.color_lut.lut_file if version.color_lut_id else version.lut_file
        if selected_lut and not ProjectService.file_exists(selected_lut):
            errors.append(
                'O arquivo LUT do template não está disponível. Restaure ou substitua o arquivo no template.'
            )
        return errors

    @staticmethod
    def file_exists(field_file):
        if not field_file or not getattr(field_file, 'name', ''):
            return False
        try:
            return field_file.storage.exists(field_file.name)
        except OSError:
            return False

    @staticmethod
    def file_size(field_file):
        if not field_file or not getattr(field_file, 'name', ''):
            return 0
        try:
            return field_file.storage.size(field_file.name)
        except OSError:
            return 0

    def initialize_steps(self, project, plugins):
        codes = ['upload', 'assembly'] + [plugin.code for plugin in plugins] + ['render', 'storage']
        sequence = {code: index for index, code in enumerate(self.STEP_SEQUENCE)}
        codes.sort(key=lambda code: (sequence.get(code, len(sequence)), code))
        seen = set()
        active_codes = []
        for order, code in enumerate(codes, start=1):
            if code in seen:
                continue
            seen.add(code)
            active_codes.append(code)
            ProjectPipelineStep.objects.update_or_create(
                project=project,
                code=code,
                defaults={
                    'label': self.STEP_LABELS.get(code, code.replace('_', ' ').title()),
                    'order': order,
                    'status': ProjectPipelineStep.Status.PENDING,
                    'progress': 0,
                    'started_at': None,
                    'finished_at': None,
                    'message': '',
                },
            )
        project.pipeline_steps.exclude(code__in=active_codes).update(
            status=ProjectPipelineStep.Status.SKIPPED,
            progress=100,
            message='Etapa desativada na configuração atual.',
        )


class LUTService:
    @staticmethod
    def selected_file(version, enabled_codes):
        if MediaTemplatePlugin.Code.LUT not in enabled_codes:
            return None
        selected_lut = getattr(version, 'color_lut', None)
        if selected_lut and selected_lut.lut_file:
            return selected_lut.lut_file
        return version.lut_file if version.lut_file else None

    @staticmethod
    def with_intensity(lut_path, intensity, workdir):
        """Materialize a partial .cube LUT so FFmpeg needs only one image pass.

        Blending source and graded frames works visually, but at 4K it doubles
        the per-frame image work. Interpolating the cube table once preserves the
        requested intensity and lets FFmpeg use a single `lut3d` filter.
        """
        if not lut_path:
            return None
        try:
            percentage = min(100, max(0, int(intensity)))
        except (TypeError, ValueError):
            percentage = 50
        source = Path(lut_path)
        if percentage <= 0:
            return None
        if percentage >= 100 or source.suffix.lower() != '.cube':
            return source
        if not source.is_file():
            return source
        try:
            lines = source.read_text(encoding='utf-8-sig').splitlines()
            size = next(
                int(line.split()[1]) for line in lines
                if line.strip().upper().startswith('LUT_3D_SIZE ')
            )
            rows = []
            for index, line in enumerate(lines):
                values = line.strip().split()
                if len(values) != 3:
                    continue
                try:
                    rows.append((index, tuple(float(value) for value in values)))
                except ValueError:
                    continue
            if size < 2 or len(rows) != size ** 3:
                raise ValueError('Tabela LUT_3D inválida.')
            ratio = percentage / 100
            output = list(lines)
            for cube_index, (line_index, graded) in enumerate(rows):
                base = (
                    # .cube tables used by FFmpeg enumerate red first, then
                    # green, then blue (the same ordering used by our admin LUTs).
                    cube_index % size / (size - 1),
                    (cube_index // size) % size / (size - 1),
                    cube_index // (size * size) / (size - 1),
                )
                output[line_index] = ' '.join(
                    f'{original + (target - original) * ratio:.8f}'
                    for original, target in zip(base, graded)
                )
            generated = Path(workdir) / f'lut_intensity_{percentage}.cube'
            generated.write_text('\n'.join(output) + '\n', encoding='utf-8')
            return generated
        except (OSError, StopIteration, ValueError, IndexError, ZeroDivisionError):
            # Keep the established frame-blend path for exotic/invalid LUT files.
            logger.warning('Não foi possível preparar o LUT com intensidade; usando mistura compatível.', exc_info=True)
            return source


class IntroOutroService:
    @staticmethod
    def enabled_fields(version, enabled_codes):
        intro = version.intro_video if MediaTemplatePlugin.Code.INTRO in enabled_codes and version.intro_video else None
        outro = version.outro_video if MediaTemplatePlugin.Code.OUTRO in enabled_codes and version.outro_video else None
        return intro, outro


class MusicService:
    @staticmethod
    def selected_file(version, enabled_codes=None):
        """Return the template track.

        Music is configured directly on a template version, unlike optional
        editorial plugins.  ``enabled_codes`` remains accepted for compatibility
        with existing callers, but must not suppress a selected track.
        """
        selected_music = getattr(version, 'background_music', None)
        if selected_music and selected_music.audio_file:
            return selected_music.audio_file
        return version.music_file if version.music_file else None


class VideoAssemblyService:
    """Normalizes every clip once, then concatenates without a second video encode."""

    def __init__(self, runner=None, storage=None):
        self.runner = runner or FFmpegRunner()
        self.storage = storage or StorageService()
        self.last_reframe_plans = []
        self.last_protected_ranges = []
        self.last_block_ranges = []

    def assemble(
        self, sources, output_path, preset, workdir, lut_path=None, lut_intensity=50, music_path=None,
        music_volume=0.15, auto_reframe_config=None, analysis_sources=None,
        reframe_plans=None, progress_callback=None,
    ):
        self.last_reframe_plans = []
        self.last_protected_ranges = []
        self.last_block_ranges = []
        source_items = [self._coerce_source(source) for source in sources]
        source_paths = [source.path for source in source_items]
        if (
            len(source_items) == 1
            and not lut_path
            and not music_path
            and not auto_reframe_config
            and not (preset.width and preset.height)
        ):
            if isinstance(source_paths[0], Path):
                shutil.copyfile(source_paths[0], output_path)
            else:
                self.runner.run([
                    settings.FFMPEG_BINARY, '-y', '-i', source_paths[0], '-c', 'copy',
                    '-movflags', '+faststart', str(output_path),
                ])
            return False
        width = preset.width or 1920
        height = preset.height or 1080
        normalized = []
        temporary_normalized = []
        used_auto_reframe = False
        prepared_lut_path = LUTService.with_intensity(lut_path, lut_intensity, workdir)
        lut_is_prepared = bool(prepared_lut_path and prepared_lut_path != Path(lut_path))
        analysis_source_items = [
            self._coerce_source(source) for source in (analysis_sources or source_items)
        ]
        analysis_sources = [source.path for source in analysis_source_items]
        reframe_plans = reframe_plans or []
        timeline_cursor = 0
        normalize_jobs = []
        for index, source_item in enumerate(source_items):
            source = source_item.path
            destination = workdir / f'normalized_{index:03d}.mp4'
            source_duration = self._effective_duration_ms(source_item)
            range_entry = {
                'start_ms': timeline_cursor,
                'end_ms': timeline_cursor + source_duration,
                'block_id': source_item.block_id,
                'block_key': source_item.block_key,
                'block_name': source_item.block_name,
                'label': source_item.label,
                # "Manter intacto" always wins over every optional edit,
                # including quiet-background-voice removal.
                'skip_extra_processing': source_item.skip_extra_processing,
                'remove_background_voice': source_item.remove_background_voice,
            }
            # Tracks the timeline span of every clip (not just "manter intacto" blocks) so
            # subtitles can later be prevented from bleeding across a block boundary.
            self.last_block_ranges.append(dict(range_entry))
            if source_item.skip_extra_processing:
                self.last_protected_ranges.append(range_entry)
            timeline_cursor += source_duration
            effective_auto_reframe_config = None if source_item.skip_extra_processing else auto_reframe_config
            effective_lut_path = None if source_item.skip_extra_processing else prepared_lut_path
            effective_lut_intensity = 0 if source_item.skip_extra_processing else (
                100 if lut_is_prepared else lut_intensity
            )
            normalize_jobs.append({
                'source': source,
                'destination': destination,
                'width': width,
                'height': height,
                'lut_path': effective_lut_path,
                'lut_intensity': effective_lut_intensity,
                'auto_reframe_config': effective_auto_reframe_config,
                'analysis_source': analysis_sources[index],
                'reframe_plan_data': reframe_plans[index] if index < len(reframe_plans) else None,
                'trim_start_ms': source_item.trim_start_ms,
                'trim_end_ms': source_item.trim_end_ms,
                'preserve_framing': source_item.skip_extra_processing,
            })
            normalized.append(destination)

        # In the final pass the proxy phase has already produced all crop plans.
        # The 4K takes are then independent, so a bounded pool can encode two at
        # once. We keep the analysis pass serial to avoid competing face detectors.
        workers = min(max(1, settings.EXTERNAL_MEDIA_ASSEMBLY_WORKERS), len(normalize_jobs))
        if settings.USE_S3:
            # Upload each completed segment before starting the next one. This
            # deliberately trades some parallelism for a bounded scratch peak.
            workers = 1
        serialized_reframe_plans = {}
        if workers > 1 and reframe_plans:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='media-assemble') as executor:
                futures = [executor.submit(self._normalize, **job) for job in normalize_jobs]
                reframe_results = []
                for index, future in enumerate(futures, start=1):
                    reframe_results.append(future.result())
                    if progress_callback:
                        progress_callback(index, len(futures))
        else:
            reframe_results = []
            try:
                for index, job in enumerate(normalize_jobs, start=1):
                    reframe_plan = self._normalize(**job)
                    reframe_results.append(reframe_plan)
                    effective_auto_reframe_config = job['auto_reframe_config']
                    if effective_auto_reframe_config:
                        # This can point at a temporary S3 proxy. Record its
                        # dimensions before deleting that proxy below.
                        serialized_reframe_plans[index - 1] = self._serialize_reframe_plan(
                            reframe_plan,
                            analysis_sources[index - 1],
                        )
                    source_temporary_name = source_items[index - 1].temporary_storage_name
                    if source_temporary_name:
                        self.storage.delete_temporary(source_temporary_name)
                    analysis_temporary_name = analysis_source_items[index - 1].temporary_storage_name
                    if analysis_temporary_name and analysis_temporary_name != source_temporary_name:
                        self.storage.delete_temporary(analysis_temporary_name)
                    if settings.USE_S3:
                        staged, temporary_name = self.storage.stage_temporary(
                            normalized[index - 1],
                            f'assembly-{uuid.uuid4().hex[:12]}-{index - 1:03d}',
                        )
                        normalized[index - 1] = staged
                        temporary_normalized.append(temporary_name)
                    if progress_callback:
                        progress_callback(index, len(normalize_jobs))
            except Exception:
                source_temporary_names = {
                    item.temporary_storage_name
                    for item in [*source_items, *analysis_source_items]
                    if item.temporary_storage_name
                }
                for temporary_name in source_temporary_names:
                    self.storage.delete_temporary(temporary_name)
                for temporary_name in temporary_normalized:
                    self.storage.delete_temporary(temporary_name)
                raise
        try:
            for index, reframe_plan in enumerate(reframe_results):
                effective_auto_reframe_config = normalize_jobs[index]['auto_reframe_config']
                used_auto_reframe = bool(reframe_plan) or used_auto_reframe
                self.last_reframe_plans.append(
                    (
                        serialized_reframe_plans[index]
                        if index in serialized_reframe_plans
                        else self._serialize_reframe_plan(reframe_plan, analysis_sources[index])
                    ) if effective_auto_reframe_config else None
                )
            concat_file = workdir / 'concat.txt'
            concat_file.write_text(
                ''.join(
                    f"file '{str(path).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n"
                    for path in normalized
                ),
                encoding='utf-8',
            )
            # The manifest contains presigned URLs while this workspace exists.
            concat_file.chmod(0o600)
            assembled = output_path if not music_path else workdir / 'assembled_without_music.mp4'
            self.runner.run([
                settings.FFMPEG_BINARY, '-y',
                '-protocol_whitelist', 'file,http,https,tcp,tls,crypto',
                '-f', 'concat', '-safe', '0', '-i', str(concat_file),
                '-c', 'copy', '-movflags', '+faststart', str(assembled),
            ])
        finally:
            for temporary_name in temporary_normalized:
                self.storage.delete_temporary(temporary_name)
            for path in normalized:
                if not isinstance(path, Path):
                    continue
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    logger.warning('Não foi possível remover intermediário normalizado %s.', path)
        if music_path:
            AudioMixingService(self.runner).mix(
                assembled, music_path, output_path,
                music_volume=music_volume,
                duration_ms=self._duration_ms(assembled),
                ducking_enabled=False,
            )
        return used_auto_reframe

    def create_proxy(self, source, destination, trim_start_ms=0, trim_end_ms=None, profile=None):
        source_width, source_height = self._video_dimensions(source)
        max_width = int(getattr(profile, 'max_width', 0) or settings.EXTERNAL_MEDIA_PROXY_WIDTH)
        proxy_width = min(source_width, max_width)
        proxy_height = self._even(proxy_width * source_height / source_width)
        fps = int(getattr(profile, 'fps', 0) or 30)
        crf = int(getattr(profile, 'video_crf', 0) or settings.EXTERNAL_MEDIA_PROXY_CRF)
        audio_bitrate = int(getattr(profile, 'audio_bitrate_kbps', 0) or 96)
        command = [settings.FFMPEG_BINARY, '-y', '-i', FFmpegRunner.input_arg(source)]
        command.extend([
            *self._trim_output_args(trim_start_ms, trim_end_ms),
            '-vf', f'scale={proxy_width}:{proxy_height}:flags=fast_bilinear,fps={fps},setsar=1',
            '-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'libx264',
            '-preset', settings.EXTERNAL_MEDIA_PROXY_PRESET,
            '-crf', str(crf),
            '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', f'{audio_bitrate}k',
            '-ar', '48000', '-ac', '2', '-shortest', '-movflags', '+faststart', str(destination),
        ])
        self.runner.run(command)
        return destination

    def proxy_preset(self, preset):
        width = preset.width or 1920
        height = preset.height or 1080
        if width <= settings.EXTERNAL_MEDIA_PROXY_WIDTH:
            return preset
        proxy_width = settings.EXTERNAL_MEDIA_PROXY_WIDTH
        proxy_height = self._even(proxy_width * height / width)
        return SimpleNamespace(width=proxy_width, height=proxy_height)

    def _normalize(
        self, source, destination, width, height, lut_path, lut_intensity=50, auto_reframe_config=None,
        analysis_source=None, reframe_plan_data=None, trim_start_ms=0, trim_end_ms=None,
        preserve_framing=False,
    ):
        has_audio = self._has_audio(source)
        metadata = RenderService(runner=self.runner).probe_video(source)
        command = [settings.FFMPEG_BINARY, '-y', '-i', FFmpegRunner.input_arg(source)]
        if not has_audio:
            command.extend(['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo'])
        filters = []
        if metadata.is_hdr:
            filters.extend([
                'format=gbrpf32le',
                'tonemap=tonemap=hable:desat=0:peak=100',
                'format=yuv420p',
            ])
        reframe_plan = None
        if auto_reframe_config:
            analysis_source = analysis_source or source
            if reframe_plan_data is not None:
                plan_payload = reframe_plan_data.get('plan')
                if plan_payload:
                    reframe_plan = AutoReframePlan.from_dict(plan_payload)
                    analysis_width = int(reframe_plan_data.get('analysis_width') or 0)
                    analysis_height = int(reframe_plan_data.get('analysis_height') or 0)
                    source_width, source_height = self._video_dimensions(source)
                    if analysis_width and analysis_height:
                        reframe_plan = reframe_plan.scaled(
                            source_width / max(1, analysis_width),
                            source_height / max(1, analysis_height),
                        )
            else:
                reframe_plan = AutoReframeService(
                    priority=auto_reframe_config.get('priority', 'face'),
                    safe_margin=auto_reframe_config.get('safe_margin', 0.15),
                    top_margin=auto_reframe_config.get('top_margin'),
                    interval_frames=auto_reframe_config.get('interval_frames'),
                    smoothing=auto_reframe_config.get('smoothing', 0.18),
                    horizontal_smoothing=auto_reframe_config.get('horizontal_smoothing'),
                    vertical_lock=auto_reframe_config.get('vertical_lock'),
                ).analyze(analysis_source, width, height)
                if reframe_plan and analysis_source != source:
                    source_width, source_height = self._video_dimensions(source)
                    analysis_width, analysis_height = self._video_dimensions(analysis_source)
                    reframe_plan = reframe_plan.scaled(
                        source_width / max(1, analysis_width),
                        source_height / max(1, analysis_height),
                    )
        if reframe_plan:
            filters.extend(reframe_plan.ffmpeg_filters(width, height))
        elif preserve_framing:
            filters.extend([
                f'scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos',
                f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black',
            ])
        else:
            filters.extend([
                f'scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos',
                f'crop={width}:{height}:(iw-ow)/2:(ih-oh)/2',
            ])
        filters.extend(['fps=30', 'setsar=1'])
        if lut_path and int(lut_intensity or 0) > 0:
            escaped = str(lut_path).replace('\\', r'\\').replace(':', r'\:').replace("'", r"\'")
            intensity = min(100, max(0, int(lut_intensity))) / 100
            if intensity >= 1:
                filters.append(f"lut3d='{escaped}'")
            else:
                # Blend original and graded images, matching Premiere's LUT intensity.
                filters.extend([
                    'split=2[lut_original][lut_graded_source]',
                    f"[lut_graded_source]lut3d='{escaped}'[lut_graded]",
                    f"[lut_original][lut_graded]blend=all_expr='A*{1 - intensity:.3f}+B*{intensity:.3f}'",
                ])
        command.extend([
            *self._trim_output_args(trim_start_ms, trim_end_ms),
            '-vf', ','.join(filters), '-map', '0:v:0', '-map', '0:a:0' if has_audio else '1:a:0',
            '-c:v', 'libx264', '-preset', settings.EXTERNAL_MEDIA_INTERMEDIATE_PRESET,
            '-crf', str(settings.EXTERNAL_MEDIA_INTERMEDIATE_CRF), '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k',
            '-ar', '48000', '-ac', '2', '-colorspace', 'bt709', '-color_primaries', 'bt709',
            '-color_trc', 'bt709', '-shortest', str(destination),
        ])
        self.runner.run(command)
        return reframe_plan

    def _serialize_reframe_plan(self, plan, analysis_source):
        width, height = self._video_dimensions(analysis_source)
        return {
            'analysis_width': width,
            'analysis_height': height,
            'plan': plan.as_dict() if plan else None,
        }

    def _has_audio(self, source):
        output = self.runner.run([
            settings.FFPROBE_BINARY, '-v', 'error', '-select_streams', 'a:0',
            '-show_entries', 'stream=index', '-of', 'csv=p=0', FFmpegRunner.input_arg(source),
        ])
        return bool(output.strip())

    def _duration_ms(self, source):
        output = self.runner.run([
            settings.FFPROBE_BINARY, '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', FFmpegRunner.input_arg(source),
        ]).strip()
        try:
            return max(1, round(float(output) * 1000))
        except (TypeError, ValueError) as exc:
            raise ExternalMediaError('Não foi possível identificar a duração do vídeo.') from exc

    def _effective_duration_ms(self, source_item):
        duration = self._duration_ms(source_item.path)
        start = max(0, int(source_item.trim_start_ms or 0))
        end = int(source_item.trim_end_ms) if source_item.trim_end_ms else duration
        end = min(duration, max(start + 1, end))
        return max(1, end - start)

    def _video_dimensions(self, source):
        output = self.runner.run([
            settings.FFPROBE_BINARY, '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height', '-of', 'csv=s=x:p=0', FFmpegRunner.input_arg(source),
        ]).strip()
        try:
            width, height = output.split('x', 1)
            return max(2, int(width)), max(2, int(height))
        except ValueError as exc:
            raise ExternalMediaError('Não foi possível identificar a resolução do vídeo.') from exc

    @staticmethod
    def _even(value):
        return max(2, int(round(value)) // 2 * 2)

    @staticmethod
    def _coerce_source(source):
        if isinstance(source, AssemblySource):
            return source
        if isinstance(source, MediaInput):
            source = source.get_ffmpeg_input()
        if isinstance(source, RemoteMediaSource) or str(source).startswith(('http://', 'https://')):
            return AssemblySource(source)
        return AssemblySource(Path(source))

    @staticmethod
    def _trim_output_args(trim_start_ms=0, trim_end_ms=None):
        start_ms = max(0, int(trim_start_ms or 0))
        args = ['-ss', f'{start_ms / 1000:.3f}'] if start_ms else []
        if trim_end_ms:
            duration_ms = max(1, int(trim_end_ms) - start_ms)
            args.extend(['-t', f'{duration_ms / 1000:.3f}'])
        return args


class ExternalMediaProjectPipeline:
    """Orchestrates a template project and delegates subtitle work to the proven legacy engine."""

    def __init__(self):
        self.storage = StorageService()
        self.templates = TemplateService()
        self.projects = ProjectService()
        self.assembly = VideoAssemblyService(storage=self.storage)
        self.lut = LUTService()
        self.intro_outro = IntroOutroService()
        self.music = MusicService()
        self.quality = MediaQualityService(self.assembly.runner)
        self._source_manifest = None

    def run(self, project_id):
        project = self._get_project(project_id)
        try:
            errors = self.projects.validate_uploads(project)
            if errors:
                raise ExternalMediaError(' '.join(errors))
            plugins = self.templates.enabled_plugins(project)
            plugin_codes = {plugin.code for plugin in plugins}
            wants_subtitles = bool({
                MediaTemplatePlugin.Code.SUBTITLE_PT,
                MediaTemplatePlugin.Code.TRANSLATION_EN,
            } & plugin_codes)
            self.projects.initialize_steps(project, plugins)
            self._step(project, 'upload', ProjectPipelineStep.Status.FINISHED, 'Uploads conferidos')
            self._update(project, ExternalMediaProject.Status.ASSEMBLING, 8, 'Organizando seus vídeos')
            with JobWorkspace(
                project.public_id,
                'project',
                estimated_bytes=self._project_workspace_estimate(project, needs_proxy=wants_subtitles),
            ) as workspace:
                workdir = workspace.path
                sources, lut_path, music_path = self._materialize(project, plugins, workdir)
                assembly_sources = sources
                assembly_preset = project.template_version.preset
                if wants_subtitles:
                    self._update(project, ExternalMediaProject.Status.ASSEMBLING, 10, 'Preparando seus vídeos')
                    assembly_sources = self._create_proxies(
                        sources, workdir,
                        on_progress=lambda completed, total: self._update(
                            project,
                            ExternalMediaProject.Status.ASSEMBLING,
                            10 + round((completed / max(1, total)) * 8),
                            'Preparando seus vídeos',
                        ),
                    )
                    assembly_preset = self.assembly.proxy_preset(project.template_version.preset)
                assembled = workdir / 'project_source.mp4'
                self._update(project, ExternalMediaProject.Status.ASSEMBLING, 19, 'Organizando seu vídeo')
                self._step(project, 'assembly', ProjectPipelineStep.Status.RUNNING)
                auto_reframe_plugin = next(
                    (plugin for plugin in plugins if plugin.code == MediaTemplatePlugin.Code.AUTO_TRACKING),
                    None,
                )
                if auto_reframe_plugin:
                    self._step(project, MediaTemplatePlugin.Code.AUTO_TRACKING, ProjectPipelineStep.Status.RUNNING)
                speech_edit_enabled = bool({
                    MediaTemplatePlugin.Code.SILENCE_REMOVAL,
                    MediaTemplatePlugin.Code.FILLER_REMOVAL,
                } & {plugin.code for plugin in plugins})
                used_auto_reframe = self.assembly.assemble(
                    assembly_sources, assembled, assembly_preset, workdir,
                    lut_path=lut_path,
                    lut_intensity=project.template_version.lut_intensity,
                    # Music is deferred until after VAD/transcription so it cannot mask silence.
                    music_path=None if speech_edit_enabled else music_path,
                    music_volume=project.template_version.music_volume,
                    auto_reframe_config=(
                        auto_reframe_plugin.configuration or {'priority': 'face'}
                    ) if auto_reframe_plugin else None,
                    progress_callback=lambda completed, total: self._update(
                        project,
                        ExternalMediaProject.Status.ASSEMBLING,
                        19 + round((completed / max(1, total)) * 3),
                        'Organizando seu vídeo',
                    ),
                )
                if auto_reframe_plugin:
                    self._step(
                        project,
                        MediaTemplatePlugin.Code.AUTO_TRACKING,
                        ProjectPipelineStep.Status.FINISHED,
                        '' if used_auto_reframe else (
                            'Nenhuma pessoa foi detectada; aplicado enquadramento central preenchendo o canvas.'
                        ),
                    )
                self._step(project, 'assembly', ProjectPipelineStep.Status.FINISHED)
                job = self._create_render_job(project, assembled)
                self._persist_canonical_state(project, job.pk, self._source_manifest)
                project.configuration = {
                    **(project.configuration or {}),
                    'proxy_pipeline': True,
                    'analysis_source_duration_ms': self.assembly._duration_ms(assembled),
                    'analysis_source_block_ranges': self.assembly.last_block_ranges,
                    'auto_reframe_plan_version': AUTO_REFRAME_PLAN_VERSION,
                    'auto_reframe_plans': self.assembly.last_reframe_plans if auto_reframe_plugin else [],
                    'protected_block_ranges': self.assembly.last_protected_ranges,
                    'block_ranges': self.assembly.last_block_ranges,
                }
                project.save(update_fields=['configuration', 'update_at'])
                self._persist_canonical_state(project, job.pk, self._source_manifest)
            self._update(project, ExternalMediaProject.Status.PROCESSING, 22, 'Preparando seu projeto')
            if wants_subtitles:
                media_pipeline = ExternalMediaPipeline()
                media_pipeline.prepare_subtitle_tracks(job.pk)
                for code in (MediaTemplatePlugin.Code.SUBTITLE_PT, MediaTemplatePlugin.Code.TRANSLATION_EN):
                    if code in plugin_codes:
                        self._step(project, code, ProjectPipelineStep.Status.FINISHED)
            elif project.template_version.audio_noise_cleanup_enabled:
                media_input = media_input_factory(job.original_video)
                with JobWorkspace(
                    project.public_id,
                    'noise-analysis',
                    estimated_bytes=media_input.workspace_estimate(),
                ) as workspace:
                    ExternalMediaPipeline()._apply_noise_analysis(
                        job,
                        media_input.get_ffmpeg_input(),
                        workdir=workspace.path,
                    )
            if not project.template_version.interactive_preview_enabled:
                self.render(project_id)
                return
            from .preview import ProjectProxyService, TimelineRevisionService

            self._update(project, ExternalMediaProject.Status.PROCESSING, 64, 'Preparando a revisão')
            ProjectProxyService.prepare(project)
            TimelineRevisionService.ensure_initial(project)
            job.status = ExternalMediaJob.Status.AWAITING_REVIEW
            job.progress = 66
            job.current_step = 'Aguardando sua revisão'
            job.save(update_fields=['status', 'progress', 'current_step', 'update_at'])
            project.status = ExternalMediaProject.Status.AWAITING_REVIEW
            project.progress = 66
            project.current_step = 'Revise as decisões antes de finalizar'
            project.finished_at = None
            project.save(update_fields=['status', 'progress', 'current_step', 'finished_at', 'update_at'])
        except (ExternalMediaError, AIServiceError) as exc:
            self._fail(project, str(exc))
            raise
        except Exception:
            logger.exception('Erro inesperado no projeto de mídia %s', project_id)
            self._fail(project, 'Ocorreu um erro inesperado durante o projeto.')
            raise

    def render(self, project_id):
        project = self._get_project(project_id)
        if not project.render_job_id:
            raise ExternalMediaError('O projeto ainda não possui conteúdo preparado.')
        if project.template_version.interactive_preview_enabled and not project.approved_timeline_revision_id:
            raise ExternalMediaError('A edição precisa ser aprovada antes da renderização final.')
        try:
            self._update(project, ExternalMediaProject.Status.PROCESSING, 68, 'Preparando a versão final')
            self._step(project, 'render', ProjectPipelineStep.Status.RUNNING)
            if (project.configuration or {}).get('proxy_pipeline'):
                with JobWorkspace(
                    project.public_id,
                    'project-final',
                    estimated_bytes=self._job_workspace_estimate(project.render_job),
                ) as workspace:
                    with timed_step('prepare_final_master', project=project.public_id):
                        self._prepare_final_master(project, workspace.path)
            subtitle_report = self.quality.validate_subtitles(
                ExternalMediaPipeline._ordered_output_tracks(project.render_job),
                (project.configuration or {}).get('protected_block_ranges'),
            )
            subtitle_report.require_ok()
            self._update(project, ExternalMediaProject.Status.PROCESSING, 91, 'Finalizando seu vídeo')
            if project.render_job.subtitle_tracks.exists():
                with timed_step('render_outputs', project=project.public_id):
                    ExternalMediaPipeline().render_outputs(project.render_job_id)
            else:
                with JobWorkspace(
                    project.public_id,
                    'project-output',
                    estimated_bytes=self._job_workspace_estimate(project.render_job),
                ) as workspace:
                    source = self.storage.ffmpeg_input(project.render_job.original_video)
                    self.quality.validate_media(source, deep_audio=True).require_ok()
                    self.storage.save_asset_from_field(
                        project.render_job, MediaAsset.Kind.VIDEO,
                        project.render_job.original_language,
                        project.render_job.original_video,
                        'video_final.mp4',
                    )
                project.render_job.status = ExternalMediaJob.Status.FINISHED
                project.render_job.progress = 100
                project.render_job.current_step = 'Processamento finalizado'
                project.render_job.finished_at = timezone.now()
                project.render_job.save(update_fields=[
                    'status', 'progress', 'current_step', 'finished_at', 'update_at',
                ])
            self._step(project, 'render', ProjectPipelineStep.Status.FINISHED)
            self._step(project, 'storage', ProjectPipelineStep.Status.FINISHED)
            project.status = ExternalMediaProject.Status.FINISHED
            project.progress = 100
            project.current_step = 'Processamento finalizado'
            project.finished_at = timezone.now()
            project.error_message = ''
            project.final_render_outdated = False
            project.save(update_fields=[
                'status', 'progress', 'current_step', 'finished_at', 'error_message',
                'final_render_outdated', 'update_at',
            ])
            if project.approved_timeline_revision_id:
                project.render_job.rendered_from_timeline_revision = project.approved_timeline_revision.revision
                project.render_job.save(update_fields=['rendered_from_timeline_revision', 'update_at'])
        except Exception as exc:
            self._fail(project, str(exc) if isinstance(exc, ExternalMediaError) else 'Erro durante a renderização.')
            raise

    def _job_workspace_estimate(self, job):
        if not job or not job.original_video:
            return 0
        return media_input_factory(job.original_video).workspace_estimate()

    def _project_workspace_estimate(self, project, *, needs_proxy=False):
        primary = next((
            item for item in project.block_media.all()
            if item.camera_role == item.CameraRole.PRIMARY and item.file
        ), None)
        if not primary:
            return 0
        return media_input_factory(primary.file).workspace_estimate(needs_proxy=needs_proxy)

    def _materialize(self, project, plugins, workdir):
        codes = {plugin.code for plugin in plugins}
        sources = []

        def materialize_small_asset(field_file, name):
            path = workdir / f'{name}{Path(field_file.name).suffix.lower()}'
            self.storage.copy_to_local(field_file, path, purpose=name)
            return path

        version = project.template_version
        self._source_manifest = SourceManifestBuilder.build(project)
        for item in self._source_manifest['sources']:
            if not (item.get('metadata') or {}).get('render_enabled', True):
                continue
            field = SourceManifestBuilder.resolve_field(project, item)
            metadata = item['metadata']
            sources.append(AssemblySource(
                self.storage.ffmpeg_input(field),
                label=item['id'],
                block_id=item.get('block_id'),
                block_key=item.get('block_key') or '',
                block_name=item.get('block_name') or '',
                skip_extra_processing=bool(metadata.get('skip_extra_processing')),
                remove_background_voice=bool(metadata.get('remove_background_voice')),
                trim_start_ms=int((item.get('trim') or {}).get('start_ms') or 0),
                trim_end_ms=(item.get('trim') or {}).get('end_ms'),
            ))
        if not sources:
            raise ExternalMediaError('Nenhum vídeo foi encontrado para montar o projeto.')
        lut_field = self.lut.selected_file(version, codes)
        music_field = self.music.selected_file(version, codes)
        lut = materialize_small_asset(lut_field, 'template_lut') if lut_field else None
        music = materialize_small_asset(music_field, 'template_music') if music_field else None
        available = {
            MediaTemplatePlugin.Code.INTRO: any(item['role'] == 'intro' for item in self._source_manifest['sources']),
            MediaTemplatePlugin.Code.OUTRO: any(item['role'] == 'outro' for item in self._source_manifest['sources']),
            MediaTemplatePlugin.Code.LUT: bool(lut),
            MediaTemplatePlugin.Code.MUSIC: bool(music),
        }
        for code, has_asset in available.items():
            if code in codes:
                self._step(
                    project,
                    code,
                    ProjectPipelineStep.Status.FINISHED if has_asset else ProjectPipelineStep.Status.SKIPPED,
                    '' if has_asset else 'O template não possui um arquivo configurado para esta etapa.',
                )
        return sources, lut, music

    @staticmethod
    def _persist_canonical_state(project, processing_job_id=None, source_manifest=None):
        """Dual-writes snapshots while the legacy configuration remains active."""
        state = ProjectProcessingState(project)
        manifest = source_manifest or SourceManifestBuilder.build(project, processing_job_id)
        manifest['processing_job_id'] = str(processing_job_id) if processing_job_id else manifest.get('processing_job_id')
        state.set_source_manifest(manifest)
        state.set_edit_decision_set(EditDecisionSetBuilder.build(project, manifest, processing_job_id))
        state.apply()
        project.save(update_fields=['configuration', 'update_at'])

    def _create_proxies(self, sources, workdir, on_progress=None):
        proxies = []
        try:
            for index, source in enumerate(sources):
                source_item = VideoAssemblyService._coerce_source(source)
                proxy = workdir / f'proxy_{index:03d}.mp4'
                self.assembly.create_proxy(
                    source_item.path,
                    proxy,
                    trim_start_ms=source_item.trim_start_ms,
                    trim_end_ms=source_item.trim_end_ms,
                )
                temporary_name = ''
                proxy_input = proxy
                if settings.USE_S3:
                    proxy_input, temporary_name = self.storage.stage_temporary(
                        proxy,
                        f'proxy-{uuid.uuid4().hex[:12]}-{index:03d}',
                    )
                proxies.append(AssemblySource(
                    proxy_input,
                    label=source_item.label,
                    block_id=source_item.block_id,
                    block_key=source_item.block_key,
                    block_name=source_item.block_name,
                    skip_extra_processing=source_item.skip_extra_processing,
                    remove_background_voice=source_item.remove_background_voice,
                    trim_start_ms=0,
                    trim_end_ms=None,
                    temporary_storage_name=temporary_name,
                ))
                if on_progress:
                    on_progress(index + 1, len(sources))
        except Exception:
            for proxy_source in proxies:
                self.storage.delete_temporary(proxy_source.temporary_storage_name)
            raise
        return proxies

    def _prepare_final_master(self, project, workdir):
        plugins = self.templates.enabled_plugins(project)
        sources, lut_path, music_path = self._materialize(project, plugins, workdir)
        assembled = workdir / 'project_master_original.mp4'
        auto_reframe_plugin = next(
            (plugin for plugin in plugins if plugin.code == MediaTemplatePlugin.Code.AUTO_TRACKING),
            None,
        )
        configuration = project.configuration or {}
        saved_reframe_plans = configuration.get('auto_reframe_plans') or []
        if configuration.get('auto_reframe_plan_version') != AUTO_REFRAME_PLAN_VERSION:
            # Reprocess old projects with the current framing strategy instead of
            # replaying crop plans generated by a previous implementation.
            saved_reframe_plans = []
        if project.approved_timeline_revision_id and saved_reframe_plans:
            saved_reframe_plans = self._approved_reframe_plans(project, saved_reframe_plans)
        analysis_sources = None
        if auto_reframe_plugin and not saved_reframe_plans:
            with timed_step('create_final_analysis_proxies', count=len(sources)):
                analysis_sources = self._create_proxies(
                    sources, workdir,
                    on_progress=lambda completed, total: self._update(
                        project,
                        ExternalMediaProject.Status.PROCESSING,
                        69 + round((completed / max(1, total)) * 2),
                        'Preparando seu vídeo',
                    ),
                )
        self._update(project, ExternalMediaProject.Status.PROCESSING, 72, 'Montando seu vídeo')
        with timed_step('assemble_final_master', clips=len(sources), reused_reframe_plans=bool(saved_reframe_plans)):
            self.assembly.assemble(
                sources,
                assembled,
                project.template_version.preset,
                workdir,
                lut_path=lut_path,
                lut_intensity=project.template_version.lut_intensity,
                # Music is never baked in here: mixing (with ducking, if enabled) always
                # happens afterwards in `_finalize_audio`, once the speech-edit cuts (if
                # any) have already reshaped the timeline.
                music_path=None,
                music_volume=project.template_version.music_volume,
                auto_reframe_config=(
                    auto_reframe_plugin.configuration or {'priority': 'face'}
                ) if auto_reframe_plugin else None,
                analysis_sources=analysis_sources,
                reframe_plans=saved_reframe_plans,
                progress_callback=lambda completed, total: self._update(
                    project,
                    ExternalMediaProject.Status.PROCESSING,
                    72 + round((completed / max(1, total)) * 8),
                    'Montando seu vídeo',
                ),
            )
        final_path = assembled
        approved = project.approved_timeline_revision
        background_plan_data = (project.configuration or {}).get('background_voice_plan')
        background_plan = SpeechEditPlan.from_dict(background_plan_data) if background_plan_data else None
        plan_data = (project.configuration or {}).get('speech_edit_plan')
        plan = SpeechEditPlan.from_dict(plan_data) if plan_data else None
        # Speech-edit timestamps are created after background-voice cuts. Convert
        # them back to the original master and apply both plans in one FFmpeg pass.
        # This removes an entire 4K re-encode without changing the resulting cuts.
        combined_cuts = []
        original_duration_ms = SpeechEditService(self.assembly.runner).duration_ms(assembled)
        timeline_report = self._validate_analysis_timeline(
            configuration,
            original_duration_ms,
            self.assembly.last_block_ranges,
        )
        if approved:
            combined_cuts.extend(
                SpeechCut(
                    int(item.get('source_in_ms') or 0),
                    int(item.get('source_out_ms') or 0),
                    (item.get('metadata') or {}).get('kind', 'silence'),
                    item.get('reason') or '',
                )
                for item in (approved.edit_decision_set.get('operations') or [])
                if item.get('type') == 'remove_segment' and item.get('enabled', True)
            )
        else:
            if background_plan and background_plan.cuts:
                combined_cuts.extend(background_plan.cuts)
            if plan and plan.cuts:
                if background_plan and background_plan.cuts:
                    combined_cuts.extend(
                        type(cut)(
                            background_plan.source_time(cut.start_ms),
                            background_plan.source_time(cut.end_ms),
                            cut.kind,
                            cut.label,
                        )
                        for cut in plan.cuts
                    )
                else:
                    combined_cuts.extend(plan.cuts)
        if combined_cuts:
            self._update(project, ExternalMediaProject.Status.PROCESSING, 82, 'Ajustando seu vídeo')
            edited = workdir / 'project_master_speech_edited.mp4'
            combined_plan = SpeechEditPlan.normalized(
                combined_cuts,
                original_duration_ms,
                max(
                    getattr(background_plan, 'crossfade_ms', 0) or 0,
                    getattr(plan, 'crossfade_ms', 0) or 0,
                    40,
                ),
            )
            cut_report = self.quality.validate_cuts(combined_plan.cuts, original_duration_ms)
            cut_report.require_ok()
            with timed_step('apply_combined_speech_edits_to_final_master', cuts=len(combined_plan.cuts)):
                with self.storage.staged_processing_input(
                    final_path, f'project-{project.public_id}-speech-edit',
                ) as processing_input:
                    SpeechEditService(self.assembly.runner).apply(processing_input, edited, combined_plan)
            final_path = edited
        else:
            combined_plan = SpeechEditPlan.normalized((), original_duration_ms)
        job = project.render_job
        version = project.template_version
        if (
            music_path
            or version.audio_mastering_enabled
            or version.dialogue_processing_enabled
            or version.audio_noise_cleanup_enabled
        ):
            self._update(project, ExternalMediaProject.Status.PROCESSING, 87, 'Ajustando o áudio')
            with timed_step('finalize_audio_final_master'):
                final_path = self._finalize_audio(project, job, workdir, final_path, music_path, combined_plan)
        expected_duration_ms = max(1, original_duration_ms - combined_plan.saved_ms)
        revision = project.approved_timeline_revision or project.current_timeline_revision
        overlays = list((revision.timeline if revision else {}).get('overlays') or [])
        if not revision:
            from .overlays import OverlayTimelineService

            overlay_clips = [
                {
                    'timeline_in_ms': combined_plan.remap_time(int(item.get('start_ms') or 0)),
                    'timeline_out_ms': combined_plan.remap_time(int(item.get('end_ms') or 0)),
                    'block': {'id': item.get('block_id')},
                }
                for item in self.assembly.last_block_ranges
                if item.get('block_id')
            ]
            overlays = OverlayTimelineService.compose(project, overlay_clips, expected_duration_ms)
        if overlays:
            from .overlays import OverlayRenderService

            self._update(project, ExternalMediaProject.Status.PROCESSING, 89, 'Aplicando elementos visuais')
            overlay_output = workdir / 'project_master_overlays.mp4'
            metadata = RenderService(self.assembly.runner).probe_video(final_path)
            canvas_width = version.preset.width or metadata.width or 1920
            canvas_height = version.preset.height or metadata.height or 1080
            with timed_step('render_timeline_overlays', count=len(overlays)):
                with self.storage.staged_processing_input(
                    final_path, f'project-{project.public_id}-overlays',
                ) as processing_input:
                    final_path = OverlayRenderService(self.assembly.runner).apply(
                        processing_input, overlay_output, overlays, canvas_width, canvas_height, workdir,
                    )
        quality_report = self.quality.validate_media(
            final_path,
            expected_duration_ms=expected_duration_ms,
            deep_audio=True,
        )
        quality_report.require_ok()
        project.configuration = {
            **(project.configuration or {}),
            'timeline_validation': timeline_report,
            'quality_report': quality_report.as_dict(),
        }
        project.save(update_fields=['configuration', 'update_at'])
        if not approved:
            self._persist_canonical_state(project, job.pk, self._source_manifest)
        replace_file_safely(
            job, 'original_video', final_path, 'project_source.mp4', ('update_at',),
        )
        self._update(project, ExternalMediaProject.Status.PROCESSING, 90, 'Finalizando seu vídeo')

    @staticmethod
    def _approved_reframe_plans(project, fallback_plans):
        """Applies user transform overrides to the plans already computed by analysis."""
        revision = project.approved_timeline_revision
        manifest = revision.source_manifest or {}
        operations = revision.edit_decision_set.get('operations') or []
        by_source = {
            item.get('source_id'): item
            for item in operations
            if item.get('type') == 'reframe' and item.get('enabled', True)
        }
        fallback_plans = list(fallback_plans or [])
        result = []
        for index, source in enumerate(manifest.get('sources') or []):
            raw_fallback = fallback_plans[index] if index < len(fallback_plans) else None
            fallback = dict(raw_fallback) if raw_fallback else None
            operation = by_source.get(source.get('id'))
            plan_data = dict((operation or {}).get('plan_reference') or fallback or {})
            manual = ((operation or {}).get('metadata') or {}).get('manual_transform')
            plan = dict(plan_data.get('plan') or {})
            if not manual or not plan:
                result.append(plan_data or fallback)
                continue
            analysis_width = int(plan_data.get('analysis_width') or plan.get('crop_width') or 1)
            analysis_height = int(plan_data.get('analysis_height') or plan.get('crop_height') or 1)
            old_width = int(plan.get('crop_width') or analysis_width)
            old_height = int(plan.get('crop_height') or analysis_height)
            scale = min(2.0, max(1.0, float(manual.get('scale') or 1)))
            new_width = max(2, round(old_width / scale / 2) * 2)
            new_height = max(2, round(old_height / scale / 2) * 2)
            offset_x = float(manual.get('x') or 0) * max(0, analysis_width - new_width) / 2
            offset_y = float(manual.get('y') or 0) * max(0, analysis_height - new_height) / 2
            keyframes = []
            for keyframe in plan.get('keyframes') or []:
                center_x = float(keyframe.get('x') or 0) + old_width / 2
                center_y = float(keyframe.get('y') or 0) + old_height / 2
                keyframes.append({
                    **keyframe,
                    'x': min(analysis_width - new_width, max(0, center_x - new_width / 2 + offset_x)),
                    'y': min(analysis_height - new_height, max(0, center_y - new_height / 2 + offset_y)),
                })
            plan_data['plan'] = {
                **plan, 'crop_width': new_width, 'crop_height': new_height, 'keyframes': keyframes,
            }
            result.append(plan_data)
        return result

    @staticmethod
    def _validate_analysis_timeline(configuration, original_duration_ms, final_ranges):
        analysis_duration_ms = int(configuration.get('analysis_source_duration_ms') or 0)
        analysis_ranges = configuration.get('analysis_source_block_ranges') or []
        if not analysis_duration_ms:
            raise ExternalMediaError('A duração da timeline de análise não foi registrada.')
        duration_drift_ms = abs(original_duration_ms - analysis_duration_ms)
        duration_tolerance_ms = max(1000, round(original_duration_ms * 0.01))
        if duration_drift_ms > duration_tolerance_ms:
            raise ExternalMediaError(
                'O proxy e o vídeo original perderam sincronismo '
                f'({duration_drift_ms / 1000:.2f}s de diferença).'
            )
        if len(analysis_ranges) != len(final_ranges):
            raise ExternalMediaError('A quantidade de blocos difere entre o proxy e o vídeo original.')
        largest_block_drift_ms = 0
        for analysis, final in zip(analysis_ranges, final_ranges):
            if analysis.get('block_key') != final.get('block_key'):
                raise ExternalMediaError('A ordem dos blocos mudou depois da análise do proxy.')
            analysis_ms = int(analysis.get('end_ms') or 0) - int(analysis.get('start_ms') or 0)
            final_ms = int(final.get('end_ms') or 0) - int(final.get('start_ms') or 0)
            block_drift_ms = abs(analysis_ms - final_ms)
            largest_block_drift_ms = max(largest_block_drift_ms, block_drift_ms)
            if block_drift_ms > max(500, round(max(analysis_ms, final_ms) * 0.01)):
                raise ExternalMediaError(
                    f'O bloco "{analysis.get("block_name") or analysis.get("block_key")}" '
                    'não está sincronizado entre o proxy e o original.'
                )
        return {
            'ok': True,
            'analysis_duration_ms': analysis_duration_ms,
            'original_duration_ms': original_duration_ms,
            'duration_drift_ms': duration_drift_ms,
            'largest_block_drift_ms': largest_block_drift_ms,
        }

    def _finalize_audio(self, project, job, workdir, video_path, music_path, plan):
        """Processes dialogue, mixes in music (with adaptive ducking, if enabled), then
        masters the final mix.

        The three stages stay independent: dialogue processing only ever sees the
        dialogue/original track (before any music is blended in), mixing only runs
        when there is music to blend in, and mastering only ever touches the resulting
        stereo bed, never looking at dialogue/music separately (see
        AudioMasteringService).
        """
        version = project.template_version
        final_path = video_path
        noise_metrics = None
        if version.audio_noise_cleanup_enabled:
            cleanup_settings = NoiseCleanupSettings.from_config(version.audio_noise_cleanup_config)
            decisions = self._noise_reduction_decisions(project)
            if decisions:
                cleaned_path = workdir / 'noise_cleaned.mp4'
                with self.storage.staged_processing_input(
                    final_path, f'project-{project.public_id}-noise-cleanup',
                ) as processing_input:
                    cleanup_result = AudioCleanupService(self.assembly.runner).apply(
                        processing_input, cleaned_path, decisions, settings_=cleanup_settings,
                    )
                final_path = cleanup_result.path
                noise_metrics = cleanup_result.metrics
            self._step(project, 'audio_noise_cleanup', ProjectPipelineStep.Status.FINISHED)
        dialogue_metrics = None
        if version.dialogue_processing_enabled:
            speech_blocks = self._speech_blocks_for_job(job)
            protected_ranges = self._protected_ranges_ms(project, plan)
            # The preceding noise pass may have staged and removed the original
            # local master. Measure the current output, not that stale path.
            duration_ms = SpeechEditService(self.assembly.runner).duration_ms(final_path)
            dialogue_settings = DialogueSettings.from_config(version.dialogue_processing_config)
            processed_path = workdir / 'dialogue_processed.mp4'
            with self.storage.staged_processing_input(
                final_path, f'project-{project.public_id}-dialogue',
            ) as processing_input:
                dialogue_result = DialogueProcessor(self.assembly.runner).process(
                    processing_input, processed_path,
                    duration_ms=duration_ms,
                    speech_blocks=speech_blocks,
                    protected_ranges=protected_ranges,
                    settings_=dialogue_settings,
                )
            final_path = dialogue_result.path
            dialogue_metrics = dialogue_result.metrics
            self._step(project, 'dialogue_processing', ProjectPipelineStep.Status.FINISHED)
        mix_metrics = None
        if music_path:
            mixing_enabled = version.audio_mixing_enabled
            speech_blocks = self._speech_blocks_for_job(job) if mixing_enabled else []
            protected_ranges = self._protected_ranges_ms(project, plan) if mixing_enabled else []
            duration_ms = SpeechEditService(self.assembly.runner).duration_ms(final_path)
            ducking_settings = DuckingSettings.from_config(version.audio_mixing_config)
            mixed_path = workdir / 'audio_mixed.mp4'
            with self.storage.staged_processing_input(
                final_path, f'project-{project.public_id}-audio-mix',
            ) as processing_input:
                mix_result = AudioMixingService(self.assembly.runner).mix(
                    processing_input, music_path, mixed_path,
                    music_volume=version.music_volume,
                    duration_ms=duration_ms,
                    speech_blocks=speech_blocks,
                    protected_ranges=protected_ranges,
                    settings_=ducking_settings,
                    ducking_enabled=mixing_enabled and version.audio_ducking_enabled,
                    spectral_enabled=mixing_enabled and version.audio_spectral_ducking_enabled,
                )
            final_path = mix_result.path
            mix_metrics = mix_result.metrics
            self._step(project, 'audio_mixing', ProjectPipelineStep.Status.FINISHED)
        master_metrics = None
        if version.audio_mastering_enabled and version.mastering_profile:
            mastered_path = workdir / 'audio_mastered.mp4'
            target = MasteringTarget.from_profile(version.mastering_profile)
            with self.storage.staged_processing_input(
                final_path, f'project-{project.public_id}-mastering',
            ) as processing_input:
                master_result = AudioMasteringService(self.assembly.runner).master(
                    processing_input, mastered_path, target,
                )
            final_path = master_result.path
            master_metrics = {
                **master_result.metrics,
                'validation': AudioValidationService().validate(final_path, version.mastering_profile),
            }
            self._step(project, 'audio_mastering', ProjectPipelineStep.Status.FINISHED)
        if dialogue_metrics or mix_metrics or master_metrics or noise_metrics:
            audio_metrics = {}
            if noise_metrics:
                audio_metrics['noise_cleanup'] = noise_metrics
            if dialogue_metrics:
                audio_metrics['dialogue'] = dialogue_metrics
            if mix_metrics:
                audio_metrics['mixing'] = mix_metrics
            if master_metrics:
                audio_metrics['mastering'] = master_metrics
            project.configuration = {**(project.configuration or {}), 'audio_metrics': audio_metrics}
            project.save(update_fields=['configuration', 'update_at'])
        return final_path

    @staticmethod
    def _noise_reduction_decisions(project):
        revision = project.approved_timeline_revision or project.current_timeline_revision
        operations = []
        if revision:
            operations = revision.edit_decision_set.get('operations') or []
        else:
            operations = ((project.configuration or {}).get('edit_decision_set') or {}).get('operations') or []
        decisions = []
        for item in operations:
            if item.get('type') != 'audio_noise_reduction':
                continue
            metadata = item.get('metadata') or {}
            start_ms = int(item.get('source_in_ms') or 0)
            end_ms = int(item.get('source_out_ms') or start_ms)
            if metadata.get('mode') == ReductionMode.GLOBAL:
                end_ms = max(end_ms, start_ms + 1)
            decisions.append(NoiseReductionDecision(
                start_ms,
                end_ms,
                metadata.get('mode', ReductionMode.LOCAL),
                metadata.get('strength', 'LIGHT'),
                metadata.get('source', 'AUTO_NOISE_ANALYSIS'),
                metadata.get('noise_type', 'UNKNOWN_NOISE'),
                bool(item.get('enabled', False)),
                metadata.get('recommended_action', 'REVIEW'),
                bool(metadata.get('speech_overlap')),
                float(item.get('confidence') or 0),
                metadata.get('label') or item.get('reason') or '',
                metadata.get('event_index'),
            ))
        if decisions:
            return decisions
        stored = (project.configuration or {}).get('noise_reduction_decisions') or []
        return [NoiseReductionDecision.from_dict(item) for item in stored if item.get('enabled')]

    @staticmethod
    def _speech_blocks_for_job(job, gap_threshold_ms=450):
        """Speech Blocks for ducking, derived from the already-grouped subtitle cues.

        Cue timestamps live in the same (post speech-edit) timeline as the final master
        video, so no extra remapping is needed here.
        """
        track = job.subtitle_tracks.filter(is_source=True).prefetch_related('cues').first()
        if not track:
            return []
        return group_speech_blocks(
            [(cue.start_ms, cue.end_ms) for cue in track.cues.all()],
            gap_threshold_ms=gap_threshold_ms,
        )

    @staticmethod
    def _protected_ranges_ms(project, plan):
        """"Manter bloco intacto" ranges, remapped onto the post speech-edit timeline."""
        ranges = (project.configuration or {}).get('protected_block_ranges') or []
        remap = plan.remap_time if plan else (lambda ms: ms)
        return [
            (remap(item.get('start_ms') or 0), remap(item.get('end_ms') or 0))
            for item in ranges
        ]

    def _create_render_job(self, project, assembled):
        version = project.template_version
        output_languages = list(version.output_languages)
        plugin_codes = {plugin.code for plugin in self.templates.enabled_plugins(project)}
        if MediaTemplatePlugin.Code.TRANSLATION_EN not in plugin_codes:
            output_languages = [version.original_language]
        # Cada reprocessamento recebe um job próprio. Isso preserva o histórico do
        # projeto e evita que os resultados de uma execução antiga sejam sobrescritos.
        job = ExternalMediaJob(created_by=project.created_by, processing_project=project)
        job.name = project.name
        job.original_language = version.original_language
        job.output_languages = output_languages
        job.translation_model = project.configuration.get('translation_model', 'gpt-4.1-mini')
        job.preset = version.preset
        job.subtitle_style = version.subtitle_style or SubtitleStyle.objects.filter(is_active=True).first()
        job.translated_subtitle_style = version.translated_subtitle_style or job.subtitle_style
        if not job.subtitle_style:
            raise ExternalMediaError('O template precisa de um estilo de legenda ativo.')
        job.status = ExternalMediaJob.Status.PENDING
        job.progress = 5
        with assembled.open('rb') as source:
            job.original_video.save('project_source.mp4', File(source), save=False)
        job.save()
        project.render_job = job
        project.save(update_fields=['render_job', 'update_at'])
        return job

    @staticmethod
    def _get_project(project_id):
        queryset = ExternalMediaProject.objects.select_related(
            'template_version__preset',
            'template_version__subtitle_style',
            'template_version__translated_subtitle_style',
            'created_by',
            'render_job',
            'current_timeline_revision',
            'approved_timeline_revision',
        ).prefetch_related('template_version__blocks', 'template_version__plugins', 'block_media')
        if isinstance(project_id, str):
            try:
                return queryset.get(public_id=uuid.UUID(project_id))
            except ValueError:
                pass
        return queryset.get(pk=project_id)

    @staticmethod
    def _update(project, status, progress, step):
        project.status = status
        project.progress = progress
        project.current_step = step
        project.error_message = ''
        if not project.started_at:
            project.started_at = timezone.now()
        project.save(update_fields=[
            'status', 'progress', 'current_step', 'error_message', 'started_at', 'update_at',
        ])

    @staticmethod
    def _step(project, code, status, message=''):
        now = timezone.now()
        values = {'status': status, 'message': message}
        if status == ProjectPipelineStep.Status.RUNNING:
            values.update(started_at=now, progress=20)
        elif status in {ProjectPipelineStep.Status.FINISHED, ProjectPipelineStep.Status.SKIPPED}:
            values.update(finished_at=now, progress=100)
        ProjectPipelineStep.objects.filter(project=project, code=code).update(**values)

    @staticmethod
    def _fail(project, message):
        project.status = ExternalMediaProject.Status.ERROR
        project.current_step = 'Processamento interrompido'
        project.error_message = message
        project.save(update_fields=['status', 'current_step', 'error_message', 'update_at'])
        ProjectPipelineStep.objects.filter(
            project=project, status=ProjectPipelineStep.Status.RUNNING,
        ).update(status=ProjectPipelineStep.Status.ERROR, message=message, finished_at=timezone.now())


class ExternalMediaPipeline:
    def __init__(self):
        self.storage = StorageService()
        self.audio = AudioExtractor()
        self.transcription = TranscriptionService()
        self.translation = TranslationService()
        self.subtitles = SubtitleService()
        self.renderer = RenderService(subtitle_service=self.subtitles)
        self.speech_analyzer = SpeechEditAnalyzer()
        self.speech_editor = SpeechEditService(self.audio.runner)
        self.quality = MediaQualityService(self.audio.runner)

    def prepare_subtitle_tracks(self, job_id):
        job = self._get_job(job_id)
        try:
            self._update(job, ExternalMediaJob.Status.EXTRACTING_AUDIO, 12, 'Preparando o áudio')
            media_input = media_input_factory(job.original_video)
            with JobWorkspace(
                job.public_id,
                'subtitle-analysis',
                estimated_bytes=media_input.workspace_estimate(needs_proxy=True),
            ) as workspace:
                workdir = workspace.path
                video_path = media_input.get_ffmpeg_input()
                chunks = self.audio.extract(video_path, workdir)
                self._update(job, ExternalMediaJob.Status.TRANSCRIBING, 30, 'Preparando as legendas')
                project = getattr(job, 'project', None)
                preserve_disfluencies = bool(project and any(
                    plugin.code == MediaTemplatePlugin.Code.FILLER_REMOVAL
                    for plugin in TemplateService.enabled_plugins(project)
                ))
                detailed = self.transcription.transcribe_detailed(
                    chunks,
                    self._transcription_language(job),
                    preserve_disfluencies=preserve_disfluencies,
                )
                self._update(job, ExternalMediaJob.Status.TRANSCRIBING, 36, 'Ajustando o áudio do vídeo')
                video_path, detailed = self._apply_background_voice_removal(job, video_path, workdir, detailed)
                detailed = self._apply_speech_edit(job, video_path, workdir, detailed)
                detailed = self.transcription.exclude_protected_ranges(
                    detailed,
                    (project.configuration or {}).get('protected_block_ranges') if project else [],
                )
                boundaries = self._block_boundaries_ms(job)
                segments = self.transcription.group_for_subtitles(detailed, boundaries)
                self._update(job, ExternalMediaJob.Status.GENERATING_SUBTITLES, 52, 'Sincronizando as legendas')
                source_track = self._save_source_track(job, segments)
                targets = [language for language in job.output_languages if language != job.original_language]
                for index, language in enumerate(targets):
                    progress = 58 + round((index / max(1, len(targets))) * 22)
                    self._update(job, ExternalMediaJob.Status.TRANSLATING, progress, f'Preparando legendas em {language.upper()}')
                    self.translation.translate_track(source_track, language, job.translation_model)
                noise_input = media_input_factory(job.original_video).get_ffmpeg_input()
                self._apply_noise_analysis(job, noise_input, source_track, workdir=workdir)
            self._update(job, ExternalMediaJob.Status.TRANSLATING, 82, 'Legendas prontas')
        except (ExternalMediaError, AIServiceError) as exc:
            self._fail(job, str(exc))
            raise
        except Exception:
            logger.exception('Erro inesperado no job de mídia %s', job_id)
            self._fail(job, 'Ocorreu um erro inesperado durante o processamento.')
            raise

    def prepare_subtitles(self, job_id):
        self.prepare_subtitle_tracks(job_id)
        self.render_outputs(job_id)

    def render_outputs(self, job_id):
        job = self._get_job(job_id)
        try:
            self._update(job, ExternalMediaJob.Status.RENDERING, 86, 'Finalizando seu vídeo')
            media_input = media_input_factory(job.original_video)
            with JobWorkspace(
                job.public_id,
                'subtitle-render',
                estimated_bytes=media_input.workspace_estimate(
                    output_count=max(1, len(job.output_languages)),
                ),
            ) as workspace:
                workdir = workspace.path
                video_path = media_input.get_ffmpeg_input()
                source_report = self.quality.validate_media(video_path)
                source_report.require_ok()
                source_duration_ms = source_report.metrics['duration_ms']
                tracks = self._ordered_output_tracks(job)
                if not tracks:
                    raise ExternalMediaError('Nenhuma legenda foi encontrada para renderização.')
                for track in tracks:
                    srt_path = workdir / f'legenda_{track.language}.srt'
                    vtt_path = workdir / f'legenda_{track.language}.vtt'
                    style = self._style_for_track(job, track.language)
                    self.subtitles.write_srt(track, srt_path, style)
                    self.subtitles.write_vtt(track, vtt_path, style)
                    self.storage.save_asset(job, MediaAsset.Kind.SRT, track.language, srt_path, srt_path.name)
                    self.storage.save_asset(job, MediaAsset.Kind.VTT, track.language, vtt_path, vtt_path.name)
                if self._should_render_dual_subtitle_video(job, tracks):
                    languages = '_'.join(track.language for track in tracks[:2])
                    video_output = workdir / f'video_{languages}.mp4'
                    with timed_step('render_dual_subtitle_video', languages=languages):
                        self.renderer.render_tracks(
                            video_path, tracks[:2], video_output, job.preset,
                            job.subtitle_style,
                            workdir,
                            job.original_language,
                            job.translated_subtitle_style or job.subtitle_style,
                        )
                    output_report = self.quality.validate_media(
                        video_output, expected_duration_ms=source_duration_ms,
                    )
                    output_report.require_ok()
                    self._remove_stale_video_assets(job, keep_language=job.original_language)
                    self.storage.save_asset(
                        job, MediaAsset.Kind.VIDEO, job.original_language, video_output, video_output.name,
                    )
                    video_output.unlink(missing_ok=True)
                    self._update(job, ExternalMediaJob.Status.RENDERING, 99, 'Vídeo bilingue concluído')
                else:
                    for index, track in enumerate(tracks):
                        video_output = workdir / f'video_{track.language}.mp4'
                        with timed_step('render_language_video', language=track.language):
                            self.renderer.render(
                                video_path,
                                track,
                                video_output,
                                job.preset,
                                self._style_for_track(job, track.language),
                                workdir,
                            )
                        output_report = self.quality.validate_media(
                            video_output, expected_duration_ms=source_duration_ms,
                        )
                        output_report.require_ok()
                        self.storage.save_asset(
                            job, MediaAsset.Kind.VIDEO, track.language, video_output, video_output.name,
                        )
                        video_output.unlink(missing_ok=True)
                        self._update(
                            job,
                            ExternalMediaJob.Status.RENDERING,
                            88 + round(((index + 1) / len(tracks)) * 11),
                            f'Vídeo {track.language.upper()} concluído',
                        )
            job.status = ExternalMediaJob.Status.FINISHED
            job.progress = 100
            job.current_step = 'Processamento finalizado'
            job.finished_at = timezone.now()
            job.error_message = ''
            job.save(update_fields=['status', 'progress', 'current_step', 'finished_at', 'error_message', 'update_at'])
        except ExternalMediaError as exc:
            self._fail(job, str(exc))
            raise
        except Exception:
            logger.exception('Erro inesperado na renderização do job %s', job_id)
            self._fail(job, 'Ocorreu um erro inesperado durante a renderização.')
            raise

    @staticmethod
    def _get_job(job_id):
        queryset = ExternalMediaJob.objects.select_related(
            'subtitle_style', 'translated_subtitle_style', 'preset', 'project__template_version',
        )
        if isinstance(job_id, str):
            try:
                public_id = uuid.UUID(job_id)
            except ValueError:
                return queryset.get(pk=job_id)
            return queryset.get(public_id=public_id)
        return queryset.get(pk=job_id)

    @staticmethod
    def _ordered_output_tracks(job):
        tracks = list(job.subtitle_tracks.filter(language__in=job.output_languages).prefetch_related('cues'))
        language_order = {language: index for index, language in enumerate(job.output_languages)}
        return sorted(tracks, key=lambda track: language_order.get(track.language, 999))

    @staticmethod
    def _style_for_track(job, language):
        if language == job.original_language:
            return job.subtitle_style
        return job.translated_subtitle_style or job.subtitle_style

    @staticmethod
    def _should_render_dual_subtitle_video(job, tracks):
        project = getattr(job, 'project', None)
        version = getattr(project, 'template_version', None)
        default_settings = getattr(version, 'default_settings', None) or {}
        return (
            default_settings.get('language_mode') == 'translated'
            and len(tracks) >= 2
            and any(track.language != job.original_language for track in tracks)
        )

    @staticmethod
    def _remove_stale_video_assets(job, keep_language):
        stale_assets = MediaAsset.objects.filter(job=job, kind=MediaAsset.Kind.VIDEO).exclude(language=keep_language)
        for asset in stale_assets:
            asset.file.delete(save=False)
            asset.delete(force_policy=HARD_DELETE)

    @staticmethod
    def _block_boundaries_ms(job):
        """Interior timeline points where one template block ends and the next begins.

        Used so subtitle cues are grouped independently per block and never mix the
        tail of one block's speech with the start of the next one's.
        """
        project = getattr(job, 'project', None)
        if not project:
            return []
        ranges = (project.configuration or {}).get('block_ranges') or []
        boundaries = {
            int(item.get('end_ms') or 0)
            for item in ranges
            if int(item.get('end_ms') or 0) > int(item.get('start_ms') or 0)
        }
        boundaries.discard(0)
        return sorted(boundaries)

    @staticmethod
    def _transcription_language(job):
        project = getattr(job, 'project', None)
        version = getattr(project, 'template_version', None)
        default_settings = getattr(version, 'default_settings', None) or {}
        if default_settings.get('language_mode') == 'bilingual_source':
            return None
        return job.original_language

    def _apply_background_voice_removal(self, job, video_path, workdir, detailed):
        """Remove off-camera interviewer turns only from opted-in blocks."""
        project = getattr(job, 'project', None)
        if not project:
            return video_path, detailed
        configuration = project.configuration or {}
        selected_ranges = [
            item for item in (configuration.get('block_ranges') or [])
            if item.get('remove_background_voice') and not item.get('skip_extra_processing')
        ]
        if not selected_ranges:
            return video_path, detailed
        service = BackgroundVoiceRemovalService(self.audio.runner)
        plan = service.analyze(video_path, detailed, selected_ranges, workspace=workdir)
        if not plan.cuts:
            configuration['background_voice_plan'] = plan.as_dict()
            project.configuration = configuration
            project.save(update_fields=['configuration', 'update_at'])
            ExternalMediaProjectPipeline._persist_canonical_state(project, project.render_job_id)
            return video_path, detailed
        edited_path = workdir / 'background_voice_removed.mp4'
        service.apply(video_path, edited_path, plan)
        replace_file_safely(
            job, 'original_video', edited_path, 'project_source.mp4', ('update_at',),
        )
        remap = SpeechEditPlan(plan.cuts, plan.duration_ms).remap_time
        for key in ('block_ranges', 'protected_block_ranges'):
            if configuration.get(key):
                configuration[key] = [
                    {
                        **item,
                        'start_ms': remap(item.get('start_ms') or 0),
                        'end_ms': remap(item.get('end_ms') or 0),
                    }
                    for item in configuration[key]
                ]
        configuration['background_voice_plan'] = plan.as_dict()
        project.configuration = configuration
        project.save(update_fields=['configuration', 'update_at'])
        ExternalMediaProjectPipeline._persist_canonical_state(project, project.render_job_id)
        return edited_path, SpeechEditPlan(plan.cuts, plan.duration_ms).remap_words(detailed)

    def _apply_noise_analysis(self, job, video_path, source_track=None, speech_blocks=None, workdir=None):
        project = getattr(job, 'project', None)
        if not project or not project.template_version.audio_noise_cleanup_enabled:
            return
        version = project.template_version
        settings_ = NoiseCleanupSettings.from_config(version.audio_noise_cleanup_config)
        if speech_blocks is None:
            speech_blocks = group_speech_blocks(
                [(cue.start_ms, cue.end_ms) for cue in source_track.cues.all()],
            ) if source_track else []
        ExternalMediaProjectPipeline._step(project, 'audio_noise_cleanup', ProjectPipelineStep.Status.RUNNING)
        plan = AudioNoiseAnalysisService(self.audio.runner).analyze(
            video_path,
            speech_blocks=speech_blocks,
            settings_=settings_,
            workspace=workdir,
        )
        decisions = NoiseReductionDecisionBuilder.build(plan, settings_)
        state = ProjectProcessingState(project)
        state.set_noise_analysis_plan(plan.as_dict())
        state.set_noise_reduction_decisions([item.as_dict() for item in decisions])
        manifest = state.get_source_manifest() or SourceManifestBuilder.build(project, job.pk)
        state.set_edit_decision_set(EditDecisionSetBuilder.build(project, manifest, job.pk))
        state.apply()
        project.save(update_fields=['configuration', 'update_at'])
        ExternalMediaProjectPipeline._step(project, 'audio_noise_cleanup', ProjectPipelineStep.Status.FINISHED)
        ExternalMediaProjectPipeline._persist_canonical_state(project, job.pk, manifest)

    def _apply_speech_edit(self, job, video_path, workdir, detailed):
        project = getattr(job, 'project', None)
        if not project:
            return detailed
        enabled_plugins = TemplateService.enabled_plugins(project)
        plugins = {
            plugin.code: plugin
            for plugin in enabled_plugins
            if plugin.code in {
                MediaTemplatePlugin.Code.SILENCE_REMOVAL,
                MediaTemplatePlugin.Code.FILLER_REMOVAL,
            }
        }
        if not plugins:
            return detailed
        remove_silence = MediaTemplatePlugin.Code.SILENCE_REMOVAL in plugins
        remove_fillers = MediaTemplatePlugin.Code.FILLER_REMOVAL in plugins
        for code in plugins:
            ExternalMediaProjectPipeline._step(project, code, ProjectPipelineStep.Status.RUNNING)
        configuration = {}
        for plugin in plugins.values():
            configuration.update(plugin.configuration or {})
        wav_path = workdir / 'speech_analysis.wav'
        self.speech_editor.extract_analysis_audio(video_path, wav_path)
        plan = self.speech_analyzer.analyze(
            detailed,
            wav_path,
            self.speech_editor.duration_ms(video_path),
            remove_silence=remove_silence,
            remove_fillers=remove_fillers,
            configuration=configuration,
            block_ranges=(project.configuration or {}).get('block_ranges'),
        )
        plan = plan.without_ranges((project.configuration or {}).get('protected_block_ranges'))
        proxy_pipeline = bool((project.configuration or {}).get('proxy_pipeline'))
        final_path = video_path
        if plan.cuts and not proxy_pipeline:
            edited_path = workdir / 'speech_edited.mp4'
            self.speech_editor.apply(video_path, edited_path, plan)
            final_path = edited_path
        enabled_codes = {plugin.code for plugin in enabled_plugins}
        music_field = MusicService.selected_file(project.template_version, enabled_codes)
        if music_field and not proxy_pipeline:
            music_path = workdir / f'speech_music{Path(music_field.name).suffix.lower()}'
            self.storage.copy_to_local(music_field, music_path, purpose='speech_music')
            mixed_path = workdir / 'speech_edited_with_music.mp4'
            final_path = AudioMixingService(self.audio.runner).mix(
                final_path, music_path, mixed_path,
                music_volume=project.template_version.music_volume,
                duration_ms=self.speech_editor.duration_ms(final_path),
                ducking_enabled=False,
            ).path
        if final_path != video_path:
            replace_file_safely(
                job, 'original_video', final_path, 'project_source.mp4', ('update_at',),
            )
        configuration = project.configuration or {}
        remapped_ranges = {}
        for key in ('block_ranges', 'protected_block_ranges'):
            ranges = configuration.get(key)
            if ranges:
                remapped_ranges[key] = [
                    {
                        **item,
                        'start_ms': plan.remap_time(item.get('start_ms') or 0),
                        'end_ms': plan.remap_time(item.get('end_ms') or 0),
                    }
                    for item in ranges
                ]
        project.configuration = {
            **configuration,
            'speech_edit_preview': plan.as_preview(),
            'speech_edit_plan': plan.as_dict(),
            **remapped_ranges,
        }
        project.save(update_fields=['configuration', 'update_at'])
        ExternalMediaProjectPipeline._persist_canonical_state(project, project.render_job_id)
        messages = {
            MediaTemplatePlugin.Code.SILENCE_REMOVAL: f'{plan.silence_count} silêncio(s) ajustado(s).',
            MediaTemplatePlugin.Code.FILLER_REMOVAL: f'{plan.filler_count} vício(s) de fala removido(s).',
        }
        for code in plugins:
            ExternalMediaProjectPipeline._step(
                project, code, ProjectPipelineStep.Status.FINISHED, messages[code],
            )
        return plan.remap_words(detailed)

    @staticmethod
    def _save_source_track(job, segments):
        with transaction.atomic():
            track, _ = SubtitleTrack.objects.get_or_create(
                job=job,
                language=job.original_language,
                defaults={'is_source': True},
            )
            if track.human_reviewed:
                return track
            track.is_source = True
            track.save(update_fields=['is_source', 'update_at'])
            hard_delete_track_cues(track)
            SubtitleCue.objects.bulk_create([
                SubtitleCue(
                    track=track,
                    cue_index=index,
                    start_ms=segment.start_ms,
                    end_ms=segment.end_ms,
                    text=segment.text,
                )
                for index, segment in enumerate(segments, start=1)
            ])
        return track

    @staticmethod
    def _update(job, status, progress, step):
        job.status = status
        job.progress = progress
        job.current_step = step
        job.error_message = ''
        if status != ExternalMediaJob.Status.FINISHED:
            job.finished_at = None
        if not job.started_at:
            job.started_at = timezone.now()
        job.save(update_fields=[
            'status', 'progress', 'current_step', 'error_message',
            'finished_at', 'started_at', 'update_at',
        ])
        project_id = getattr(job, 'processing_project_id', None)
        if not project_id:
            return
        if status == ExternalMediaJob.Status.RENDERING:
            project_progress = 91 + round((max(86, min(100, progress)) - 86) / 14 * 8)
        else:
            project_progress = 24 + round(max(0, min(82, progress)) / 82 * 43)
        ExternalMediaProject.objects.filter(pk=project_id).update(
            status=ExternalMediaProject.Status.PROCESSING,
            progress=min(99, project_progress),
            current_step=step,
            error_message='',
            update_at=timezone.now(),
        )

    @staticmethod
    def _fail(job, message):
        job.status = ExternalMediaJob.Status.ERROR
        job.current_step = 'Falha no processamento'
        job.error_message = message
        job.finished_at = timezone.now()
        job.save(update_fields=['status', 'current_step', 'error_message', 'finished_at', 'update_at'])
