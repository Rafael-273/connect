from __future__ import annotations

import json
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

from .auto_reframe import AutoReframePlan, AutoReframeService
from .exceptions import ExternalMediaError
from .speech_edit import SpeechEditAnalyzer, SpeechEditPlan, SpeechEditService

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class AudioChunk:
    path: Path
    offset_ms: int


@dataclass(frozen=True)
class VideoMetadata:
    color_space: str = ''
    color_transfer: str = ''
    color_primaries: str = ''

    @property
    def is_hdr(self):
        values = {self.color_space, self.color_transfer, self.color_primaries}
        return bool({'bt2020nc', 'bt2020', 'smpte2084', 'arib-std-b67'} & values)


class FFmpegRunner:
    def run(self, command: list[str]) -> str:
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=settings.EXTERNAL_MEDIA_FFMPEG_TIMEOUT,
            )
            return result.stdout
        except FileNotFoundError as exc:
            raise ExternalMediaError('FFmpeg/FFprobe não está instalado no worker.') from exc
        except subprocess.TimeoutExpired as exc:
            raise ExternalMediaError('O processamento de vídeo excedeu o tempo limite.') from exc
        except subprocess.CalledProcessError as exc:
            logger.error('FFmpeg falhou: %s', exc.stderr[-4000:])
            raise ExternalMediaError('O FFmpeg não conseguiu processar este vídeo.') from exc


class AudioExtractor:
    def __init__(self, runner=None):
        self.runner = runner or FFmpegRunner()

    def extract(self, video_path: Path, workdir: Path) -> list[AudioChunk]:
        output = self.runner.run([
            settings.FFPROBE_BINARY, '-v', 'error', '-show_entries',
            'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', str(video_path),
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
                settings.FFMPEG_BINARY, '-y', '-ss', str(offset), '-i', str(video_path),
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

    def __init__(self, ai_service=None):
        self.ai_service = ai_service or get_ai_service()

    def transcribe(self, chunks: list[AudioChunk], language: str | None) -> list[TranscriptionSegment]:
        detailed = self.transcribe_detailed(chunks, language)
        return self.group_for_subtitles(detailed)

    def transcribe_detailed(self, chunks: list[AudioChunk], language: str | None) -> list[TranscriptionSegment]:
        result = []
        for chunk in chunks:
            segments = self.ai_service.transcribe_segments(
                chunk.path,
                filename=chunk.path.name,
                language=language,
                prompt=self.PROMPT,
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
    def group_for_subtitles(cls, detailed):
        return cls._group_words(detailed) if detailed and all(item.granularity == 'word' for item in detailed) else detailed

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

    def __init__(self, ai_service=None):
        self.ai_service = ai_service or get_ai_service()

    def translate_track(self, source_track: SubtitleTrack, target_language: str, model: str):
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
        prompt = json.dumps({'glossary': glossary, 'cues': payload}, ensure_ascii=False)
        instructions = f"""Você é um tradutor e editor nativo especializado em sermões,
igrejas evangélicas e conteúdo cristão. Traduza de {self.LANGUAGE_NAMES[source_language]}
para {self.LANGUAGE_NAMES[target_language]} natural e idiomático, como um falante nativo
realmente diria. Nunca faça tradução palavra por palavra. Preserve integralmente o sentido,
o tom pastoral, nomes próprios, referências bíblicas e a ordem dos blocos. Priorize o
glossário fornecido. Não junte, divida, remova ou acrescente blocos. Para cada cue_id de
entrada, devolva exatamente um item. Responda somente JSON válido no formato
{{"cues":[{{"cue_id":1,"text":"..."}}]}}. Não devolva timestamps."""
        expected_ids = [cue.cue_index for cue in cues]
        for attempt in range(2):
            raw = self.ai_service.generate_text(
                prompt,
                instructions=instructions,
                model=model,
                max_output_tokens=max(1200, len(cues) * 100),
            )
            try:
                return self._parse_translated_cues(raw, expected_ids)
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
        header = self._ass_header(
            width,
            height,
            [self._ass_style_line('Default', style, style.margin_bottom)],
        )
        rows = []
        for cue in track.cues.all():
            text = self.wrap_text(cue.text, style.max_characters, style.max_lines)
            text = self._ass_escape(text).replace('\n', r'\N')
            rows.append(
                f'Dialogue: 0,{self._ass_time(cue.start_ms)},{self._ass_time(cue.end_ms)},'
                f'Default,,0,0,0,,{text}'
            )
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
        header = self._ass_header(
            width,
            height,
            [
                self._ass_style_line('Original', original_style, original_style.margin_bottom),
                self._ass_style_line('Translated', translated_style, translated_style.margin_bottom),
            ],
        )
        rows = []
        styled_tracks = (
            (ordered_tracks[0], 'Original', original_style),
            (ordered_tracks[1], 'Translated', translated_style),
        )
        for track, style_name, style in styled_tracks:
            for cue in track.cues.all():
                text = self._single_line_text(cue.text)
                text = r'{\q2}' + self._ass_escape(text)
                rows.append(
                    f'Dialogue: 0,{self._ass_time(cue.start_ms)},{self._ass_time(cue.end_ms)},'
                    f'{style_name},,0,0,0,,{text}'
                )
        path.write_text(header + '\n'.join(rows) + '\n', encoding='utf-8-sig')

    def _ass_header(self, width, height, styles):
        style_lines = '\n'.join(styles)
        return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style_lines}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    @staticmethod
    def _ass_style_line(name, style, margin_bottom):
        color = style.primary_color.lstrip('#').zfill(6)
        outline = style.outline_color.lstrip('#').zfill(6)
        primary_color = f'&H00{color[4:6]}{color[2:4]}{color[0:2]}'
        outline_color = f'&H00{outline[4:6]}{outline[2:4]}{outline[0:2]}'
        return (
            f'Style: {name},{style.font_name},{style.font_size},{primary_color},&H000000FF,'
            f'{outline_color},&H64000000,0,0,0,0,100,100,0,0,1,'
            f'{style.outline_width},{style.shadow},{style.alignment},40,40,{margin_bottom},1'
        )

    @staticmethod
    def _ass_escape(text):
        return text.replace('\\', r'\\').replace('{', r'\{').replace('}', r'\}')

    @staticmethod
    def _single_line_text(text):
        return re.sub(r'\s+', ' ', str(text or '').replace('\n', ' ')).strip()

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
        width = preset.width or 1920
        height = preset.height or 1080
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
        metadata = self.probe_video(video_path)
        filters = self.build_video_filters(preset, escaped_ass_path, metadata)
        command = [
            settings.FFMPEG_BINARY, '-y', '-i', str(video_path), '-vf', ','.join(filters),
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
                '-show_entries', 'stream=color_space,color_transfer,color_primaries',
                '-of', 'json', str(video_path),
            ])
            data = json.loads(output or '{}')
            stream = (data.get('streams') or [{}])[0]
            return VideoMetadata(
                color_space=stream.get('color_space') or '',
                color_transfer=stream.get('color_transfer') or '',
                color_primaries=stream.get('color_primaries') or '',
            )
        except (ExternalMediaError, json.JSONDecodeError, IndexError, TypeError):
            logger.warning('Nao foi possivel identificar o perfil de cor do video; usando SDR padrao.')
            return VideoMetadata()

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
        filters.extend([
            f"ass='{escaped_ass_path}'",
            'format=yuv420p',
        ])
        return filters


class StorageService:
    def copy_to_local(self, field_file, destination):
        with field_file.open('rb') as source, destination.open('wb') as target:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                target.write(chunk)

    def save_asset(self, job, kind, language, source_path, filename):
        asset = MediaAsset.objects.filter(job=job, kind=kind, language=language).first()
        if asset:
            asset.file.delete(save=False)
        else:
            asset = MediaAsset(job=job, kind=kind, language=language)
        with source_path.open('rb') as source:
            asset.file.save(filename, File(source), save=False)
        asset.file_size = source_path.stat().st_size
        asset.save()
        return asset


class TemplateService:
    """Reads the immutable template snapshot attached to a project."""

    @staticmethod
    def enabled_plugins(project):
        configured = project.configuration.get('plugins', {})
        result = []
        ignored_codes = {
            MediaTemplatePlugin.Code.SUBTITLE_PT,
            MediaTemplatePlugin.Code.TRANSLATION_EN,
            MediaTemplatePlugin.Code.LUT,
            MediaTemplatePlugin.Code.INTRO,
            MediaTemplatePlugin.Code.OUTRO,
            MediaTemplatePlugin.Code.MUSIC,
        }
        for plugin in project.template_version.plugins.filter(is_enabled=True).exclude(code__in=ignored_codes):
            enabled = configured.get(plugin.code, True) if plugin.user_can_override else True
            if enabled:
                result.append(plugin)
        version = project.template_version
        result.append(SimpleNamespace(code=MediaTemplatePlugin.Code.SUBTITLE_PT))
        if any(language != version.original_language for language in version.output_languages):
            result.append(SimpleNamespace(code=MediaTemplatePlugin.Code.TRANSLATION_EN))
        if version.lut_file:
            result.append(SimpleNamespace(code=MediaTemplatePlugin.Code.LUT))
        if version.background_music_id and version.background_music and version.background_music.audio_file:
            result.append(SimpleNamespace(code=MediaTemplatePlugin.Code.MUSIC))
        elif version.music_file:
            result.append(SimpleNamespace(code=MediaTemplatePlugin.Code.MUSIC))
        return result


class ProjectService:
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
        'render': 'Renderizando',
        'storage': 'Salvando arquivos',
    }

    @staticmethod
    def validate_uploads(project):
        counts = {}
        for item in project.block_media.all():
            counts[item.block_id] = counts.get(item.block_id, 0) + 1
        errors = []
        for block in project.template_version.blocks.all():
            count = counts.get(block.pk, 0)
            effective_count = count or (1 if block.default_video else 0)
            minimum = block.min_occurrences if block.is_required else 0
            if effective_count < minimum:
                errors.append(f'{block.name}: envie pelo menos {minimum} vídeo(s).')
            if count > block.max_occurrences:
                errors.append(f'{block.name}: máximo de {block.max_occurrences} vídeo(s).')
        return errors

    def initialize_steps(self, project, plugins):
        codes = ['upload', 'assembly'] + [plugin.code for plugin in plugins] + ['render', 'storage']
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
        return version.lut_file if MediaTemplatePlugin.Code.LUT in enabled_codes and version.lut_file else None


class IntroOutroService:
    @staticmethod
    def enabled_fields(version, enabled_codes):
        intro = version.intro_video if MediaTemplatePlugin.Code.INTRO in enabled_codes and version.intro_video else None
        outro = version.outro_video if MediaTemplatePlugin.Code.OUTRO in enabled_codes and version.outro_video else None
        return intro, outro


class MusicService:
    @staticmethod
    def selected_file(version, enabled_codes):
        if MediaTemplatePlugin.Code.MUSIC not in enabled_codes:
            return None
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

    def assemble(
        self, sources, output_path, preset, workdir, lut_path=None, music_path=None,
        music_volume=0.15, auto_reframe_config=None, analysis_sources=None,
        reframe_plans=None,
    ):
        self.last_reframe_plans = []
        if (
            len(sources) == 1
            and not lut_path
            and not music_path
            and not auto_reframe_config
            and not (preset.width and preset.height)
        ):
            shutil.copyfile(sources[0], output_path)
            return False
        width = preset.width or 1920
        height = preset.height or 1080
        normalized = []
        used_auto_reframe = False
        analysis_sources = analysis_sources or sources
        reframe_plans = reframe_plans or []
        for index, source in enumerate(sources):
            destination = workdir / f'normalized_{index:03d}.mp4'
            reframe_plan = self._normalize(
                source, destination, width, height, lut_path, auto_reframe_config,
                analysis_source=analysis_sources[index],
                reframe_plan_data=reframe_plans[index] if index < len(reframe_plans) else None,
            )
            used_auto_reframe = bool(reframe_plan) or used_auto_reframe
            self.last_reframe_plans.append(
                self._serialize_reframe_plan(reframe_plan, analysis_sources[index])
                if auto_reframe_config else None
            )
            normalized.append(destination)
        concat_file = workdir / 'concat.txt'
        concat_file.write_text(
            ''.join(f"file '{str(path).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'\n" for path in normalized),
            encoding='utf-8',
        )
        assembled = output_path if not music_path else workdir / 'assembled_without_music.mp4'
        self.runner.run([
            settings.FFMPEG_BINARY, '-y', '-f', 'concat', '-safe', '0', '-i', str(concat_file),
            '-c', 'copy', '-movflags', '+faststart', str(assembled),
        ])
        if music_path:
            self.runner.run([
                settings.FFMPEG_BINARY, '-y', '-i', str(assembled), '-stream_loop', '-1', '-i', str(music_path),
                '-filter_complex',
                f'[1:a]volume={float(music_volume):.3f}[music];[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[a]',
                '-map', '0:v:0', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
                '-movflags', '+faststart', str(output_path),
            ])
        return used_auto_reframe

    def create_proxy(self, source, destination):
        source_width, source_height = self._video_dimensions(source)
        proxy_width = min(source_width, settings.EXTERNAL_MEDIA_PROXY_WIDTH)
        proxy_height = self._even(proxy_width * source_height / source_width)
        self.runner.run([
            settings.FFMPEG_BINARY, '-y', '-i', str(source),
            '-vf', f'scale={proxy_width}:{proxy_height}:flags=fast_bilinear,fps=30,setsar=1',
            '-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'libx264',
            '-preset', settings.EXTERNAL_MEDIA_PROXY_PRESET,
            '-crf', str(settings.EXTERNAL_MEDIA_PROXY_CRF),
            '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '96k',
            '-ar', '48000', '-ac', '2', '-shortest', str(destination),
        ])
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
        self, source, destination, width, height, lut_path, auto_reframe_config=None,
        analysis_source=None, reframe_plan_data=None,
    ):
        has_audio = self._has_audio(source)
        metadata = RenderService(runner=self.runner).probe_video(source)
        command = [settings.FFMPEG_BINARY, '-y', '-i', str(source)]
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
        else:
            filters.extend([
                f'scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos',
                f'crop={width}:{height}:(iw-ow)/2:(ih-oh)/2',
            ])
        filters.extend(['fps=30', 'setsar=1'])
        if lut_path:
            escaped = str(lut_path).replace('\\', r'\\').replace(':', r'\:').replace("'", r"\'")
            filters.append(f"lut3d='{escaped}'")
        command.extend([
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
            '-show_entries', 'stream=index', '-of', 'csv=p=0', str(source),
        ])
        return bool(output.strip())

    def _video_dimensions(self, source):
        output = self.runner.run([
            settings.FFPROBE_BINARY, '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height', '-of', 'csv=s=x:p=0', str(source),
        ]).strip()
        try:
            width, height = output.split('x', 1)
            return max(2, int(width)), max(2, int(height))
        except ValueError as exc:
            raise ExternalMediaError('Não foi possível identificar a resolução do vídeo.') from exc

    @staticmethod
    def _even(value):
        return max(2, int(round(value)) // 2 * 2)


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
            self._update(project, ExternalMediaProject.Status.ASSEMBLING, 8, 'Montando os blocos do template')
            with TemporaryDirectory(prefix='connect-project-') as temp:
                workdir = Path(temp)
                sources, lut_path, music_path = self._materialize(project, plugins, workdir)
                assembly_sources = sources
                assembly_preset = project.template_version.preset
                if wants_subtitles:
                    self._update(project, ExternalMediaProject.Status.ASSEMBLING, 10, 'Criando proxy de análise')
                    assembly_sources = self._create_proxies(sources, workdir)
                    assembly_preset = self.assembly.proxy_preset(project.template_version.preset)
                assembled = workdir / 'project_source.mp4'
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
                    # Music is deferred until after VAD/transcription so it cannot mask silence.
                    music_path=None if speech_edit_enabled else music_path,
                    music_volume=project.template_version.music_volume,
                    auto_reframe_config=(
                        auto_reframe_plugin.configuration or {'priority': 'face'}
                    ) if auto_reframe_plugin else None,
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
                if wants_subtitles:
                    project.configuration = {
                        **(project.configuration or {}),
                        'proxy_pipeline': True,
                        'auto_reframe_plans': self.assembly.last_reframe_plans if auto_reframe_plugin else [],
                    }
                    project.save(update_fields=['configuration', 'update_at'])
            self._update(project, ExternalMediaProject.Status.PROCESSING, 22, 'Iniciando processamento do conteúdo')
            if wants_subtitles:
                ExternalMediaPipeline().prepare_subtitles(job.pk)
                project.status = ExternalMediaProject.Status.AWAITING_REVIEW
                project.progress = 82
                project.current_step = 'Legendas prontas para revisão'
                for code in (MediaTemplatePlugin.Code.SUBTITLE_PT, MediaTemplatePlugin.Code.TRANSLATION_EN):
                    if code in plugin_codes:
                        self._step(project, code, ProjectPipelineStep.Status.FINISHED)
            else:
                with TemporaryDirectory(prefix='connect-project-output-') as temp:
                    source = Path(temp) / 'video.mp4'
                    self.storage.copy_to_local(job.original_video, source)
                    self.storage.save_asset(
                        job, MediaAsset.Kind.VIDEO, job.original_language, source, 'video_final.mp4',
                    )
                job.status = ExternalMediaJob.Status.FINISHED
                job.progress = 100
                job.current_step = 'Processamento finalizado'
                job.finished_at = timezone.now()
                job.save(update_fields=['status', 'progress', 'current_step', 'finished_at', 'update_at'])
                project.status = ExternalMediaProject.Status.FINISHED
                project.progress = 100
                project.current_step = 'Processamento finalizado'
                project.finished_at = timezone.now()
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
        try:
            self._update(project, ExternalMediaProject.Status.PROCESSING, 85, 'Renderizando os vídeos finais')
            self._step(project, 'render', ProjectPipelineStep.Status.RUNNING)
            if (project.configuration or {}).get('proxy_pipeline'):
                with TemporaryDirectory(prefix='connect-project-final-') as temp:
                    with timed_step('prepare_final_master', project=project.public_id):
                        self._prepare_final_master(project, Path(temp))
            with timed_step('render_outputs', project=project.public_id):
                ExternalMediaPipeline().render_outputs(project.render_job_id)
            self._step(project, 'render', ProjectPipelineStep.Status.FINISHED)
            self._step(project, 'storage', ProjectPipelineStep.Status.FINISHED)
            project.status = ExternalMediaProject.Status.FINISHED
            project.progress = 100
            project.current_step = 'Processamento finalizado'
            project.finished_at = timezone.now()
            project.error_message = ''
            project.save(update_fields=[
                'status', 'progress', 'current_step', 'finished_at', 'error_message', 'update_at',
            ])
        except Exception as exc:
            self._fail(project, str(exc) if isinstance(exc, ExternalMediaError) else 'Erro durante a renderização.')
            raise

    def _materialize(self, project, plugins, workdir):
        codes = {plugin.code for plugin in plugins}
        sources = []

        def copy(field_file, name):
            path = workdir / f'{name}{Path(field_file.name).suffix.lower()}'
            self.storage.copy_to_local(field_file, path)
            return path

        version = project.template_version
        intro, outro = self.intro_outro.enabled_fields(version, codes)
        if intro:
            sources.append(copy(intro, 'intro'))
        media_by_block = {}
        for item in project.block_media.select_related('block'):
            media_by_block.setdefault(item.block_id, []).append(item)
        for block in version.blocks.all():
            items = media_by_block.get(block.pk, [])
            if items:
                for item in items:
                    sources.append(copy(item.file, f'block_{block.order}_{item.position}'))
            elif block.default_video:
                sources.append(copy(block.default_video, f'default_{block.order}'))
        if outro:
            sources.append(copy(outro, 'outro'))
        if not sources:
            raise ExternalMediaError('Nenhum vídeo foi encontrado para montar o projeto.')
        lut_field = self.lut.selected_file(version, codes)
        music_field = self.music.selected_file(version, codes)
        lut = copy(lut_field, 'template_lut') if lut_field else None
        music = copy(music_field, 'template_music') if music_field else None
        available = {
            MediaTemplatePlugin.Code.INTRO: bool(intro),
            MediaTemplatePlugin.Code.OUTRO: bool(outro),
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

    def _create_proxies(self, sources, workdir):
        proxies = []
        for index, source in enumerate(sources):
            proxy = workdir / f'proxy_{index:03d}.mp4'
            self.assembly.create_proxy(source, proxy)
            proxies.append(proxy)
        return proxies

    def _prepare_final_master(self, project, workdir):
        plugins = self.templates.enabled_plugins(project)
        codes = {plugin.code for plugin in plugins}
        sources, lut_path, music_path = self._materialize(project, plugins, workdir)
        assembled = workdir / 'project_master_original.mp4'
        speech_edit_enabled = bool({
            MediaTemplatePlugin.Code.SILENCE_REMOVAL,
            MediaTemplatePlugin.Code.FILLER_REMOVAL,
        } & codes)
        auto_reframe_plugin = next(
            (plugin for plugin in plugins if plugin.code == MediaTemplatePlugin.Code.AUTO_TRACKING),
            None,
        )
        saved_reframe_plans = (project.configuration or {}).get('auto_reframe_plans') or []
        analysis_sources = None
        if auto_reframe_plugin and not saved_reframe_plans:
            with timed_step('create_final_analysis_proxies', count=len(sources)):
                analysis_sources = self._create_proxies(sources, workdir)
        with timed_step('assemble_final_master', clips=len(sources), reused_reframe_plans=bool(saved_reframe_plans)):
            self.assembly.assemble(
                sources,
                assembled,
                project.template_version.preset,
                workdir,
                lut_path=lut_path,
                music_path=None if speech_edit_enabled else music_path,
                music_volume=project.template_version.music_volume,
                auto_reframe_config=(
                    auto_reframe_plugin.configuration or {'priority': 'face'}
                ) if auto_reframe_plugin else None,
                analysis_sources=analysis_sources,
                reframe_plans=saved_reframe_plans,
            )
        final_path = assembled
        plan_data = (project.configuration or {}).get('speech_edit_plan')
        if plan_data:
            plan = SpeechEditPlan.from_dict(plan_data)
            if plan.cuts:
                edited = workdir / 'project_master_speech_edited.mp4'
                with timed_step('apply_speech_edit_to_final_master', cuts=len(plan.cuts)):
                    SpeechEditService(self.assembly.runner).apply(final_path, edited, plan)
                final_path = edited
        if speech_edit_enabled and music_path:
            mixed = workdir / 'project_master_with_music.mp4'
            with timed_step('mix_music_final_master'):
                self.assembly.runner.run([
                    settings.FFMPEG_BINARY, '-y', '-i', str(final_path), '-stream_loop', '-1',
                    '-i', str(music_path), '-filter_complex',
                    f'[1:a]volume={float(project.template_version.music_volume):.3f}[music];'
                    '[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[a]',
                    '-map', '0:v:0', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
                    '-movflags', '+faststart', str(mixed),
                ])
            final_path = mixed
        job = project.render_job
        if job.original_video:
            job.original_video.delete(save=False)
        with final_path.open('rb') as source:
            job.original_video.save('project_source.mp4', File(source), save=False)
        job.save(update_fields=['original_video', 'update_at'])

    def _create_render_job(self, project, assembled):
        version = project.template_version
        output_languages = list(version.output_languages)
        plugin_codes = {plugin.code for plugin in self.templates.enabled_plugins(project)}
        if MediaTemplatePlugin.Code.TRANSLATION_EN not in plugin_codes:
            output_languages = [version.original_language]
        job = project.render_job or ExternalMediaJob(created_by=project.created_by)
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

    def prepare_subtitles(self, job_id):
        job = self._get_job(job_id)
        try:
            self._update(job, ExternalMediaJob.Status.EXTRACTING_AUDIO, 12, 'Extraindo e preparando o áudio')
            with TemporaryDirectory(prefix='connect-media-') as temp:
                workdir = Path(temp)
                video_path = workdir / f'original{Path(job.original_video.name).suffix.lower()}'
                self.storage.copy_to_local(job.original_video, video_path)
                chunks = self.audio.extract(video_path, workdir)
                self._update(job, ExternalMediaJob.Status.TRANSCRIBING, 30, 'Transcrevendo com Whisper')
                detailed = self.transcription.transcribe_detailed(chunks, self._transcription_language(job))
                detailed = self._apply_speech_edit(job, video_path, workdir, detailed)
                segments = self.transcription.group_for_subtitles(detailed)
                self._update(job, ExternalMediaJob.Status.GENERATING_SUBTITLES, 52, 'Criando blocos sincronizados')
                source_track = self._save_source_track(job, segments)
                targets = [language for language in job.output_languages if language != job.original_language]
                for index, language in enumerate(targets):
                    progress = 58 + round((index / max(1, len(targets))) * 22)
                    self._update(job, ExternalMediaJob.Status.TRANSLATING, progress, f'Traduzindo para {language.upper()}')
                    self.translation.translate_track(source_track, language, job.translation_model)
                self._update(job, ExternalMediaJob.Status.AWAITING_REVIEW, 82, 'Legendas prontas para revisão')
        except (ExternalMediaError, AIServiceError) as exc:
            self._fail(job, str(exc))
            raise
        except Exception as exc:
            logger.exception('Erro inesperado no job de mídia %s', job_id)
            self._fail(job, 'Ocorreu um erro inesperado durante o processamento.')
            raise

    def render_outputs(self, job_id):
        job = self._get_job(job_id)
        try:
            self._update(job, ExternalMediaJob.Status.RENDERING, 86, 'Gerando arquivos e renderizando vídeos')
            with TemporaryDirectory(prefix='connect-render-') as temp:
                workdir = Path(temp)
                video_path = workdir / f'original{Path(job.original_video.name).suffix.lower()}'
                self.storage.copy_to_local(job.original_video, video_path)
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
                    self._remove_stale_video_assets(job, keep_language=job.original_language)
                    self.storage.save_asset(
                        job, MediaAsset.Kind.VIDEO, job.original_language, video_output, video_output.name,
                    )
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
                        self.storage.save_asset(
                            job, MediaAsset.Kind.VIDEO, track.language, video_output, video_output.name,
                        )
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
    def _transcription_language(job):
        project = getattr(job, 'project', None)
        version = getattr(project, 'template_version', None)
        default_settings = getattr(version, 'default_settings', None) or {}
        if default_settings.get('language_mode') == 'bilingual_source':
            return None
        return job.original_language

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
        )
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
            self.storage.copy_to_local(music_field, music_path)
            mixed_path = workdir / 'speech_edited_with_music.mp4'
            self.audio.runner.run([
                settings.FFMPEG_BINARY, '-y', '-i', str(final_path), '-stream_loop', '-1',
                '-i', str(music_path), '-filter_complex',
                f'[1:a]volume={float(project.template_version.music_volume):.3f}[music];'
                '[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[a]',
                '-map', '0:v:0', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
                '-movflags', '+faststart', str(mixed_path),
            ])
            final_path = mixed_path
        if final_path != video_path:
            if job.original_video:
                job.original_video.delete(save=False)
            with final_path.open('rb') as source:
                job.original_video.save('project_source.mp4', File(source), save=False)
            job.save(update_fields=['original_video', 'update_at'])
        project.configuration = {
            **(project.configuration or {}),
            'speech_edit_preview': plan.as_preview(),
            'speech_edit_plan': plan.as_dict(),
        }
        project.save(update_fields=['configuration', 'update_at'])
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

    @staticmethod
    def _fail(job, message):
        job.status = ExternalMediaJob.Status.ERROR
        job.current_step = 'Falha no processamento'
        job.error_message = message
        job.finished_at = timezone.now()
        job.save(update_fields=['status', 'current_step', 'error_message', 'finished_at', 'update_at'])
