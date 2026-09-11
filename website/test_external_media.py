import json
import math
import re
import struct
import tempfile
import wave
from decimal import Decimal
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from safedelete.models import HARD_DELETE

from website.external_media.exceptions import ExternalMediaError
from website.external_media.audio_mastering import AudioMasteringService, MasteringTarget
from website.external_media.audio_mixing import (
    AudioMixingService,
    DuckingSettings, build_dynamic_ducking_envelope,
    SpeechBlock,
    build_ducking_envelope,
    build_spectral_windows,
    clip_blocks_against_protected_ranges,
    group_speech_blocks,
)
from website.external_media.audio_noise import (
    AudioNoiseAnalysisService,
    NoiseAnalysisPlan,
    NoiseCleanupSettings,
    NoiseEvent,
    NoiseReductionDecisionBuilder,
    NoiseType,
    RecommendedAction,
    ReductionMode,
    ReductionStrength,
)
from website.external_media.auto_reframe import AutoReframePlan, AutoReframeService, ReframeKeyframe, limit_keyframes_for_ffmpeg
from website.external_media.background_voice import BackgroundVoiceRemovalService, QuietUtterance
from website.external_media.quality_control import MediaQualityService
from website.external_media.speaker_diarization import SpeakerTurn
from website.external_media.dialogue_processing import (
    DialogueProcessor,
    DialogueSettings,
    build_leveling_envelope,
)
from website.external_media.speech_edit import SpeechCut, SpeechEditAnalyzer, SpeechEditPlan, SpeechEditService
from website.external_media.subtitle_reviews import SubtitleReviewService
from website.external_media.premiere_export import (
    ExportValidationService,
    PremiereExporter,
    PremierePackageService,
    PremiereTransformAdapter,
    json_compatible,
)
from website.external_media.timeline import InternalTimelineBuilder, KeyframeSimplifier, TimelineSource
from website.external_media.canonical import EditDecisionSetBuilder, SourceManifestBuilder, normalize_edit_ranges
from website.external_media.preview import PreviewCompositionService, TimelineRevisionService
from website.external_media.tasks import (
    _execution_is_current,
    _premiere_archive_filename,
    create_project_preview,
    render_reviewed_subtitles,
)
from website.external_media.services import (
    AudioChunk,
    AssemblySource,
    ExternalMediaPipeline,
    ExternalMediaProjectPipeline,
    FFmpegRunner,
    LUTService,
    RenderService,
    SubtitleService,
    TemplateService,
    TranscriptionSegment,
    TranscriptionService,
    TranslationService,
    VideoAssemblyService,
    VideoMetadata,
)
from website.forms.admin_external_media import AdminMediaTemplateBlockForm
from website.forms.external_media import ExternalMediaProjectForm
from website.views.external_media import protected_file_response
from website.models.external_media import (
    BackgroundMusicTrack,
    ColorLUT,
    ExternalMediaJob,
    ExternalMediaProject,
    ExternalMediaProjectExport,
    GlossaryTerm,
    MasteringProfile,
    MediaAsset,
    MediaTemplate,
    MediaTemplateBlock,
    MediaTemplatePlugin,
    MediaTemplateVersion,
    ProjectPipelineStep,
    ProjectBlockMedia,
    ProjectCustomBlock,
    PreviewSession,
    RenderPreset,
    SubtitleCue,
    SubtitleReviewSession,
    SubtitleRevision,
    SubtitleStyle,
    SubtitleSuggestion,
    SubtitleTrack,
    SubtitleVideoVersion,
    TimelineRevision,
)
from website.models import Member, Ministry, MinistryMembership, User
from website.models.external_media import external_media_project_upload_path


class ExternalMediaFixtureMixin:
    def setUp(self):
        self.user = User.objects.create_user('media@example.com', 'secret')
        self.member = Member.objects.create(user=self.user, name='Pessoa da Mídia')
        self.ministry, _ = Ministry.objects.get_or_create(
            code='midia_externa', defaults={'name': 'Mídia Externa'},
        )
        self.preset, _ = RenderPreset.objects.get_or_create(
            code='test-original', defaults={'name': 'Teste Original'},
        )
        self.style, _ = SubtitleStyle.objects.get_or_create(name='Teste')

    def tearDown(self):
        for item in ProjectBlockMedia.objects.all():
            if item.file:
                item.file.delete(save=False)
        for job in ExternalMediaJob.objects.all():
            if job.original_video:
                job.original_video.delete(save=False)
        super().tearDown()

    def make_job(self, status=ExternalMediaJob.Status.FINISHED):
        return ExternalMediaJob.objects.create(
            name='Culto de domingo',
            created_by=self.member,
            original_video=SimpleUploadedFile('culto.mp4', b'fake-video', content_type='video/mp4'),
            original_language='pt',
            output_languages=['pt', 'en'],
            preset=self.preset,
            subtitle_style=self.style,
            status=status,
        )


class ExternalMediaPermissionTests(ExternalMediaFixtureMixin, TestCase):
    def test_member_without_admin_access_does_not_loop_on_admin_dashboard(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('admin_dashboard'))

        self.assertRedirects(response, reverse('redirect_after_login'), fetch_redirect_response=False)

    def test_member_outside_ministry_is_redirected(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('external_media_dashboard'))
        self.assertRedirects(response, reverse('member_dashboard'))

    def test_active_ministry_member_can_open_module(self):
        MinistryMembership.objects.create(member=self.member, ministry=self.ministry)
        self.client.force_login(self.user)
        response = self.client.get(reverse('external_media_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Histórico de processamentos')

    def test_all_module_pages_render(self):
        MinistryMembership.objects.create(member=self.member, ministry=self.ministry)
        job = self.make_job()
        self.client.force_login(self.user)
        urls = [
            reverse('external_media_create'),
            reverse('external_media_glossary'),
            reverse('external_media_detail', args=[job.public_id]),
            reverse('external_media_editor', args=[job.public_id]),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_glossary_duplicate_returns_a_form_error(self):
        MinistryMembership.objects.create(member=self.member, ministry=self.ministry)
        GlossaryTerm.objects.create(
            source_language='pt', target_language='en',
            source_text='Termo duplicado de cobertura', translated_text='Coverage duplicate term',
        )
        self.client.force_login(self.user)
        response = self.client.post(reverse('external_media_glossary'), {
            'source_language': 'pt',
            'target_language': 'en',
            'source_text': 'Termo duplicado de cobertura',
            'translated_text': 'Another translation',
            'notes': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Este termo já existe para este par de idiomas.')
        self.assertEqual(GlossaryTerm.objects.filter(source_text='Termo duplicado de cobertura').count(), 1)

    def test_glossary_term_can_be_recreated_after_deletion(self):
        MinistryMembership.objects.create(member=self.member, ministry=self.ministry)
        term = GlossaryTerm.objects.create(
            source_language='pt', target_language='en',
            source_text='Termo removível', translated_text='First translation',
        )
        self.client.force_login(self.user)
        response = self.client.post(reverse('external_media_glossary_delete', args=[term.pk]))
        self.assertRedirects(response, reverse('external_media_glossary'))
        self.assertFalse(GlossaryTerm.all_objects.filter(pk=term.pk).exists())

        response = self.client.post(reverse('external_media_glossary'), {
            'source_language': 'pt',
            'target_language': 'en',
            'source_text': 'Termo removível',
            'translated_text': 'Replacement translation',
            'notes': '',
        })
        self.assertRedirects(response, reverse('external_media_glossary'))
        recreated = GlossaryTerm.objects.get(
            source_language='pt', target_language='en', source_text='Termo removível',
        )
        self.assertEqual(recreated.translated_text, 'Replacement translation')


class SubtitleIntegrityTests(ExternalMediaFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.job = self.make_job()
        self.track = SubtitleTrack.objects.create(job=self.job, language='pt', is_source=True)
        self.cues = [
            SubtitleCue.objects.create(
                track=self.track, cue_index=1, start_ms=1200, end_ms=4800,
                text='Olá, igreja!',
            ),
            SubtitleCue.objects.create(
                track=self.track, cue_index=2, start_ms=5000, end_ms=8200,
                text='Vamos adorar ao Senhor.',
            ),
        ]

    def test_translation_preserves_every_timestamp(self):
        ai = Mock()
        ai.generate_text.return_value = (
            '{"cues":[{"cue_id":1,"text":"Hello, church!"},'
            '{"cue_id":2,"text":"Let us worship the Lord."}]}'
        )
        target = TranslationService(ai_service=ai).translate_track(
            self.track, 'en', 'gpt-4.1-mini',
        )
        translated = list(target.cues.all())
        self.assertEqual(
            [(cue.start_ms, cue.end_ms) for cue in translated],
            [(cue.start_ms, cue.end_ms) for cue in self.cues],
        )
        self.assertEqual([cue.cue_index for cue in translated], [1, 2])

    def test_translation_never_overwrites_a_human_reviewed_track(self):
        translated = SubtitleTrack.objects.create(
            job=self.job, language='en', human_reviewed=True,
        )
        cue = SubtitleCue.objects.create(
            track=translated, cue_index=1, start_ms=1200, end_ms=4800,
            text='Human approved translation',
        )
        ai = Mock()

        result = TranslationService(ai_service=ai).translate_track(
            self.track, 'en', 'gpt-4.1-mini',
        )

        cue.refresh_from_db()
        result.refresh_from_db()
        self.assertEqual(cue.text, 'Human approved translation')
        self.assertEqual(result.translation_status, SubtitleTrack.TranslationStatus.SOURCE_CHANGED)
        ai.generate_text.assert_not_called()

    def test_translation_removes_ellipsis_and_continuation_hyphens(self):
        ai = Mock()
        ai.generate_text.return_value = (
            '{"cues":[{"cue_id":1,"text":"Hello, church... --"},'
            '{"cue_id":2,"text":"Let us worship the Lord…"}]}'
        )
        target = TranslationService(ai_service=ai).translate_track(
            self.track, 'en', 'gpt-4.1-mini',
        )
        self.assertEqual(
            list(target.cues.order_by('cue_index').values_list('text', flat=True)),
            ['Hello, church', 'Let us worship the Lord'],
        )

    def test_translation_rejects_missing_cue(self):
        ai = Mock()
        ai.generate_text.return_value = '{"cues":[{"cue_id":1,"text":"Hello!"}]}'
        with self.assertRaises(ExternalMediaError):
            TranslationService(ai_service=ai).translate_track(
                self.track, 'en', 'gpt-4.1-mini',
            )
        self.assertEqual(ai.generate_text.call_count, 5)

    def test_translation_falls_back_to_single_cues_when_batch_is_incomplete(self):
        ai = Mock()
        ai.generate_text.side_effect = [
            '{"cues":[{"cue_id":1,"text":"Hello, church!"}]}',
            '{"cues":[{"cue_id":1,"text":"Hello, church!"}]}',
            'Hello, church!',
            'Let us worship the Lord.',
        ]
        target = TranslationService(ai_service=ai).translate_track(
            self.track, 'en', 'gpt-4.1-mini',
        )
        translated = list(target.cues.order_by('cue_index'))
        self.assertEqual([cue.text for cue in translated], ['Hello, church!', 'Let us worship the Lord.'])
        self.assertEqual(ai.generate_text.call_count, 4)

    def test_translation_splits_large_invalid_batch_before_single_cues(self):
        ai = Mock()
        translations = {
            1: 'Hello, church!',
            2: 'Let us worship the Lord.',
            3: 'Peace be with you.',
            4: 'Amen.',
        }
        SubtitleCue.objects.create(
            track=self.track, cue_index=3, start_ms=9000, end_ms=11000, text='Paz do Senhor.',
        )
        SubtitleCue.objects.create(
            track=self.track, cue_index=4, start_ms=11200, end_ms=12600, text='Amém.',
        )

        def fake_translate(prompt, **_kwargs):
            payload = json.loads(prompt)
            cues = payload['cues']
            if len(cues) > 1:
                return '{"cues":[{"cue_id":1,"text":"Hello, church!"}]}'
            cue_id = cues[0]['cue_id']
            return translations[cue_id]

        ai.generate_text.side_effect = fake_translate

        target = TranslationService(ai_service=ai).translate_track(
            self.track, 'en', 'gpt-4.1-mini',
        )

        translated = list(target.cues.order_by('cue_index'))
        self.assertEqual(
            [cue.text for cue in translated],
            ['Hello, church!', 'Let us worship the Lord.', 'Peace be with you.', 'Amen.'],
        )
        self.assertEqual(ai.generate_text.call_count, 10)

    def test_translation_accepts_same_cues_out_of_order(self):
        ai = Mock()
        ai.generate_text.return_value = (
            '{"cues":['
            '{"cue_id":2,"text":"Let us worship the Lord."},'
            '{"cue_id":1,"text":"Hello, church!"}'
            ']}'
        )
        target = TranslationService(ai_service=ai).translate_track(
            self.track, 'en', 'gpt-4.1-mini',
        )
        translated = list(target.cues.order_by('cue_index'))
        self.assertEqual([cue.text for cue in translated], ['Hello, church!', 'Let us worship the Lord.'])

    def test_translation_accepts_json_wrapped_with_extra_text(self):
        ai = Mock()
        ai.generate_text.return_value = (
            'Segue o JSON:\n'
            '{"cues":['
            '{"cue_id":1,"translated_text":"Hello, church!"},'
            '{"cue_id":2,"translated_text":"Let us worship the Lord."}'
            ']}'
        )
        target = TranslationService(ai_service=ai).translate_track(
            self.track, 'en', 'gpt-4.1-mini',
        )
        translated = list(target.cues.order_by('cue_index'))
        self.assertEqual([cue.text for cue in translated], ['Hello, church!', 'Let us worship the Lord.'])

    def test_translation_restores_protected_brand_terms(self):
        ai = Mock()
        ai.generate_text.return_value = (
            '{"cues":[{"cue_id":1,"text":"Welcome to __TERM_1__."},'
            '{"cue_id":2,"text":"Join us at __TERM_2__ this Sunday."}]}'
        )
        self.cues[0].text = 'Bem-vindo à Igreja Filadélfia.'
        self.cues[0].save(update_fields=['text'])
        self.cues[1].text = 'Participe da Filadélfia neste domingo.'
        self.cues[1].save(update_fields=['text'])

        target = TranslationService(ai_service=ai).translate_track(
            self.track, 'en', 'gpt-4.1-mini',
        )

        prompt = json.loads(ai.generate_text.call_args.args[0])
        self.assertEqual(
            prompt['protected_terms'],
            [
                {'token': '__TERM_1__', 'text': 'Igreja Filadélfia'},
                {'token': '__TERM_2__', 'text': 'Filadélfia'},
            ],
        )
        self.assertEqual(prompt['cues'][0]['text'], 'Bem-vindo à __TERM_1__.')
        self.assertEqual(prompt['cues'][1]['text'], 'Participe da __TERM_2__ neste domingo.')

        translated = list(target.cues.order_by('cue_index'))
        self.assertEqual(
            [cue.text for cue in translated],
            ['Welcome to Igreja Filadélfia.', 'Join us at Filadélfia this Sunday.'],
        )

    def test_editor_never_accepts_timestamp_fields(self):
        MinistryMembership.objects.create(member=self.member, ministry=self.ministry)
        self.client.force_login(self.user)
        response = self.client.post(
            reverse('external_media_editor', args=[self.job.public_id]),
            {
                f'cue_{self.cues[0].pk}': 'Texto corrigido\ncom quebra',
                f'cue_{self.cues[1].pk}': self.cues[1].text,
                'start_ms': '999999',
                'end_ms': '1000000',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.cues[0].refresh_from_db()
        self.assertEqual(self.cues[0].text, 'Texto corrigido\ncom quebra')
        self.assertEqual((self.cues[0].start_ms, self.cues[0].end_ms), (1200, 4800))

    def test_srt_and_vtt_have_expected_timestamps(self):
        service = SubtitleService()
        with tempfile.TemporaryDirectory() as directory:
            srt = Path(directory) / 'legenda.srt'
            vtt = Path(directory) / 'legenda.vtt'
            service.write_srt(self.track, srt, self.style)
            service.write_vtt(self.track, vtt, self.style)
            self.assertIn('00:00:01,200 --> 00:00:04,800', srt.read_text())
            self.assertIn('00:00:01.200 --> 00:00:04.800', vtt.read_text())

    def test_dual_ass_uses_single_line_original_and_translation(self):
        translated = SubtitleTrack.objects.create(job=self.job, language='en')
        SubtitleCue.objects.create(
            track=translated, cue_index=1, start_ms=1200, end_ms=4800,
            text='Hello church this is a long translated',
        )
        service = SubtitleService()
        with tempfile.TemporaryDirectory() as directory:
            ass = Path(directory) / 'dual.ass'
            service.write_dual_ass([translated, self.track], ass, self.style, 1920, 1080, 'pt')
            content = ass.read_text(encoding='utf-8-sig')
        self.assertIn('Style: Original', content)
        self.assertIn('Style: Translated', content)
        self.assertIn(r'{\q2}', content)
        self.assertIn('Olá, igreja!', content)
        self.assertIn('Hello church this is a long translated', content)
        self.assertNotIn(r'\N', content)
        original_style = next(line for line in content.splitlines() if line.startswith('Style: Original,'))
        translated_style = next(line for line in content.splitlines() if line.startswith('Style: Translated,'))
        original_margin = int(original_style.split(',')[-2])
        translated_margin = int(translated_style.split(',')[-2])
        # Sem translated_style distinto, as duas usam o mesmo estilo → mesma margem.
        self.assertEqual(original_margin, translated_margin)

    def test_dual_ass_keeps_long_bilingual_pair_in_the_same_time_window(self):
        # Legendas bilíngues devem exibir o mesmo trecho nos dois idiomas, mesmo
        # quando uma tradução for maior que o limite de caracteres configurado.
        self.style.max_characters = 20
        self.style.background_enabled = False
        self.style.shadow = 0
        self.style.save()
        translated = SubtitleTrack.objects.create(job=self.job, language='en')
        SubtitleCue.objects.create(
            track=translated, cue_index=1, start_ms=1200, end_ms=4800,
            text='This translated line is way longer than the configured limit',
        )
        service = SubtitleService()
        with tempfile.TemporaryDirectory() as directory:
            ass = Path(directory) / 'dual_split.ass'
            service.write_dual_ass([translated, self.track], ass, self.style, 1920, 1080, 'pt')
            content = ass.read_text(encoding='utf-8-sig')
        translated_rows = [
            row for row in content.splitlines()
            if row.startswith('Dialogue:') and ',Translated,' in row
        ]
        original_rows = [
            row for row in content.splitlines()
            if row.startswith('Dialogue:') and ',Original,' in row
        ]
        self.assertEqual(len(original_rows), 2)
        self.assertEqual(len(translated_rows), 1)
        self.assertIn('This translated line is way longer than the configured limit', translated_rows[0])
        self.assertEqual(original_rows[0].split(',', 3)[1:3], translated_rows[0].split(',', 3)[1:3])
        self.assertNotIn('…', translated_rows[0])
        self.assertNotIn(r'\N', content)

    def test_ass_background_style_uses_configured_padding(self):
        self.style.background_enabled = True
        self.style.background_color = '#101820'
        self.style.background_opacity = 65
        self.style.background_padding_x = 22
        self.style.background_padding_y = 7
        self.style.background_height_percent = 100
        self.style.outline_width = 3
        self.style.shadow = 0
        self.style.save()
        service = SubtitleService()
        with tempfile.TemporaryDirectory() as directory:
            ass = Path(directory) / 'single.ass'
            service.write_ass(self.track, ass, self.style, 1920, 1080)
            content = ass.read_text(encoding='utf-8-sig')
        style_line = next(line for line in content.splitlines() if line.startswith('Style: Default,'))
        fields = style_line.split(',')
        # BorderStyle=4 desenha uma única caixa para o evento inteiro (em vez de uma por linha),
        # evitando que o alpha semi-transparente "some" e escureça o fundo em legendas com
        # múltiplas linhas (libass issue #821). A cor/opacidade da caixa vem de overrides
        # \4c/\4a no texto do Dialogue, não mais do campo BackColour do estilo.
        self.assertEqual(fields[15], '4')
        self.assertEqual(fields[16], '3')
        self.assertIn('&H59', fields[6])
        dialogue_line = next(line for line in content.splitlines() if line.startswith('Dialogue:'))
        self.assertIn(r'\4c&H201810&', dialogue_line)
        self.assertIn(r'\4a&H59&', dialogue_line)
        self.assertIn(r'\xshad22', dialogue_line)
        self.assertIn(r'\yshad7', dialogue_line)
        # \yshad estende a caixa para cima/baixo do texto sem mover o MarginV (libass não
        # desloca o texto por causa do \shad de BorderStyle=4), então o MarginV emitido
        # precisa ser inflado pelo padding vertical para a BORDA da caixa (não o texto)
        # terminar no margin_bottom configurado, batendo com o preview (CSS `bottom`).
        self.assertEqual(int(fields[-2]), self.style.margin_bottom + 7)

    def test_ass_shadow_uses_angle_distance_on_shadow_layer(self):
        self.style.background_enabled = False
        self.style.shadow = 10
        self.style.shadow_angle = 0  # direita → xshad=10, yshad=0
        self.style.shadow_size = 0
        self.style.shadow_blur = 0
        self.style.shadow_opacity = 50
        self.style.save()
        service = SubtitleService()
        with tempfile.TemporaryDirectory() as directory:
            ass = Path(directory) / 'shadow.ass'
            service.write_ass(self.track, ass, self.style, 1920, 1080)
            content = ass.read_text(encoding='utf-8-sig')
        self.assertIn('Style: DefaultShadow', content)
        dialogues = [line for line in content.splitlines() if line.startswith('Dialogue:')]
        self.assertIn('DefaultShadow', dialogues[0])
        self.assertIn(r'\1a&H80&', dialogues[0])
        self.assertIn(r'\1c&H000000&', dialogues[0])
        self.assertIn(r'\xshad10', dialogues[0])
        self.assertIn(r'\yshad0', dialogues[0])

    def test_ass_shadow_size_stacks_undeformed_copies_along_the_angle(self):
        self.style.background_enabled = False
        self.style.shadow = 6
        self.style.shadow_angle = 90  # baixo
        self.style.shadow_size = 4
        self.style.shadow_blur = 0
        self.style.shadow_opacity = 40
        self.style.save()
        service = SubtitleService()
        with tempfile.TemporaryDirectory() as directory:
            ass = Path(directory) / 'sized_shadow.ass'
            service.write_ass(self.track, ass, self.style, 1920, 1080)
            content = ass.read_text(encoding='utf-8-sig')
        shadow_dialogues = [
            line for line in content.splitlines()
            if line.startswith('Dialogue:') and 'DefaultShadow' in line
        ]
        # "Tamanho" nunca usa \bord/\fscx (deformavam acentos e deslocavam a sombra); em vez
        # disso, empilha cópias nítidas (mesmo glifo) cada vez mais longe no mesmo ângulo —
        # a mesma técnica de múltiplos `text-shadow` do preview.
        self.assertGreater(len(shadow_dialogues), 1)
        for dialogue in shadow_dialogues:
            self.assertNotIn(r'\fscx', dialogue)
            self.assertNotIn(r'\fscy', dialogue)
            self.assertIn(r'\bord0', dialogue)
        self.assertIn(r'\xshad0', shadow_dialogues[0])
        self.assertIn(r'\yshad6', shadow_dialogues[0])
        last_yshad = float(re.search(r'\\yshad([\d.]+)', shadow_dialogues[-1]).group(1))
        self.assertGreater(last_yshad, 6)

    def test_ass_shadow_blur_only_when_requested(self):
        self.style.background_enabled = False
        self.style.shadow = 6
        self.style.shadow_angle = 90
        self.style.shadow_size = 0
        self.style.shadow_blur = 3
        self.style.shadow_opacity = 40
        self.style.save()
        service = SubtitleService()
        with tempfile.TemporaryDirectory() as directory:
            ass = Path(directory) / 'blur_shadow.ass'
            service.write_ass(self.track, ass, self.style, 1920, 1080)
            content = ass.read_text(encoding='utf-8-sig')
        shadow_dialogues = [
            line for line in content.splitlines()
            if line.startswith('Dialogue:') and 'DefaultShadow' in line
        ]
        # Sem tamanho, é uma única cópia nítida com blur por cue (2 cues no fixture) —
        # sem \fscx e sem \bord.
        self.assertEqual(len(shadow_dialogues), 2)
        self.assertIn(r'\blur3', shadow_dialogues[0])
        self.assertNotIn(r'\fscx', shadow_dialogues[0])
        self.assertNotIn(r'\bord4', shadow_dialogues[0])

    def test_ass_background_with_shadow_emits_shadow_layer(self):
        self.style.background_enabled = True
        self.style.background_padding_x = 0
        self.style.background_padding_y = 0
        self.style.shadow = 4
        self.style.shadow_angle = 45
        self.style.shadow_opacity = 40
        self.style.save()
        service = SubtitleService()
        with tempfile.TemporaryDirectory() as directory:
            ass = Path(directory) / 'bg_shadow.ass'
            service.write_ass(self.track, ass, self.style, 1920, 1080)
            content = ass.read_text(encoding='utf-8-sig')
        self.assertIn('Style: DefaultShadow', content)
        dialogues = [line for line in content.splitlines() if line.startswith('Dialogue:')]
        self.assertGreaterEqual(len(dialogues), 2)
        shadow_dialogue = next(row for row in dialogues if 'DefaultShadow' in row)
        self.assertIn(r'\1a&H99&', shadow_dialogue)
        self.assertIn(r'\1c&H000000&', shadow_dialogue)
        # 45° com distância 4 → ~2.83 em x e y
        self.assertIn(r'\xshad2.83', shadow_dialogue)
        self.assertIn(r'\yshad2.83', shadow_dialogue)

    def test_ass_background_draws_behind_shadow_which_draws_behind_text(self):
        # Se o fundo fosse desenhado por cima da sombra (ou na mesma camada do texto),
        # a caixa "engoliria" a sombra visualmente — regressão relatada pelo usuário.
        self.style.background_enabled = True
        self.style.background_padding_x = 6
        self.style.background_padding_y = 6
        self.style.background_height_percent = 100
        self.style.shadow = 4
        self.style.shadow_angle = 45
        self.style.shadow_opacity = 90
        self.style.save()
        service = SubtitleService()
        with tempfile.TemporaryDirectory() as directory:
            ass = Path(directory) / 'bg_behind_shadow.ass'
            service.write_ass(self.track, ass, self.style, 1920, 1080)
            content = ass.read_text(encoding='utf-8-sig')
        # Altura 100% normalmente não precisaria de camada própria, mas com sombra ativa
        # precisa: senão a caixa embutida no evento do texto (camada mais alta) cobriria
        # a sombra por completo.
        self.assertIn('Style: DefaultBG', content)
        dialogues = [line for line in content.splitlines() if line.startswith('Dialogue:')]
        layer_by_role = {}
        for row in dialogues:
            layer = int(row.split(',')[0].split(':')[1])
            role = row.split(',')[3]
            layer_by_role.setdefault(role, layer)
        self.assertLess(layer_by_role['DefaultBG'], layer_by_role['DefaultShadow'])
        self.assertLess(layer_by_role['DefaultShadow'], layer_by_role['Default'])

    def test_ass_background_height_percent_uses_scaled_bg_layer(self):
        self.style.background_enabled = True
        self.style.background_padding_x = 0
        self.style.background_padding_y = 0
        self.style.background_height_percent = 70
        self.style.shadow = 0
        self.style.save()
        service = SubtitleService()
        with tempfile.TemporaryDirectory() as directory:
            ass = Path(directory) / 'bg_height.ass'
            service.write_ass(self.track, ass, self.style, 1920, 1080)
            content = ass.read_text(encoding='utf-8-sig')
        self.assertIn('Style: DefaultBG', content)
        dialogues = [line for line in content.splitlines() if line.startswith('Dialogue:')]
        self.assertGreaterEqual(len(dialogues), 2)
        self.assertIn('DefaultBG', dialogues[0])
        self.assertIn(r'\fscy70', dialogues[0])
        self.assertIn(r'\1a&HFF&', dialogues[0])

    def test_ass_background_zero_padding_hugs_text(self):
        self.style.background_enabled = True
        self.style.background_padding_x = 0
        self.style.background_padding_y = 0
        self.style.background_height_percent = 100
        self.style.shadow = 0
        self.style.save()
        service = SubtitleService()
        with tempfile.TemporaryDirectory() as directory:
            ass = Path(directory) / 'tight.ass'
            service.write_ass(self.track, ass, self.style, 1920, 1080)
            content = ass.read_text(encoding='utf-8-sig')
        dialogue_line = next(line for line in content.splitlines() if line.startswith('Dialogue:'))
        self.assertIn(r'\xshad0', dialogue_line)
        self.assertIn(r'\yshad0', dialogue_line)
        style_line = next(line for line in content.splitlines() if line.startswith('Style: Default,'))
        self.assertEqual(int(style_line.split(',')[-2]), self.style.margin_bottom)

    def test_ass_margin_v_ignores_padding_without_background(self):
        self.style.background_enabled = False
        self.style.save()
        service = SubtitleService()
        with tempfile.TemporaryDirectory() as directory:
            ass = Path(directory) / 'single.ass'
            service.write_ass(self.track, ass, self.style, 1920, 1080)
            content = ass.read_text(encoding='utf-8-sig')
        style_line = next(line for line in content.splitlines() if line.startswith('Style: Default'))
        fields = style_line.split(',')
        self.assertEqual(int(fields[-2]), self.style.margin_bottom)

    def test_dual_ass_compacts_excessive_margin_between_subtitles(self):
        translated = SubtitleTrack.objects.create(job=self.job, language='en')
        SubtitleCue.objects.create(
            track=translated, cue_index=1, start_ms=1200, end_ms=4800,
            text='Hello church this is a long translated subtitle',
        )
        translated_style, _ = SubtitleStyle.objects.get_or_create(name='TesteTraduzida')
        translated_style.background_enabled = True
        translated_style.background_padding_y = 10
        translated_style.background_height_percent = 100
        translated_style.margin_bottom = 40
        translated_style.shadow = 0
        translated_style.save()
        self.style.background_enabled = False
        self.style.margin_bottom = 120
        self.style.shadow = 0
        self.style.save()
        service = SubtitleService()
        with tempfile.TemporaryDirectory() as directory:
            ass = Path(directory) / 'dual.ass'
            service.write_dual_ass(
                [translated, self.track], ass, self.style, 1920, 1080, 'pt',
                translated_style=translated_style,
            )
            content = ass.read_text(encoding='utf-8-sig')
        original_style_line = next(line for line in content.splitlines() if line.startswith('Style: Original,'))
        translated_style_line = next(line for line in content.splitlines() if line.startswith('Style: Translated,'))
        original_margin_v = int(original_style_line.split(',')[-2])
        translated_margin_v = int(translated_style_line.split(',')[-2])
        logical_original, logical_translated = SubtitleService._dual_style_margins(self.style, translated_style)
        # A margem inferior (tradução) é preservada; a outra é compactada logo acima.
        self.assertEqual(logical_original, 119)
        self.assertEqual(logical_translated, 40)
        self.assertEqual(original_margin_v, SubtitleService._ass_margin_v(self.style, 119))
        self.assertEqual(translated_margin_v, SubtitleService._ass_margin_v(translated_style, 40))

    def test_dual_ass_dialogues_use_pos_for_pixel_perfect_margins(self):
        translated = SubtitleTrack.objects.create(job=self.job, language='en')
        SubtitleCue.objects.create(
            track=translated, cue_index=1, start_ms=1200, end_ms=4800,
            text='Hello church',
        )
        translated_style, _ = SubtitleStyle.objects.get_or_create(name='TesteTradPos')
        translated_style.background_enabled = True
        translated_style.background_padding_y = 2
        translated_style.margin_bottom = 164
        translated_style.shadow = 0
        translated_style.save()
        self.style.background_enabled = True
        self.style.background_padding_y = 0
        self.style.margin_bottom = 90
        self.style.shadow = 0
        self.style.save()
        service = SubtitleService()
        with tempfile.TemporaryDirectory() as directory:
            ass = Path(directory) / 'dual_pos.ass'
            service.write_dual_ass(
                [translated, self.track], ass, self.style, 3840, 1200, 'pt',
                translated_style=translated_style,
            )
            content = ass.read_text(encoding='utf-8-sig')
        original_dialogue = next(
            line for line in content.splitlines()
            if line.startswith('Dialogue:') and ',Original,' in line and 'OriginalBG' not in line and 'OriginalShadow' not in line
        )
        translated_dialogue = next(
            line for line in content.splitlines()
            if line.startswith('Dialogue:') and ',Translated,' in line and 'TranslatedBG' not in line and 'TranslatedShadow' not in line
        )
        self.assertIn(r'{\an2\pos(1920,1110)}', original_dialogue)
        self.assertIn(r'{\an2\pos(1920,1049)}', translated_dialogue)

    def test_dual_ass_uses_separate_layers_to_avoid_collision_push(self):
        translated = SubtitleTrack.objects.create(job=self.job, language='en')
        SubtitleCue.objects.create(
            track=translated, cue_index=1, start_ms=1200, end_ms=4800,
            text='Hello church',
        )
        translated_style, _ = SubtitleStyle.objects.get_or_create(name='TesteTradLayer')
        translated_style.shadow = 0
        translated_style.save()
        self.style.shadow = 0
        self.style.save()
        service = SubtitleService()
        with tempfile.TemporaryDirectory() as directory:
            ass = Path(directory) / 'dual_layers.ass'
            service.write_dual_ass(
                [translated, self.track], ass, self.style, 1920, 1080, 'pt',
                translated_style=translated_style,
            )
            content = ass.read_text(encoding='utf-8-sig')
        original_layers = {
            int(line.split(',')[0].split(':')[1])
            for line in content.splitlines()
            if line.startswith('Dialogue:') and ',Original' in line
        }
        translated_layers = {
            int(line.split(',')[0].split(':')[1])
            for line in content.splitlines()
            if line.startswith('Dialogue:') and ',Translated' in line
        }
        self.assertTrue(all(layer < 10 for layer in original_layers))
        self.assertTrue(all(layer >= 10 for layer in translated_layers))

    def test_render_filters_use_subtitle_fonts_dir_when_available(self):
        preset = SimpleNamespace(width=3840, height=1200)
        filters = RenderService.build_video_filters(
            preset, '/tmp/subtitles.ass', VideoMetadata(),
        )
        ass_filter = next(item for item in filters if item.startswith('ass='))
        fonts_dir = RenderService.subtitle_fonts_dir()
        if fonts_dir.is_dir() and any(fonts_dir.glob('*.[ot]tf')):
            self.assertIn('fontsdir=', ass_filter)
        else:
            self.assertNotIn('fontsdir=', ass_filter)

    def test_ass_header_enables_scaled_border_and_shadow(self):
        service = SubtitleService()
        with tempfile.TemporaryDirectory() as directory:
            ass = Path(directory) / 'scaled.ass'
            service.write_ass(self.track, ass, self.style, 1920, 1080)
            content = ass.read_text(encoding='utf-8-sig')
        self.assertIn('ScaledBorderAndShadow: yes', content)

    def test_render_play_res_prefers_preset_output_dimensions(self):
        preset = SimpleNamespace(width=1080, height=1920)
        metadata = VideoMetadata(width=3840, height=2160)
        width, height = RenderService.ass_play_res(preset, metadata)
        self.assertEqual((width, height), (1080, 1920))

    def test_ass_invalid_alignment_falls_back_to_bottom_center(self):
        self.style.alignment = 0
        service = SubtitleService()
        with tempfile.TemporaryDirectory() as directory:
            ass = Path(directory) / 'single.ass'
            service.write_ass(self.track, ass, self.style, 1920, 1080)
            content = ass.read_text(encoding='utf-8-sig')
        style_line = next(line for line in content.splitlines() if line.startswith('Style: Default'))
        fields = style_line.split(',')
        self.assertEqual(fields[18], '2')

    def test_ass_font_weight_maps_to_bold_flag(self):
        self.style.font_weight = 400
        self.style.save()
        service = SubtitleService()
        with tempfile.TemporaryDirectory() as directory:
            ass = Path(directory) / 'regular.ass'
            service.write_ass(self.track, ass, self.style, 1920, 1080)
            regular_fields = next(
                line for line in ass.read_text(encoding='utf-8-sig').splitlines()
                if line.startswith('Style: Default')
            ).split(',')
        self.assertEqual(regular_fields[7], '0')

        self.style.font_weight = 700
        self.style.save()
        with tempfile.TemporaryDirectory() as directory:
            ass = Path(directory) / 'bold.ass'
            service.write_ass(self.track, ass, self.style, 1920, 1080)
            bold_fields = next(
                line for line in ass.read_text(encoding='utf-8-sig').splitlines()
                if line.startswith('Style: Default')
            ).split(',')
        self.assertEqual(bold_fields[7], '-1')

    def test_ass_reflects_every_configured_style_parameter_together(self):
        # Golden test: combines font weight, alignment, colors, a scaled-down background,
        # and an angled/sized/blurred shadow at once, guaranteeing the render never drifts
        # from whatever the admin saved, even when several fields interact.
        self.style.font_weight = SubtitleStyle.FontWeight.SEMIBOLD
        self.style.alignment = SubtitleStyle.Alignment.TOP_RIGHT
        self.style.primary_color = '#112233'
        self.style.background_enabled = True
        self.style.background_color = '#445566'
        self.style.background_opacity = 55
        self.style.background_padding_x = 5
        self.style.background_padding_y = 3
        self.style.background_height_percent = 60
        self.style.outline_color = '#000000'
        self.style.outline_width = 2
        self.style.shadow = 6
        self.style.shadow_angle = 90
        self.style.shadow_size = 2
        self.style.shadow_blur = 1
        self.style.shadow_opacity = 40
        self.style.margin_bottom = 77
        self.style.save()

        service = SubtitleService()
        with tempfile.TemporaryDirectory() as directory:
            ass = Path(directory) / 'golden.ass'
            service.write_ass(self.track, ass, self.style, 1920, 1080)
            content = ass.read_text(encoding='utf-8-sig')

        main_style = next(line for line in content.splitlines() if line.startswith('Style: Default,'))
        shadow_style = next(line for line in content.splitlines() if line.startswith('Style: DefaultShadow,'))
        bg_style = next(line for line in content.splitlines() if line.startswith('Style: DefaultBG,'))
        main_fields = main_style.split(',')

        # Font weight -> Bold flag; alignment maps 1:1 to ASS numpad alignment.
        self.assertEqual(main_fields[7], '-1')
        self.assertEqual(main_fields[18], '9')
        # Primary color is BGR-swapped and fully opaque (text opacity isn't user-exposed).
        self.assertEqual(main_fields[3], '&H00332211')
        self.assertEqual(main_fields[5], '&H00000000')
        # Background scaled below 100% forces a dedicated BG layer, so MarginV is padded
        # on every layer by the saved background_padding_y on top of margin_bottom.
        for style_line in (main_style, shadow_style, bg_style):
            self.assertTrue(style_line.endswith(',80,1'), style_line)

        rows = [line for line in content.splitlines() if line.startswith('Dialogue:')]
        # 2 cues x (2 stacked shadow copies for shadow_size=2 + background + main) layers.
        self.assertEqual(len(rows), 8)
        shadow_rows = [row for row in rows if ',DefaultShadow,' in row]
        bg_row = next(row for row in rows if ',DefaultBG,' in row)
        main_row = next(row for row in rows if row.count(',Default,'))

        # Shadow: 40% opacity, angle 90° (straight down) at distance 6. "Tamanho" stacks
        # undeformed copies (never \bord/\fscx, which deform accents and shift position)
        # progressively further along the same angle; blur is only added because
        # shadow_blur > 0.
        self.assertEqual(len(shadow_rows), 4)  # 2 cues x 2 stacked copies
        base_shadow_row = shadow_rows[0]
        self.assertIn(r'\1a&H99&', base_shadow_row)
        self.assertIn(r'\1c&H000000&', base_shadow_row)
        self.assertNotIn(r'\fscx', base_shadow_row)
        self.assertIn(r'\bord0', base_shadow_row)
        self.assertIn(r'\blur1', base_shadow_row)
        self.assertIn(r'\xshad0', base_shadow_row)
        self.assertIn(r'\yshad6', base_shadow_row)

        # Background: 55% opacity, BGR-swapped box color, configured padding as extra
        # width/height, and fscy60 to hug the text at 60% of the default box height.
        self.assertIn(r'\4c&H665544&', bg_row)
        self.assertIn(r'\4a&H73&', bg_row)
        self.assertIn(r'\fscy60', bg_row)
        self.assertIn(r'\xshad5', bg_row)
        self.assertIn(r'\yshad3', bg_row)

        self.assertIn('Olá, igreja!', main_row)

    def test_pipeline_can_resolve_job_by_public_id(self):
        found = ExternalMediaPipeline._get_job(str(self.job.public_id))
        self.assertEqual(found.pk, self.job.pk)

    def test_translated_project_renders_one_video_with_two_subtitle_tracks(self):
        template = MediaTemplate.objects.create(
            name='Template traduzido', slug='template-traduzido', category=MediaTemplate.Category.TRANSLATION,
        )
        version = MediaTemplateVersion.objects.create(
            template=template, version=1, status=MediaTemplateVersion.Status.PUBLISHED,
            preset=self.preset, subtitle_style=self.style, original_language='pt',
            output_languages=['pt', 'en'],
            default_settings={'language_mode': 'translated', 'translated_language': 'en'},
        )
        ExternalMediaProject.objects.create(
            name='Projeto traduzido', template_version=version, created_by=self.member,
            render_job=self.job,
        )
        translated = SubtitleTrack.objects.create(job=self.job, language='en')
        SubtitleCue.objects.create(
            track=translated, cue_index=1, start_ms=1200, end_ms=4800, text='Hello, church!',
        )
        pipeline = ExternalMediaPipeline()
        pipeline.storage = Mock()
        pipeline.renderer = Mock()
        pipeline.quality = Mock()
        pipeline.quality.validate_media.return_value = Mock(metrics={'duration_ms': 5000})
        pipeline.renderer.render_tracks.side_effect = lambda *args: Path(args[2]).write_bytes(b'video')
        pipeline.renderer.render.side_effect = AssertionError('single-language render should not be used')

        pipeline.render_outputs(self.job.pk)

        pipeline.renderer.render_tracks.assert_called_once()
        rendered_tracks = pipeline.renderer.render_tracks.call_args.args[1]
        self.assertEqual([track.language for track in rendered_tracks], ['pt', 'en'])
        video_assets = [
            call.args
            for call in pipeline.storage.save_asset.call_args_list
            if call.args[1] == MediaAsset.Kind.VIDEO
        ]
        self.assertEqual(len(video_assets), 1)
        self.assertEqual(video_assets[0][2], 'pt')

    def test_save_source_track_replaces_old_cues_without_unique_conflict(self):
        service = ExternalMediaPipeline()
        segments = [
            TranscriptionSegment(start_ms=0, end_ms=1000, text='Primeiro bloco'),
            TranscriptionSegment(start_ms=1100, end_ms=2200, text='Segundo bloco'),
        ]
        service._save_source_track(self.job, segments)
        track = self.job.subtitle_tracks.get(language='pt')
        track.cues.all().delete()
        self.assertEqual(SubtitleCue.all_objects.filter(track=track).count(), 2)
        updated = [
            TranscriptionSegment(start_ms=0, end_ms=900, text='Bloco atualizado'),
        ]
        track = service._save_source_track(self.job, updated)
        cues = list(track.cues.order_by('cue_index'))
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].cue_index, 1)
        self.assertEqual(cues[0].text, 'Bloco atualizado')
        self.assertEqual(SubtitleCue.all_objects.filter(track=track).count(), 1)


class ExternalMediaProjectTests(ExternalMediaFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        MinistryMembership.objects.create(member=self.member, ministry=self.ministry)
        self.template = MediaTemplate.objects.create(
            name='Template de teste', slug='template-de-teste', category=MediaTemplate.Category.TRANSLATION,
        )
        self.version = MediaTemplateVersion.objects.create(
            template=self.template, version=1, status=MediaTemplateVersion.Status.PUBLISHED,
            preset=self.preset, subtitle_style=self.style, output_languages=['pt', 'en'],
        )
        self.block = MediaTemplateBlock.objects.create(
            version=self.version, key='video', name='Vídeo', order=1,
            is_required=True, min_occurrences=1, max_occurrences=1,
        )
        MediaTemplatePlugin.objects.create(
            version=self.version, code=MediaTemplatePlugin.Code.SUBTITLE_PT, order=1,
        )
        MediaTemplatePlugin.objects.create(
            version=self.version, code=MediaTemplatePlugin.Code.TRANSLATION_EN, order=2,
        )
        self.client.force_login(self.user)

    def make_project(self):
        return ExternalMediaProject.objects.create(
            name='Projeto de agosto', template_version=self.version, created_by=self.member,
        )

    def test_source_manifest_resolves_custom_blocks_and_configured_order(self):
        project = self.make_project()
        custom = ProjectCustomBlock.objects.create(project=project, name='Extra', position=2)
        template_media = ProjectBlockMedia.objects.create(
            project=project, block=self.block, position=1, original_filename='template.mov',
            file=SimpleUploadedFile('template.mov', b'template'), trim_start_ms=120, trim_end_ms=900,
        )
        custom_media = ProjectBlockMedia.objects.create(
            project=project, custom_block=custom, position=1, original_filename='custom.mov',
            file=SimpleUploadedFile('custom.mov', b'custom'),
        )
        project.configuration = {'block_order': [f'c-{custom.pk}', f't-{self.block.pk}']}
        project.save(update_fields=['configuration', 'update_at'])

        manifest = SourceManifestBuilder.build(project)

        self.assertEqual([item['id'] for item in manifest['sources']], [
            f'project-media-{custom_media.pk}', f'project-media-{template_media.pk}',
        ])
        self.assertEqual(manifest['sources'][1]['trim'], {'start_ms': 120, 'end_ms': 900})
        self.assertEqual(manifest['sources'][0]['block_type'], 'custom')
        self.assertEqual(
            [source.asset_id for source in InternalTimelineBuilder()._sources(project)],
            [f'project-media-{custom_media.pk}', f'project-media-{template_media.pk}'],
        )

    def test_source_manifest_preserves_extra_cameras_without_rendering_them_sequentially(self):
        project = self.make_project()
        primary = ProjectBlockMedia.objects.create(
            project=project, block=self.block, position=1, camera_order=1,
            camera_role=ProjectBlockMedia.CameraRole.PRIMARY,
            camera_label='Câmera aberta', original_filename='wide.mov',
            file=SimpleUploadedFile('wide.mov', b'wide'),
        )
        extra = ProjectBlockMedia.objects.create(
            project=project, block=self.block, position=1, camera_order=2,
            camera_role=ProjectBlockMedia.CameraRole.SECONDARY,
            camera_label='Convidada', camera_hint=ProjectBlockMedia.CameraHint.SPEAKER_B,
            original_filename='guest.mov', file=SimpleUploadedFile('guest.mov', b'guest'),
        )

        manifest = SourceManifestBuilder.build(project)

        self.assertEqual(len(manifest['sources']), 2)
        self.assertEqual(manifest['multicam_groups'][0]['reference_source_id'], f'project-media-{primary.pk}')
        self.assertEqual(manifest['multicam_groups'][0]['source_ids'], [
            f'project-media-{primary.pk}', f'project-media-{extra.pk}',
        ])
        self.assertFalse(manifest['sources'][1]['metadata']['render_enabled'])
        self.assertEqual(
            [source.asset_id for source in InternalTimelineBuilder()._sources(project)],
            [f'project-media-{primary.pk}'],
        )

    def test_decision_snapshot_maps_speech_cuts_back_after_background_voice(self):
        project = self.make_project()
        project.configuration = {
            'background_voice_plan': {'duration_ms': 1000, 'cuts': [{'start_ms': 100, 'end_ms': 200, 'kind': 'background_voice'}]},
            'speech_edit_plan': {'duration_ms': 900, 'cuts': [{'start_ms': 250, 'end_ms': 350, 'kind': 'silence'}]},
        }
        snapshot = EditDecisionSetBuilder.build(project, SourceManifestBuilder.build(project))
        cuts = [item for item in snapshot['operations'] if item['type'] == 'remove_segment']

        self.assertEqual([(item['source_in_ms'], item['source_out_ms']) for item in cuts], [(100, 200), (350, 450)])

    def test_preview_revision_restores_an_automatic_cut_without_reanalysis(self):
        project = self.make_project()
        media = ProjectBlockMedia.objects.create(
            project=project, block=self.block, position=1, original_filename='take.mov',
            file=SimpleUploadedFile('take.mov', b'video'), duration_ms=10_000,
        )
        manifest = SourceManifestBuilder.build(project)
        decisions = {
            'schema': 'connect.edit_decisions.v1',
            'operations': [{
                'id': 'speech-edit-1', 'type': 'remove_segment', 'source_id': 'project-master',
                'source_in_ms': 2000, 'source_out_ms': 3000, 'reason': 'silence',
                'metadata': {'kind': 'silence'}, 'enabled': True,
            }],
        }
        project.configuration = {'source_manifest': manifest, 'edit_decision_set': decisions}
        job = self.make_job()
        job.processing_project = project
        job.save(update_fields=['processing_project', 'update_at'])
        project.render_job = job
        project.save(update_fields=['configuration', 'render_job', 'update_at'])
        track = SubtitleTrack.objects.create(job=job, language='pt', is_source=True)
        cue = SubtitleCue.objects.create(
            track=track, cue_index=1, start_ms=4000, end_ms=5000, text='Depois do corte',
        )

        initial = TimelineRevisionService.ensure_initial(project, self.member)
        restored = TimelineRevisionService.mutate_decision(
            project, self.member, 'speech-edit-1', False,
        )

        self.assertEqual(initial.revision, 1)
        self.assertEqual(restored.revision, 2)
        self.assertEqual(initial.timeline['sequence']['duration_ms'], 9000)
        self.assertEqual(restored.timeline['sequence']['duration_ms'], 10000)
        self.assertFalse(restored.edit_decision_set['operations'][0]['enabled'])
        cue.refresh_from_db()
        self.assertEqual((cue.start_ms, cue.end_ms), (5000, 6000))
        project.refresh_from_db()
        self.assertTrue(project.preview_dirty)
        self.assertTrue(project.final_render_outdated)
        self.assertEqual(PreviewSession.objects.get(project=project).undo_stack, [initial.pk])
        TimelineRevisionService.navigate_history(project, self.member, 'undo')
        cue.refresh_from_db()
        self.assertEqual((cue.start_ms, cue.end_ms), (4000, 5000))
        self.assertEqual(
            TimelineRevisionService.history_state(project, self.member),
            {'can_undo': False, 'can_redo': True},
        )
        TimelineRevisionService.navigate_history(project, self.member, 'redo')
        cue.refresh_from_db()
        self.assertEqual((cue.start_ms, cue.end_ms), (5000, 6000))
        self.assertEqual(
            TimelineRevisionService.history_state(project, self.member),
            {'can_undo': True, 'can_redo': False},
        )
        media.delete(force_policy=HARD_DELETE)

    def test_approval_pins_the_current_timeline_revision(self):
        project = self.make_project()
        manifest = {'schema': 'connect.source_manifest.v1', 'sources': []}
        decisions = {'schema': 'connect.edit_decisions.v1', 'operations': []}
        project.configuration = {'source_manifest': manifest, 'edit_decision_set': decisions}
        project.save(update_fields=['configuration', 'update_at'])
        revision = TimelineRevisionService.ensure_initial(project, self.member)

        approved = TimelineRevisionService.approve(project, self.member)

        project.refresh_from_db()
        self.assertEqual(approved, revision)
        self.assertEqual(project.approved_timeline_revision, revision)
        self.assertFalse(project.preview_dirty)
        self.assertTrue(project.final_render_outdated)

    def test_interactive_preview_page_uses_the_persisted_timeline(self):
        project = self.make_project()
        project.status = ExternalMediaProject.Status.AWAITING_REVIEW
        project.configuration = {
            'source_manifest': {'schema': 'connect.source_manifest.v1', 'sources': []},
            'edit_decision_set': {'schema': 'connect.edit_decisions.v1', 'operations': []},
        }
        project.save(update_fields=['status', 'configuration', 'update_at'])
        revision = TimelineRevisionService.ensure_initial(project, self.member)

        response = self.client.get(reverse('external_media_project_preview', args=[project.public_id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Revisar edição')
        self.assertContains(response, f'Revisão {revision.revision}')
        self.assertContains(response, 'connect.internal_timeline.v1')

    def make_reviewable_project(self):
        project = self.make_project()
        job = self.make_job()
        job.processing_project = project
        job.save(update_fields=['processing_project', 'update_at'])
        project.render_job = job
        project.status = ExternalMediaProject.Status.FINISHED
        project.progress = 100
        project.save(update_fields=['render_job', 'status', 'progress', 'update_at'])
        pt = SubtitleTrack.objects.create(job=job, language='pt', is_source=True)
        en = SubtitleTrack.objects.create(job=job, language='en')
        pt_cue = SubtitleCue.objects.create(
            track=pt, cue_index=1, start_ms=1000, end_ms=3000, text='Um encontro com Jesus',
        )
        en_cue = SubtitleCue.objects.create(
            track=en, cue_index=1, start_ms=1000, end_ms=3000, text='One experience with Jesus',
        )
        return project, job, pt, en, pt_cue, en_cue

    def test_review_link_is_hashed_and_public_page_exposes_only_allowed_tracks(self):
        project, _job, _pt, _en, _pt_cue, _en_cue = self.make_reviewable_project()
        review, token = SubtitleReviewService.create_session(
            project, self.member, ['en'], reviewer_name='John',
        )

        self.assertGreaterEqual(len(token), 64)
        self.assertNotEqual(review.token_hash, token)
        self.assertTrue(SubtitleRevision.objects.filter(track__language='en', revision=1).exists())

        response = self.client.get(reverse('public_subtitle_review', args=[token]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'One experience with Jesus')
        self.assertNotContains(response, 'Um encontro com Jesus')

    def test_internal_review_hub_renders_tracks_and_received_reviews(self):
        project, _job, _pt, _en, _pt_cue, en_cue = self.make_reviewable_project()
        review, _token = SubtitleReviewService.create_session(
            project, self.member, ['en'], reviewer_name='John',
        )
        SubtitleReviewService.autosave(review, en_cue.pk, 'One encounter with Jesus')
        SubtitleReviewService.submit(review)

        response = self.client.get(
            reverse('external_media_project_subtitle_reviews', args=[project.public_id]),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Legendas e revisões')
        self.assertContains(response, 'John')
        self.assertContains(response, 'Aguardando aprovação')

    def test_external_autosave_never_changes_the_official_cue(self):
        project, _job, _pt, _en, _pt_cue, en_cue = self.make_reviewable_project()
        review, token = SubtitleReviewService.create_session(project, self.member, ['en'])

        response = self.client.post(
            reverse('public_subtitle_review_autosave', args=[token]),
            data=json.dumps({
                'cue_id': en_cue.pk,
                'suggested_text': 'One encounter with Jesus',
                'comment': 'Encounter sounds more natural.',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        en_cue.refresh_from_db()
        self.assertEqual(en_cue.text, 'One experience with Jesus')
        suggestion = review.suggestions.get()
        self.assertEqual(suggestion.suggested_text, 'One encounter with Jesus')
        self.assertEqual(suggestion.status, SubtitleSuggestion.Status.PENDING)

    def test_submitted_review_cannot_be_edited_again(self):
        project, _job, _pt, _en, _pt_cue, en_cue = self.make_reviewable_project()
        review, token = SubtitleReviewService.create_session(project, self.member, ['en'])
        SubtitleReviewService.autosave(review, en_cue.pk, 'One encounter with Jesus')
        SubtitleReviewService.submit(review, 'Everything else looks good.')

        response = self.client.post(
            reverse('public_subtitle_review_autosave', args=[token]),
            data=json.dumps({
                'cue_id': en_cue.pk,
                'suggested_text': 'Another version',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(review.suggestions.get().suggested_text, 'One encounter with Jesus')

    def test_reviewer_can_restore_original_text_and_suggest_again(self):
        project, _job, _pt, _en, _pt_cue, en_cue = self.make_reviewable_project()
        review, _token = SubtitleReviewService.create_session(project, self.member, ['en'])
        SubtitleReviewService.autosave(review, en_cue.pk, 'One encounter with Jesus')

        SubtitleReviewService.autosave(review, en_cue.pk, en_cue.text)
        recreated = SubtitleReviewService.autosave(review, en_cue.pk, 'An encounter with Jesus')

        self.assertEqual(review.suggestions.count(), 1)
        self.assertEqual(recreated.suggested_text, 'An encounter with Jesus')

    def test_reviewer_can_submit_a_review_without_changes(self):
        project, _job, _pt, _en, _pt_cue, _en_cue = self.make_reviewable_project()
        review, _token = SubtitleReviewService.create_session(project, self.member, ['en'])

        SubtitleReviewService.submit(review, 'Everything looks good.')

        review.refresh_from_db()
        self.assertEqual(review.status, SubtitleReviewSession.Status.SUBMITTED)
        self.assertEqual(review.general_comment, 'Everything looks good.')

    def test_public_submit_accepts_null_origin_when_token_is_valid(self):
        project, _job, _pt, _en, _pt_cue, _en_cue = self.make_reviewable_project()
        _review, token = SubtitleReviewService.create_session(project, self.member, ['en'])

        response = Client(enforce_csrf_checks=True).post(
            reverse('public_subtitle_review_submit', args=[token]),
            {'general_comment': 'Everything looks good.'},
            HTTP_ORIGIN='null',
        )

        self.assertEqual(response.status_code, 302)

    def test_approved_suggestion_versions_and_marks_only_its_track_dirty(self):
        project, _job, pt, en, _pt_cue, en_cue = self.make_reviewable_project()
        review, _token = SubtitleReviewService.create_session(project, self.member, ['en'])
        suggestion = SubtitleReviewService.autosave(
            review, en_cue.pk, 'One encounter with Jesus',
        )
        SubtitleReviewService.submit(review)

        SubtitleReviewService.decide(suggestion, self.member, approve=True)

        en_cue.refresh_from_db()
        en.refresh_from_db()
        pt.refresh_from_db()
        self.assertEqual(en_cue.text, 'One encounter with Jesus')
        self.assertEqual(en.revision, 2)
        self.assertTrue(en.human_reviewed)
        self.assertTrue(en.subtitle_dirty)
        self.assertFalse(pt.subtitle_dirty)
        self.assertTrue(SubtitleRevision.objects.filter(track=en, revision=2).exists())

    def test_changed_official_cue_becomes_a_conflict_instead_of_being_overwritten(self):
        project, _job, _pt, _en, _pt_cue, en_cue = self.make_reviewable_project()
        review, _token = SubtitleReviewService.create_session(project, self.member, ['en'])
        suggestion = SubtitleReviewService.autosave(
            review, en_cue.pk, 'One encounter with Jesus',
        )
        en_cue.text = 'A new official version'
        en_cue.save(update_fields=['text', 'update_at'])

        result = SubtitleReviewService.decide(suggestion, self.member, approve=True)

        en_cue.refresh_from_db()
        self.assertEqual(result.status, SubtitleSuggestion.Status.CONFLICT)
        self.assertEqual(en_cue.text, 'A new official version')

    def test_changing_portuguese_marks_translation_as_outdated(self):
        project, _job, _pt, en, pt_cue, _en_cue = self.make_reviewable_project()
        review, _token = SubtitleReviewService.create_session(project, self.member, ['pt'])
        suggestion = SubtitleReviewService.autosave(review, pt_cue.pk, 'Um encontro real com Jesus')

        SubtitleReviewService.decide(suggestion, self.member, approve=True)

        en.refresh_from_db()
        self.assertEqual(en.translation_status, SubtitleTrack.TranslationStatus.SOURCE_CHANGED)

    def test_revoked_review_link_stops_working_immediately(self):
        project, _job, _pt, _en, _pt_cue, _en_cue = self.make_reviewable_project()
        review, token = SubtitleReviewService.create_session(project, self.member, ['en'])
        review.revoked_at = timezone.now()
        review.save(update_fields=['revoked_at', 'update_at'])

        response = self.client.get(reverse('public_subtitle_review', args=[token]))

        self.assertEqual(response.status_code, 404)

    @patch.object(ExternalMediaPipeline, 'render_outputs')
    def test_applying_review_renders_only_subtitle_outputs(self, render_outputs):
        project, job, _pt, en, _pt_cue, _en_cue = self.make_reviewable_project()
        en.subtitle_dirty = True
        en.save(update_fields=['subtitle_dirty', 'update_at'])
        project.status = ExternalMediaProject.Status.PENDING
        project.celery_task_id = 'review-render-task'
        project.save(update_fields=['status', 'celery_task_id', 'update_at'])

        result = render_reviewed_subtitles.apply(
            args=[project.pk], task_id='review-render-task', throw=True,
        )

        project.refresh_from_db()
        en.refresh_from_db()
        self.assertTrue(result.successful())
        render_outputs.assert_called_once_with(job.pk)
        self.assertEqual(project.status, ExternalMediaProject.Status.FINISHED)
        self.assertFalse(en.subtitle_dirty)
        self.assertTrue(SubtitleVideoVersion.objects.filter(project=project, version=1).exists())

    @patch('website.views.subtitle_review.render_reviewed_subtitles.apply_async')
    def test_applying_approved_review_queues_subtitle_render_without_outer_join_lock(self, apply_async):
        project, _job, _pt, en, _pt_cue, _en_cue = self.make_reviewable_project()
        en.subtitle_dirty = True
        en.save(update_fields=['subtitle_dirty', 'update_at'])
        review, _token = SubtitleReviewService.create_session(project, self.member, ['en'])

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse(
                'external_media_project_subtitle_review_render', args=[project.public_id, review.pk],
            ))

        self.assertRedirects(response, reverse('external_media_project_detail', args=[project.public_id]))
        project.refresh_from_db()
        self.assertEqual(project.status, ExternalMediaProject.Status.PENDING)
        apply_async.assert_called_once()

    def test_worker_rejects_a_stale_execution_identifier(self):
        project = self.make_project()
        project.status = ExternalMediaProject.Status.PENDING
        project.celery_task_id = 'current-task'
        project.save(update_fields=['status', 'celery_task_id', 'update_at'])

        self.assertTrue(_execution_is_current(project.pk, 'current-task'))
        self.assertFalse(_execution_is_current(project.pk, 'old-task'))

    def test_create_project_freezes_selected_template_version(self):
        response = self.client.post(reverse('external_media_create'), {
            'template_version': self.version.pk,
            'name': 'Anúncio Agosto',
        })
        project = ExternalMediaProject.objects.get(name='Anúncio Agosto')
        self.assertRedirects(response, reverse('external_media_project_detail', args=[project.public_id]))
        self.assertEqual(project.template_version, self.version)

    def test_project_form_uses_current_template_without_publication_step(self):
        self.version.status = MediaTemplateVersion.Status.DRAFT
        self.version.save(update_fields=['status', 'update_at'])

        form = ExternalMediaProjectForm()

        self.assertIn(self.version, list(form.fields['template_version'].queryset))

    def test_upload_is_attached_to_the_correct_block(self):
        project = self.make_project()
        response = self.client.post(
            reverse('external_media_project_upload', args=[project.public_id, self.block.pk]),
            {'file': SimpleUploadedFile('aviso.mp4', b'video', content_type='video/mp4')},
        )
        self.assertEqual(response.status_code, 302)
        upload = ProjectBlockMedia.objects.get(project=project)
        self.assertEqual(upload.block, self.block)
        self.assertEqual(upload.position, 1)

    def test_preview_task_skips_media_deleted_before_worker_start(self):
        result = create_project_preview.run(999999)

        self.assertEqual(result, {'skipped': True, 'reason': 'media-deleted'})

    def test_project_media_can_be_reordered_before_processing(self):
        project = self.make_project()
        first = ProjectBlockMedia.objects.create(
            project=project, block=self.block,
            file=SimpleUploadedFile('primeiro.mp4', b'video', content_type='video/mp4'),
            original_filename='primeiro.mp4', file_size=5, position=1,
        )
        second = ProjectBlockMedia.objects.create(
            project=project, block=self.block,
            file=SimpleUploadedFile('segundo.mp4', b'video', content_type='video/mp4'),
            original_filename='segundo.mp4', file_size=5, position=2,
        )

        response = self.client.post(
            reverse('external_media_project_media_reorder', args=[project.public_id, self.block.pk]),
            {'media_ids[]': [str(second.pk), str(first.pk)]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(project.block_media.order_by('position').values_list('pk', flat=True)),
            [second.pk, first.pk],
        )

    def test_project_detail_page_renders_blocks_and_plugins(self):
        project = self.make_project()
        response = self.client.get(reverse('external_media_project_detail', args=[project.public_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Gerar vídeo')
        self.assertContains(response, 'Adicionar bloco personalizado')

    def test_project_duration_minutes_rounds_up(self):
        project = self.make_project()
        project.started_at = project.created_at
        project.finished_at = project.created_at + timedelta(seconds=1857)
        project.save(update_fields=['started_at', 'finished_at', 'update_at'])

        self.assertEqual(project.duration_minutes, 31)

    def test_project_status_returns_live_elapsed_time_and_historical_eta(self):
        project = self.make_project()
        now = timezone.now()
        previous = self.make_job()
        previous.processing_project = project
        previous.started_at = now - timedelta(minutes=22)
        previous.finished_at = now - timedelta(minutes=2)
        previous.save(update_fields=['processing_project', 'started_at', 'finished_at', 'update_at'])
        project.status = ExternalMediaProject.Status.PROCESSING
        project.progress = 50
        project.started_at = now - timedelta(minutes=10)
        project.save(update_fields=['status', 'progress', 'started_at', 'update_at'])
        ProjectPipelineStep.objects.create(
            project=project, code='assembly', label='Montando blocos', order=2,
            status=ProjectPipelineStep.Status.RUNNING, progress=20,
        )

        response = self.client.get(reverse('external_media_project_status', args=[project.public_id]))

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(response.json()['elapsed_seconds'], 600)
        self.assertIsNotNone(response.json()['estimated_remaining_seconds'])
        self.assertIn('histórico', response.json()['estimate_source'])
        self.assertEqual(response.json()['steps'][0]['code'], 'assembly')
        self.assertEqual(response.json()['steps'][0]['status'], ProjectPipelineStep.Status.RUNNING)

    def test_finished_project_has_no_remaining_estimate_or_pending_eta_copy(self):
        project = self.make_project()
        project.status = ExternalMediaProject.Status.FINISHED
        project.progress = 100
        project.started_at = timezone.now() - timedelta(minutes=3)
        project.finished_at = timezone.now()
        project.save(update_fields=['status', 'progress', 'started_at', 'finished_at', 'update_at'])

        status_response = self.client.get(
            reverse('external_media_project_status', args=[project.public_id]),
        )
        self.assertTrue(status_response.json()['is_terminal'])
        self.assertIsNone(status_response.json()['estimated_remaining_seconds'])
        self.assertIsNone(status_response.json()['estimate_source'])

        detail_response = self.client.get(
            reverse('external_media_project_detail', args=[project.public_id]),
        )
        self.assertContains(detail_response, 'Tempo decorrido')
        self.assertNotContains(detail_response, 'Status final')
        self.assertNotContains(detail_response, 'Calculando previsão...')

    def test_project_can_be_renamed_from_the_edit_screen(self):
        project = self.make_project()

        response = self.client.post(
            reverse('external_media_project_edit', args=[project.public_id]),
            {'name': 'Anúncios de setembro'},
        )

        self.assertRedirects(response, reverse('external_media_project_detail', args=[project.public_id]))
        project.refresh_from_db()
        self.assertEqual(project.name, 'Anúncios de setembro')

    def test_finished_project_can_return_to_video_editing_without_losing_uploads(self):
        project = self.make_project()
        upload = ProjectBlockMedia.objects.create(
            project=project, block=self.block,
            file=SimpleUploadedFile('aviso.mp4', b'video', content_type='video/mp4'),
            original_filename='aviso.mp4', file_size=5,
        )
        job = self.make_job()
        job.processing_project = project
        job.save(update_fields=['processing_project', 'update_at'])
        project.render_job = job
        project.status = ExternalMediaProject.Status.FINISHED
        project.progress = 100
        project.save(update_fields=['render_job', 'status', 'progress', 'update_at'])

        response = self.client.post(
            reverse('external_media_project_resume_editing', args=[project.public_id]),
        )

        self.assertRedirects(response, reverse('external_media_project_detail', args=[project.public_id]))
        project.refresh_from_db()
        self.assertEqual(project.status, ExternalMediaProject.Status.DRAFT)
        self.assertIsNone(project.render_job_id)
        self.assertTrue(ProjectBlockMedia.objects.filter(pk=upload.pk, project=project).exists())
        self.assertTrue(ExternalMediaJob.objects.filter(pk=job.pk, processing_project=project).exists())

    def test_editing_mode_can_restore_the_previous_processed_result(self):
        project = self.make_project()
        job = self.make_job()
        job.processing_project = project
        job.status = ExternalMediaJob.Status.FINISHED
        job.save(update_fields=['processing_project', 'status', 'update_at'])
        project.status = ExternalMediaProject.Status.DRAFT
        project.configuration = {
            '_editable_previous_render_job_id': job.pk,
            '_editable_previous_steps': [],
        }
        project.save(update_fields=['status', 'configuration', 'update_at'])

        response = self.client.post(
            reverse('external_media_project_restore_processed_result', args=[project.public_id]),
        )

        self.assertRedirects(response, reverse('external_media_project_detail', args=[project.public_id]))
        project.refresh_from_db()
        self.assertEqual(project.status, ExternalMediaProject.Status.FINISHED)
        self.assertEqual(project.render_job_id, job.pk)
        self.assertNotIn('_editable_previous_render_job_id', project.configuration)

    def test_terminal_project_can_be_deleted_from_the_dashboard(self):
        project = self.make_project()
        job = self.make_job()
        previous_job = self.make_job()
        previous_job.processing_project = project
        previous_job.save(update_fields=['processing_project', 'update_at'])
        project.render_job = job
        project.status = ExternalMediaProject.Status.FINISHED
        project.save(update_fields=['render_job', 'status', 'update_at'])

        response = self.client.post(reverse('external_media_project_delete', args=[project.public_id]))

        self.assertRedirects(response, reverse('external_media_dashboard'))
        self.assertFalse(ExternalMediaProject.all_objects.filter(pk=project.pk).exists())
        self.assertFalse(ExternalMediaJob.all_objects.filter(pk=job.pk).exists())
        self.assertFalse(ExternalMediaJob.all_objects.filter(pk=previous_job.pk).exists())

    def test_project_detail_hides_processing_diagnostics(self):
        project = self.make_project()
        previous_job = self.make_job()
        previous_job.processing_project = project
        previous_job.save(update_fields=['processing_project', 'update_at'])
        project.configuration = {
            'speech_edit_preview': {
                'silence_count': 2,
                'filler_count': 1,
                'saved_seconds': 3,
            },
        }
        project.save(update_fields=['configuration', 'update_at'])

        response = self.client.get(reverse('external_media_project_detail', args=[project.public_id]))

        self.assertNotContains(response, 'Histórico de processamentos')
        self.assertNotContains(response, 'Edição inteligente de fala')

    def test_processing_project_cannot_be_deleted(self):
        project = self.make_project()
        project.status = ExternalMediaProject.Status.PROCESSING
        project.save(update_fields=['status', 'update_at'])

        response = self.client.post(reverse('external_media_project_delete', args=[project.public_id]))

        self.assertRedirects(response, reverse('external_media_dashboard'))
        self.assertTrue(ExternalMediaProject.objects.filter(pk=project.pk).exists())

    @patch('website.views.external_media.export_premiere_project.delay')
    def test_finished_project_can_queue_editable_premiere_export(self, delay):
        delay.return_value.id = 'premiere-export-123'
        project = self.make_project()
        job = self.make_job()
        project.render_job = job
        project.status = ExternalMediaProject.Status.FINISHED
        project.save(update_fields=['render_job', 'status', 'update_at'])

        response = self.client.post(
            reverse('external_media_project_export_premiere', args=[project.public_id]),
        )

        self.assertRedirects(response, reverse('external_media_project_detail', args=[project.public_id]))
        export = ExternalMediaProjectExport.objects.get(project=project)
        self.assertEqual(export.status, ExternalMediaProjectExport.Status.PREPARING)
        self.assertEqual(export.celery_task_id, 'premiere-export-123')
        detail = self.client.get(reverse('external_media_project_detail', args=[project.public_id]))
        self.assertContains(detail, 'Exportar projeto')
        self.assertContains(detail, 'Adobe Premiere')

    def test_export_status_endpoint_returns_async_progress(self):
        project = self.make_project()
        export = ExternalMediaProjectExport.objects.create(
            project=project, created_by=self.member,
            status=ExternalMediaProjectExport.Status.VALIDATING,
            progress=78, current_step='Validando XML',
        )
        response = self.client.get(reverse(
            'external_media_project_export_status', args=[project.public_id, export.public_id],
        ))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['progress'], 78)
        self.assertFalse(response.json()['is_terminal'])

    def test_reupload_after_delete_does_not_reuse_soft_deleted_position(self):
        project = self.make_project()
        first = ProjectBlockMedia.objects.create(
            project=project, block=self.block, position=1, original_filename='primeiro.mp4',
            file=SimpleUploadedFile('primeiro.mp4', b'video', content_type='video/mp4'), file_size=5,
        )
        self.client.post(reverse('external_media_project_media_delete', args=[project.public_id, first.pk]))
        self.client.post(
            reverse('external_media_project_upload', args=[project.public_id, self.block.pk]),
            {'file': SimpleUploadedFile('segundo.mp4', b'video', content_type='video/mp4')},
        )
        self.assertEqual(ProjectBlockMedia.objects.get(project=project).position, 2)

    @patch('website.views.external_media.run_external_media_project.apply_async')
    def test_valid_project_can_enter_the_async_queue(self, apply_async):
        project = self.make_project()
        ProjectBlockMedia.objects.create(
            project=project, block=self.block, position=1, original_filename='video.mp4',
            file=SimpleUploadedFile('video.mp4', b'video', content_type='video/mp4'), file_size=5,
        )
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('external_media_project_run', args=[project.public_id]))
        self.assertEqual(response.status_code, 302)
        project.refresh_from_db()
        self.assertEqual(project.status, ExternalMediaProject.Status.PENDING)
        self.assertTrue(project.celery_task_id)
        self.assertEqual(apply_async.call_args.kwargs['task_id'], project.celery_task_id)

    def test_required_block_prevents_pipeline_without_upload(self):
        project = self.make_project()
        response = self.client.post(reverse('external_media_project_run', args=[project.public_id]))
        self.assertEqual(response.status_code, 302)
        project.refresh_from_db()
        self.assertEqual(project.status, ExternalMediaProject.Status.DRAFT)

    @patch('website.views.external_media.run_external_media_project.apply_async')
    def test_project_can_be_retried_after_error(self, apply_async):
        project = self.make_project()
        project.status = ExternalMediaProject.Status.ERROR
        project.error_message = 'Falhou na tradução'
        project.started_at = timezone.now() - timedelta(minutes=44)
        project.finished_at = timezone.now() - timedelta(minutes=1)
        project.save(update_fields=[
            'status', 'error_message', 'started_at', 'finished_at', 'update_at',
        ])
        ProjectBlockMedia.objects.create(
            project=project, block=self.block, position=1, original_filename='video.mp4',
            file=SimpleUploadedFile('video.mp4', b'video', content_type='video/mp4'), file_size=5,
        )
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('external_media_project_run', args=[project.public_id]))
        self.assertRedirects(response, reverse('external_media_project_detail', args=[project.public_id]))
        project.refresh_from_db()
        self.assertEqual(project.status, ExternalMediaProject.Status.PENDING)
        self.assertTrue(project.celery_task_id)
        self.assertIsNone(project.started_at)
        self.assertIsNone(project.finished_at)
        self.assertEqual(apply_async.call_args.kwargs['task_id'], project.celery_task_id)

    @patch('website.views.external_media.run_external_media_project.apply_async')
    def test_finished_project_can_be_reprocessed_with_existing_uploads(self, apply_async):
        project = self.make_project()
        project.status = ExternalMediaProject.Status.FINISHED
        project.started_at = timezone.now() - timedelta(minutes=44)
        project.finished_at = timezone.now() - timedelta(minutes=1)
        project.save(update_fields=['status', 'started_at', 'finished_at', 'update_at'])
        ProjectBlockMedia.objects.create(
            project=project, block=self.block, position=1, original_filename='video.mp4',
            file=SimpleUploadedFile('video.mp4', b'video', content_type='video/mp4'), file_size=5,
        )
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('external_media_project_run', args=[project.public_id]))
        self.assertRedirects(response, reverse('external_media_project_detail', args=[project.public_id]))
        project.refresh_from_db()
        self.assertEqual(project.status, ExternalMediaProject.Status.PENDING)
        self.assertTrue(project.celery_task_id)
        self.assertIsNone(project.started_at)
        self.assertIsNone(project.finished_at)
        self.assertEqual(apply_async.call_args.kwargs['task_id'], project.celery_task_id)
        self.assertEqual(project.block_media.count(), 1)

    @patch('website.external_media.preview.TimelineRevisionService.ensure_initial')
    @patch('website.external_media.preview.ProjectProxyService.prepare')
    @patch.object(ExternalMediaProjectPipeline, 'render')
    @patch.object(ExternalMediaPipeline, 'prepare_subtitle_tracks')
    @patch.object(ExternalMediaProjectPipeline, '_create_render_job')
    @patch.object(ExternalMediaProjectPipeline, '_create_proxies')
    @patch.object(ExternalMediaProjectPipeline, '_materialize')
    @patch.object(VideoAssemblyService, '_duration_ms', return_value=1000)
    @patch.object(VideoAssemblyService, 'assemble')
    def test_project_pipeline_waits_for_interactive_review_after_subtitles(
        self, assemble_mock, _duration_mock, materialize_mock, proxies_mock, create_job_mock,
        prepare_tracks_mock, render_mock, prepare_preview_mock, ensure_revision_mock,
    ):
        project = self.make_project()
        ProjectBlockMedia.objects.create(
            project=project, block=self.block, position=1, original_filename='video.mp4',
            file=SimpleUploadedFile('video.mp4', b'video', content_type='video/mp4'), file_size=5,
        )
        job = ExternalMediaJob.objects.create(
            name=project.name, created_by=self.member,
            original_video=SimpleUploadedFile('proxy.mp4', b'video', content_type='video/mp4'),
            original_language='pt', output_languages=['pt', 'en'],
            preset=self.preset, subtitle_style=self.style,
            status=ExternalMediaJob.Status.PENDING,
        )
        materialize_mock.return_value = ([Mock()], None, None)
        proxies_mock.return_value = [Mock()]
        assemble_mock.return_value = False
        create_job_mock.return_value = job

        ExternalMediaProjectPipeline().run(project.pk)

        prepare_tracks_mock.assert_called_once_with(job.pk)
        prepare_preview_mock.assert_called_once()
        ensure_revision_mock.assert_called_once()
        render_mock.assert_not_called()
        project.refresh_from_db()
        self.assertEqual(project.status, ExternalMediaProject.Status.AWAITING_REVIEW)

    def test_prepare_subtitle_tracks_never_pauses_for_review(self):
        job = self.make_job(status=ExternalMediaJob.Status.PENDING)
        pipeline = ExternalMediaPipeline()
        pipeline.storage = Mock()
        pipeline.audio = Mock()
        pipeline.transcription = Mock()
        pipeline.translation = Mock()
        pipeline.speech_analyzer = Mock()
        pipeline.speech_editor = Mock()
        pipeline.storage.copy_to_local.return_value = None
        pipeline.audio.extract.return_value = []
        pipeline.transcription.transcribe_detailed.return_value = []
        pipeline.transcription.group_for_subtitles.return_value = []
        pipeline._save_source_track = Mock(return_value=Mock())
        pipeline._apply_speech_edit = Mock(side_effect=lambda _job, _video, _workdir, detailed: detailed)
        pipeline._block_boundaries_ms = Mock(return_value=[])

        pipeline.prepare_subtitle_tracks(job.pk)

        job.refresh_from_db()
        self.assertNotEqual(job.status, ExternalMediaJob.Status.AWAITING_REVIEW)
        self.assertEqual(job.current_step, 'Legendas prontas')

    def test_proxy_speech_edit_stores_plan_without_rendering_master_video(self):
        MediaTemplatePlugin.objects.create(
            version=self.version, code=MediaTemplatePlugin.Code.FILLER_REMOVAL, order=3,
        )
        project = self.make_project()
        project.configuration = {'proxy_pipeline': True}
        job = self.make_job()
        project.render_job = job
        project.save(update_fields=['configuration', 'render_job', 'update_at'])
        pipeline = ExternalMediaPipeline()
        pipeline.speech_editor = Mock()
        pipeline.speech_editor.duration_ms.return_value = 2000
        pipeline.speech_analyzer = Mock()
        pipeline.speech_analyzer.analyze.return_value = SpeechEditPlan(
            (SpeechCut(500, 900, 'filler', 'hum'),), 2000, 40,
        )
        detailed = [TranscriptionSegment(1000, 1300, 'Depois', 'word')]

        remapped = pipeline._apply_speech_edit(job, Path('/tmp/proxy.mp4'), Path('/tmp'), detailed)

        pipeline.speech_editor.apply.assert_not_called()
        project.refresh_from_db()
        self.assertIn('speech_edit_plan', project.configuration)
        self.assertEqual(project.configuration['speech_edit_preview']['filler_count'], 1)
        self.assertEqual(remapped[0].start_ms, 600)

    def test_speech_edit_remaps_block_ranges_to_the_edited_timeline(self):
        MediaTemplatePlugin.objects.create(
            version=self.version, code=MediaTemplatePlugin.Code.FILLER_REMOVAL, order=3,
        )
        project = self.make_project()
        project.configuration = {
            'proxy_pipeline': True,
            'block_ranges': [
                {'start_ms': 0, 'end_ms': 1000, 'block_key': 'a'},
                {'start_ms': 1000, 'end_ms': 2000, 'block_key': 'b'},
            ],
        }
        job = self.make_job()
        project.render_job = job
        project.save(update_fields=['configuration', 'render_job', 'update_at'])
        pipeline = ExternalMediaPipeline()
        pipeline.speech_editor = Mock()
        pipeline.speech_editor.duration_ms.return_value = 2000
        pipeline.speech_analyzer = Mock()
        # A single 400ms filler cut squarely inside block "a" shortens it and shifts every
        # later boundary (including block "b") back by 400ms in the edited timeline.
        pipeline.speech_analyzer.analyze.return_value = SpeechEditPlan(
            (SpeechCut(500, 900, 'filler', 'hum'),), 2000, 40,
        )
        detailed = [TranscriptionSegment(1000, 1300, 'Depois', 'word')]

        pipeline._apply_speech_edit(job, Path('/tmp/proxy.mp4'), Path('/tmp'), detailed)

        project.refresh_from_db()
        ranges = project.configuration['block_ranges']
        self.assertEqual(ranges[0]['start_ms'], 0)
        self.assertEqual(ranges[0]['end_ms'], 600)
        self.assertEqual(ranges[0]['block_key'], 'a')
        self.assertEqual(ranges[1]['start_ms'], 600)
        self.assertEqual(ranges[1]['end_ms'], 1600)
        self.assertEqual(ranges[1]['block_key'], 'b')

    def test_project_upload_path_stays_short_even_with_long_block_name(self):
        long_block = MediaTemplateBlock.objects.create(
            version=self.version,
            key='encontro-dos-homens-forja-com-um-nome-bem-grande-para-validar',
            name='Bloco longo',
            order=2,
            is_required=False,
            min_occurrences=1,
            max_occurrences=1,
        )
        item = ProjectBlockMedia(
            project=self.make_project(),
            block=long_block,
            position=1,
            original_filename='VIDEO_SUPER_LONGO_DO_IPHONE.mov',
            file=SimpleUploadedFile('VIDEO_SUPER_LONGO_DO_IPHONE.mov', b'video', content_type='video/quicktime'),
            file_size=5,
        )
        path = external_media_project_upload_path(item, item.file.name)
        self.assertLess(len(path), 100)
        self.assertTrue(path.endswith('.mov'))

    def test_enabled_plugins_derive_translation_from_version_languages(self):
        project = self.make_project()
        self.version.output_languages = ['pt']
        self.version.save(update_fields=['output_languages', 'update_at'])
        plugins = TemplateService.enabled_plugins(project)
        plugin_codes = {plugin.code for plugin in plugins}
        self.assertIn(MediaTemplatePlugin.Code.SUBTITLE_PT, plugin_codes)
        self.assertNotIn(MediaTemplatePlugin.Code.TRANSLATION_EN, plugin_codes)

    def test_enabled_plugins_respect_template_without_subtitles(self):
        project = self.make_project()
        self.version.subtitles_enabled = False
        self.version.translated_subtitles_enabled = False
        self.version.save(update_fields=['subtitles_enabled', 'translated_subtitles_enabled', 'update_at'])

        plugin_codes = {plugin.code for plugin in TemplateService.enabled_plugins(project)}

        self.assertNotIn(MediaTemplatePlugin.Code.SUBTITLE_PT, plugin_codes)
        self.assertNotIn(MediaTemplatePlugin.Code.TRANSLATION_EN, plugin_codes)

    def test_catalog_lut_has_priority_over_legacy_template_file(self):
        catalog_lut = ColorLUT.objects.create(
            name='Warm Film',
            default_intensity=65,
            lut_file=SimpleUploadedFile('warm.cube', b'TITLE "Warm"\nLUT_3D_SIZE 2\n'),
        )
        self.version.color_lut = catalog_lut
        self.version.lut_file = SimpleUploadedFile('legacy.cube', b'TITLE "Legacy"\nLUT_3D_SIZE 2\n')
        self.version.save(update_fields=['color_lut', 'lut_file', 'update_at'])

        selected = LUTService.selected_file(self.version, {MediaTemplatePlugin.Code.LUT})

        self.assertEqual(selected.name, catalog_lut.lut_file.name)
        self.assertEqual(catalog_lut.default_intensity, 65)


class AdminExternalMediaTemplateTests(ExternalMediaFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.admin_user = User.objects.create_superuser('admin-media@example.com', 'secret')
        self.client.force_login(self.admin_user)
        self.template = MediaTemplate.objects.create(
            name='Template Admin', slug='template-admin', category=MediaTemplate.Category.TRANSLATION,
        )
        self.version = MediaTemplateVersion.objects.create(
            template=self.template, version=1, status=MediaTemplateVersion.Status.DRAFT,
            preset=self.preset, subtitle_style=self.style, output_languages=['pt', 'en'],
        )

    def _version_edit_payload(self, **overrides):
        data = {
            'name': self.template.name,
            'description': self.template.description or '',
            'is_active': 'on' if self.template.is_active else '',
            'preset': self.preset.pk,
            'subtitle_style': self.style.pk,
            'original_language': 'pt',
            'language_mode': 'translated',
            'translated_language': 'en',
            'default_settings_raw': '{}',
            'allowed_overrides_raw': '[]',
            'blocks-TOTAL_FORMS': '0',
            'blocks-INITIAL_FORMS': '0',
            'blocks-MIN_NUM_FORMS': '0',
            'blocks-MAX_NUM_FORMS': '1000',
        }
        data.update(overrides)
        return data

    def _version_edit_payload(self, **overrides):
        data = {
            'name': self.template.name,
            'description': self.template.description or '',
            'is_active': 'on' if self.template.is_active else '',
            'preset': self.preset.pk,
            'subtitle_style': self.style.pk,
            'original_language': 'pt',
            'language_mode': 'translated',
            'translated_language': 'en',
            'default_settings_raw': '{}',
            'allowed_overrides_raw': '[]',
            'blocks-TOTAL_FORMS': '0',
            'blocks-INITIAL_FORMS': '0',
            'blocks-MIN_NUM_FORMS': '0',
            'blocks-MAX_NUM_FORMS': '1000',
        }
        data.update(overrides)
        return data

    def test_admin_panel_media_template_pages_render(self):
        urls = [
            (reverse('admin_external_media_templates'), 200),
            (reverse('admin_external_media_glossary'), 200),
            (reverse('admin_external_media_template_detail', args=[self.template.pk]), 200),
            (reverse('admin_external_media_template_create'), 200),
            (reverse('admin_external_media_version_edit', args=[self.version.pk]), 200),
        ]
        for url, expected_status in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, expected_status)

        response = self.client.get(reverse('admin_external_media_template_edit', args=[self.template.pk]))
        self.assertRedirects(response, reverse('admin_external_media_version_edit', args=[self.version.pk]))

    def test_admin_can_edit_glossary_term(self):
        term = GlossaryTerm.objects.create(
            source_language='pt', target_language='en',
            source_text='Culto administrativo de teste', translated_text='Service',
        )

        response = self.client.post(
            reverse('admin_external_media_glossary_edit', args=[term.pk]),
            {
                'source_language': 'pt',
                'target_language': 'en',
                'source_text': 'Culto de domingo',
                'translated_text': 'Sunday Service',
            },
        )

        self.assertRedirects(response, reverse('admin_external_media_glossary'))
        term.refresh_from_db()
        self.assertEqual(term.source_text, 'Culto de domingo')
        self.assertEqual(term.translated_text, 'Sunday Service')

    def test_admin_panel_can_create_media_template(self):
        response = self.client.post(reverse('admin_external_media_template_create'), {
            'name': 'Stories de Testemunho',
            'description': 'Modelo para cortes verticais.',
            'is_active': 'on',
            'preset': self.preset.pk,
            'subtitle_style': self.style.pk,
            'original_language': 'pt',
            'language_mode': 'translated',
            'translated_language': '',
            'default_settings_raw': '{}',
            'allowed_overrides_raw': '[]',
            'blocks-TOTAL_FORMS': '1',
            'blocks-INITIAL_FORMS': '0',
            'blocks-MIN_NUM_FORMS': '0',
            'blocks-MAX_NUM_FORMS': '1000',
        })
        template = MediaTemplate.objects.get(slug='stories-de-testemunho')
        self.assertRedirects(
            response,
            reverse('admin_external_media_template_detail', args=[template.pk]),
        )

    def test_admin_panel_can_delete_media_template_without_projects(self):
        template = MediaTemplate.objects.create(
            name='Template Descartável',
            slug='template-descartavel',
            category=MediaTemplate.Category.OTHER,
        )
        response = self.client.post(reverse('admin_external_media_template_delete', args=[template.pk]))
        self.assertRedirects(response, reverse('admin_external_media_templates'))
        self.assertFalse(MediaTemplate.objects.filter(pk=template.pk).exists())

    def test_admin_panel_cannot_delete_media_template_with_projects(self):
        ExternalMediaProject.objects.create(
            name='Projeto vinculado',
            template_version=self.version,
            created_by=self.member,
        )
        response = self.client.post(reverse('admin_external_media_template_delete', args=[self.template.pk]))
        self.assertRedirects(response, reverse('admin_external_media_templates'))
        self.assertTrue(MediaTemplate.objects.filter(pk=self.template.pk).exists())

    def test_new_block_form_defaults_to_unlimited_videos(self):
        form = AdminMediaTemplateBlockForm()
        self.assertTrue(form.fields['allows_multiple'].initial)
        self.assertEqual(form.fields['min_occurrences'].initial, 1)
        self.assertEqual(form.fields['max_occurrences'].initial, 0)
        self.assertIn('skip_extra_processing', form.fields)
        self.assertIn('remove_background_voice', form.fields)

    def test_admin_panel_can_publish_draft_version(self):
        MediaTemplateBlock.objects.create(
            version=self.version, key='video', name='Vídeo', order=1,
            is_required=True, min_occurrences=1, max_occurrences=1,
        )
        response = self.client.post(reverse('admin_external_media_version_publish', args=[self.version.pk]))
        self.assertRedirects(
            response,
            reverse('admin_external_media_template_detail', args=[self.template.pk]),
        )
        self.version.refresh_from_db()
        self.assertEqual(self.version.status, MediaTemplateVersion.Status.PUBLISHED)

    def test_admin_panel_can_create_render_preset_from_version_page(self):
        response = self.client.post(reverse('admin_external_media_preset_save'), {
            'next': reverse('admin_external_media_version_edit', args=[self.version.pk]),
            'preset-name': 'YouTube 1080p',
            'preset-width': '1920',
            'preset-height': '1080',
            'preset-video_codec': 'libx264',
            'preset-audio_codec': 'aac',
            'preset-video_crf': '21',
            'preset-extra_ffmpeg_profile': '["-maxrate", "8M"]',
            'preset-is_active': 'on',
        })
        self.assertRedirects(response, reverse('admin_external_media_version_edit', args=[self.version.pk]))
        preset = RenderPreset.objects.get(code='youtube-1080p')
        self.assertEqual(preset.extra_ffmpeg_args, ['-maxrate', '8M'])

    def test_admin_panel_can_create_subtitle_style_from_version_page(self):
        response = self.client.post(reverse('admin_external_media_subtitle_style_save'), {
            'next': reverse('admin_external_media_version_edit', args=[self.version.pk]),
            'style-name': 'Legenda Feed',
            'style-font_name': 'Arial',
            'style-font_weight': '600',
            'style-font_size': '44',
            'style-primary_color': '#FFFFFF',
            'style-background_color': '#000000',
            'style-background_opacity': '70',
            'style-background_padding_x': '14',
            'style-background_padding_y': '8',
            'style-background_height_percent': '80',
            'style-background_radius': '10',
            'style-outline_color': '#111111',
            'style-outline_width': '3',
            'style-shadow': '5',
            'style-shadow_angle': '135',
            'style-shadow_size': '2',
            'style-shadow_blur': '3',
            'style-shadow_opacity': '55',
            'style-margin_bottom': '80',
            'style-alignment': '2',
            'style-max_lines': '2',
            'style-max_characters': '38',
            'style-is_active': 'on',
        })
        self.assertRedirects(response, reverse('admin_external_media_version_edit', args=[self.version.pk]))
        style = SubtitleStyle.objects.get(name='Legenda Feed')
        self.assertEqual(style.max_characters, 38)
        self.assertEqual(style.font_weight, 600)
        self.assertEqual(style.shadow, 5)
        self.assertEqual(style.shadow_angle, 135)
        self.assertEqual(style.shadow_size, 2)
        self.assertEqual(style.shadow_blur, 3)
        self.assertEqual(style.shadow_opacity, 55)
        self.assertEqual(style.background_height_percent, 80)

    def test_admin_panel_can_delete_unused_subtitle_style(self):
        style = SubtitleStyle.objects.create(name='Estilo Temporário', font_size=40)
        response = self.client.post(
            reverse('admin_external_media_subtitle_style_delete', args=[style.pk]),
            {'next': reverse('admin_external_media_version_edit', args=[self.version.pk])},
        )
        self.assertRedirects(response, reverse('admin_external_media_version_edit', args=[self.version.pk]))
        self.assertFalse(SubtitleStyle.objects.filter(pk=style.pk).exists())

    def test_admin_panel_can_delete_subtitle_style_in_use_by_template(self):
        replacement = SubtitleStyle.objects.create(name='Estilo Reserva', font_size=41)
        style = SubtitleStyle.objects.create(name='Estilo Descartável', font_size=42)
        self.version.subtitle_style = style
        self.version.translated_subtitle_style = style
        self.version.save(update_fields=['subtitle_style', 'translated_subtitle_style', 'update_at'])

        response = self.client.post(
            reverse('admin_external_media_subtitle_style_delete', args=[style.pk]),
            {'next': reverse('admin_external_media_version_edit', args=[self.version.pk])},
        )
        self.assertRedirects(response, reverse('admin_external_media_version_edit', args=[self.version.pk]))
        self.assertFalse(SubtitleStyle.objects.filter(pk=style.pk).exists())
        self.version.refresh_from_db()
        self.assertEqual(self.version.subtitle_style_id, replacement.pk)
        self.assertIsNone(self.version.translated_subtitle_style_id)

    def test_admin_panel_can_create_background_music_from_version_page(self):
        response = self.client.post(reverse('admin_external_media_background_music_save'), {
            'next': reverse('admin_external_media_version_edit', args=[self.version.pk]),
            'bgmusic-name': 'Base Instrumental',
            'bgmusic-category': 'instrumental',
            'bgmusic-tempo': 'media',
            'bgmusic-audio_file': SimpleUploadedFile('base.mp3', b'audio', content_type='audio/mpeg'),
        })
        self.assertRedirects(response, reverse('admin_external_media_version_edit', args=[self.version.pk]))
        track = BackgroundMusicTrack.objects.get(name='Base Instrumental')
        self.assertEqual(track.category, 'instrumental')
        self.assertTrue(bool(track.audio_file))

    def test_admin_panel_can_create_mastering_profile_from_version_page(self):
        response = self.client.post(reverse('admin_external_media_mastering_profile_save'), {
            'next': reverse('admin_external_media_version_edit', args=[self.version.pk]),
            'masterprofile-name': 'Telão Teste',
            'masterprofile-target_lufs': '-14.0',
            'masterprofile-true_peak_db': '-1.5',
            'masterprofile-limiter_enabled': 'on',
        })
        self.assertRedirects(response, reverse('admin_external_media_version_edit', args=[self.version.pk]))
        profile = MasteringProfile.objects.get(name='Telão Teste')
        self.assertEqual(profile.code, 'telao-teste')
        self.assertEqual(float(profile.target_lufs), -14.0)
        self.assertTrue(profile.limiter_enabled)
        self.assertFalse(profile.bus_compression_enabled)

    def test_admin_panel_saves_audio_mixing_and_mastering_toggles(self):
        profile = MasteringProfile.objects.create(name='PA Igreja Teste', code='pa-igreja-teste')
        response = self.client.post(
            reverse('admin_external_media_version_edit', args=[self.version.pk]),
            self._version_edit_payload(
                dialogue_processing_enabled='on',
                dialogue_processing_config_raw='{"compression_ratio": 3, "deesser_enabled": true}',
                audio_mixing_enabled='on',
                audio_ducking_enabled='on',
                audio_spectral_ducking_enabled='on',
                audio_mastering_enabled='on',
                mastering_profile=profile.pk,
                audio_mixing_config_raw='{"base_duck_db": 6}',
            ),
        )
        self.assertRedirects(
            response,
            reverse('admin_external_media_template_detail', args=[self.template.pk]),
        )
        self.version.refresh_from_db()
        self.assertTrue(self.version.dialogue_processing_enabled)
        self.assertEqual(
            self.version.dialogue_processing_config, {'compression_ratio': 3, 'deesser_enabled': True},
        )
        self.assertTrue(self.version.audio_mixing_enabled)
        self.assertTrue(self.version.audio_ducking_enabled)
        self.assertTrue(self.version.audio_spectral_ducking_enabled)
        self.assertTrue(self.version.audio_mastering_enabled)
        self.assertEqual(self.version.mastering_profile_id, profile.pk)
        self.assertEqual(self.version.audio_mixing_config, {'base_duck_db': 6})

    def test_admin_panel_saves_bilingual_language_strategy(self):
        response = self.client.post(
            reverse('admin_external_media_version_edit', args=[self.version.pk]),
            self._version_edit_payload(
                language_mode='bilingual_source',
                spoken_languages=['pt', 'en'],
                translated_language='en',
            ),
        )
        self.assertRedirects(
            response,
            reverse('admin_external_media_template_detail', args=[self.template.pk]),
        )
        self.version.refresh_from_db()
        self.assertEqual(self.version.default_settings['language_mode'], 'bilingual_source')
        self.assertEqual(self.version.default_settings['spoken_languages'], ['pt', 'en'])
        self.assertEqual(self.version.output_languages, ['pt', 'en'])

    def test_admin_panel_single_language_strategy_outputs_only_default_language(self):
        response = self.client.post(
            reverse('admin_external_media_version_edit', args=[self.version.pk]),
            self._version_edit_payload(
                language_mode='single',
                spoken_languages=['pt', 'en'],
                translated_language='en',
            ),
        )
        self.assertRedirects(
            response,
            reverse('admin_external_media_template_detail', args=[self.template.pk]),
        )
        self.version.refresh_from_db()
        self.assertEqual(self.version.default_settings['language_mode'], 'single')
        self.assertEqual(self.version.default_settings['spoken_languages'], ['pt'])
        self.assertEqual(self.version.output_languages, ['pt'])

    def test_admin_panel_translated_language_strategy_sets_translation_target(self):
        response = self.client.post(
            reverse('admin_external_media_version_edit', args=[self.version.pk]),
            self._version_edit_payload(
                language_mode='translated',
                spoken_languages=['pt'],
                translated_language='en',
            ),
        )
        self.assertRedirects(
            response,
            reverse('admin_external_media_template_detail', args=[self.template.pk]),
        )
        self.version.refresh_from_db()
        self.assertEqual(self.version.default_settings['language_mode'], 'translated')
        self.assertEqual(self.version.default_settings['translated_language'], 'en')
        self.assertEqual(self.version.output_languages, ['pt', 'en'])

    def test_admin_panel_can_create_multiple_blocks_in_one_version_save(self):
        response = self.client.post(
            reverse('admin_external_media_version_edit', args=[self.version.pk]),
            self._version_edit_payload(
                **{
                    'blocks-TOTAL_FORMS': '2',
                    'blocks-0-name': 'Encerramento',
                    'blocks-0-description': 'Tela final do vídeo.',
                    'blocks-0-order': '2',
                    'blocks-0-is_required': 'on',
                    'blocks-0-min_occurrences': '1',
                    'blocks-0-max_occurrences': '1',
                    'blocks-1-name': 'Mensagem principal',
                    'blocks-1-description': 'Vídeo principal.',
                    'blocks-1-order': '1',
                    'blocks-1-is_required': 'on',
                    'blocks-1-min_occurrences': '1',
                    'blocks-1-max_occurrences': '1',
                },
            ),
        )
        self.assertRedirects(
            response,
            reverse('admin_external_media_template_detail', args=[self.template.pk]),
        )
        blocks = list(self.version.blocks.order_by('order'))
        self.assertEqual([block.name for block in blocks], ['Mensagem principal', 'Encerramento'])
        self.assertEqual([block.key for block in blocks], ['mensagem-principal', 'encerramento'])
        self.assertEqual([block.max_occurrences for block in blocks], [0, 0])

    def test_admin_panel_does_not_create_empty_block_on_save(self):
        block = MediaTemplateBlock.objects.create(
            version=self.version, key='video', name='Vídeo', order=1,
            is_required=True, min_occurrences=1, max_occurrences=1,
        )
        response = self.client.post(
            reverse('admin_external_media_version_edit', args=[self.version.pk]),
            self._version_edit_payload(
                **{
                    'blocks-TOTAL_FORMS': '2',
                    'blocks-INITIAL_FORMS': '1',
                    'blocks-0-id': str(block.pk),
                    'blocks-0-key': block.key,
                    'blocks-0-name': block.name,
                    'blocks-0-order': str(block.order),
                    'blocks-0-is_required': 'on',
                    'blocks-0-min_occurrences': '1',
                    'blocks-0-max_occurrences': '1',
                },
            ),
        )
        self.assertRedirects(
            response,
            reverse('admin_external_media_template_detail', args=[self.template.pk]),
        )
        self.assertEqual(self.version.blocks.count(), 1)

    def test_admin_panel_uses_checkboxes_for_advanced_plugins(self):
        response = self.client.post(
            reverse('admin_external_media_version_edit', args=[self.version.pk]),
            self._version_edit_payload(
                advanced_plugins=[
                    MediaTemplatePlugin.Code.SILENCE_REMOVAL,
                    MediaTemplatePlugin.Code.FILLER_REMOVAL,
                ],
                speech_edit_profile='conservative',
                filler_words='eh, hum, tipo',
            ),
        )
        self.assertRedirects(
            response,
            reverse('admin_external_media_template_detail', args=[self.template.pk]),
        )
        enabled_codes = set(
            self.version.plugins.filter(is_enabled=True).values_list('code', flat=True)
        )
        self.assertIn(MediaTemplatePlugin.Code.SILENCE_REMOVAL, enabled_codes)
        self.assertIn(MediaTemplatePlugin.Code.FILLER_REMOVAL, enabled_codes)
        self.assertNotIn(MediaTemplatePlugin.Code.AUTO_TRACKING, enabled_codes)
        silence = self.version.plugins.get(code=MediaTemplatePlugin.Code.SILENCE_REMOVAL)
        fillers = self.version.plugins.get(code=MediaTemplatePlugin.Code.FILLER_REMOVAL)
        self.assertEqual(silence.configuration['profile'], 'conservative')
        self.assertEqual(fillers.configuration['filler_words'], ['eh', 'hum', 'tipo'])

    def test_admin_panel_saves_auto_reframe_priority(self):
        response = self.client.post(
            reverse('admin_external_media_version_edit', args=[self.version.pk]),
            self._version_edit_payload(
                language_mode='single',
                advanced_plugins=[MediaTemplatePlugin.Code.AUTO_TRACKING],
                auto_reframe_priority='body',
            ),
        )
        self.assertRedirects(
            response,
            reverse('admin_external_media_template_detail', args=[self.template.pk]),
        )
        plugin = self.version.plugins.get(code=MediaTemplatePlugin.Code.AUTO_TRACKING)
        self.assertTrue(plugin.is_enabled)
        self.assertEqual(plugin.configuration['priority'], 'body')
        self.assertEqual(plugin.configuration['safe_margin'], 0.15)
        self.assertEqual(plugin.configuration['top_margin'], 0.12)


class ExternalMediaRetryTests(ExternalMediaFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        MinistryMembership.objects.create(member=self.member, ministry=self.ministry)
        self.client.force_login(self.user)

    @patch('website.views.external_media.prepare_external_media.delay')
    def test_legacy_job_can_be_retried_after_error(self, delay):
        delay.return_value.id = 'task-retry-job'
        job = self.make_job(status=ExternalMediaJob.Status.ERROR)
        job.error_message = 'Falhou na tradução'
        job.save(update_fields=['error_message', 'update_at'])
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('external_media_retry', args=[job.public_id]))
        self.assertRedirects(response, reverse('external_media_detail', args=[job.public_id]))
        job.refresh_from_db()
        self.assertEqual(job.status, ExternalMediaJob.Status.PENDING)
        self.assertEqual(job.celery_task_id, 'task-retry-job')


class TranscriptionGroupingTests(SimpleTestCase):
    def test_detailed_transcription_uses_literal_prompt_for_filler_removal(self):
        ai_service = Mock()
        ai_service.transcribe_segments.return_value = [
            TranscriptionSegment(0, 250, 'eee', 'word'),
        ]
        service = TranscriptionService(ai_service=ai_service)

        service.transcribe_detailed(
            [AudioChunk(Path('/tmp/audio.mp3'), 0)],
            'pt',
            preserve_disfluencies=True,
        )

        prompt = ai_service.transcribe_segments.call_args.kwargs['prompt']
        self.assertIn('Não omita nem corrija hesitações', prompt)
        self.assertIn('eee', prompt)
        self.assertIn('hamm', prompt)

    def test_group_for_subtitles_merges_close_words_without_boundaries(self):
        words = [
            TranscriptionSegment(0, 300, 'Bom', 'word'),
            TranscriptionSegment(320, 600, 'dia', 'word'),
            TranscriptionSegment(650, 900, 'igreja', 'word'),
        ]
        cues = TranscriptionService.group_for_subtitles(words)
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].text, 'Bom dia igreja')

    def test_group_for_subtitles_splits_cue_at_block_boundary(self):
        # Without a boundary these five words (all within 900ms of each other) would be
        # merged into a single cue, mixing the end of block "a" with the start of block "b".
        words = [
            TranscriptionSegment(0, 300, 'Bom', 'word'),
            TranscriptionSegment(320, 600, 'dia', 'word'),
            TranscriptionSegment(650, 900, 'igreja', 'word'),
            TranscriptionSegment(950, 1200, 'Vamos', 'word'),
            TranscriptionSegment(1220, 1500, 'começar', 'word'),
        ]
        cues = TranscriptionService.group_for_subtitles(words, block_boundaries_ms=[900])

        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0].text, 'Bom dia igreja')
        self.assertEqual(cues[0].end_ms, 900)
        self.assertEqual(cues[1].text, 'Vamos começar')
        self.assertEqual(cues[1].start_ms, 950)

    def test_group_for_subtitles_ignores_boundaries_far_from_any_word(self):
        words = [
            TranscriptionSegment(0, 300, 'Bom', 'word'),
            TranscriptionSegment(320, 600, 'dia', 'word'),
        ]
        cues = TranscriptionService.group_for_subtitles(words, block_boundaries_ms=[50000])
        self.assertEqual(len(cues), 1)
        self.assertEqual(cues[0].text, 'Bom dia')

    def test_group_for_subtitles_keeps_segment_level_transcripts_untouched(self):
        segments = [TranscriptionSegment(0, 900, 'Bom dia igreja', 'segment')]
        cues = TranscriptionService.group_for_subtitles(segments, block_boundaries_ms=[400])
        self.assertEqual(cues, segments)

    def test_subtitles_exclude_items_that_touch_an_intact_block(self):
        items = [
            TranscriptionSegment(100, 700, 'Legenda normal', 'word'),
            TranscriptionSegment(800, 1300, 'Bloco intacto', 'word'),
            TranscriptionSegment(1450, 1800, 'Também normal', 'word'),
        ]

        filtered = TranscriptionService.exclude_protected_ranges(items, [
            {'start_ms': 750, 'end_ms': 1400},
        ])

        self.assertEqual([item.text for item in filtered], ['Legenda normal', 'Também normal'])

    def test_block_boundaries_ms_reads_configured_block_ranges(self):
        job = SimpleNamespace(project=SimpleNamespace(configuration={
            'block_ranges': [
                {'start_ms': 0, 'end_ms': 1000},
                {'start_ms': 1000, 'end_ms': 2500},
                {'start_ms': 2500, 'end_ms': 2500},
            ],
        }))
        boundaries = ExternalMediaPipeline._block_boundaries_ms(job)
        self.assertEqual(boundaries, [1000, 2500])

    def test_block_boundaries_ms_returns_empty_without_project(self):
        job = SimpleNamespace(project=None)
        self.assertEqual(ExternalMediaPipeline._block_boundaries_ms(job), [])


class ProtectedFileResponseRangeTests(SimpleTestCase):
    def test_preview_supports_http_range_for_video_seeking(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = FileSystemStorage(location=directory)
            payload = b'0123456789ABCDEFGHIJ'
            name = storage.save('seek-test.bin', ContentFile(payload))

            class FakeFieldFile:
                def __init__(self):
                    self.name = name
                    self.storage = storage
                    self.size = len(payload)

                def open(self, mode='rb'):
                    return storage.open(self.name, mode)

            factory = RequestFactory()
            response = protected_file_response(
                factory.get('/asset?preview=1', HTTP_RANGE='bytes=4-8'),
                FakeFieldFile(),
            )
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response['Accept-Ranges'], 'bytes')
            self.assertEqual(response['Content-Range'], 'bytes 4-8/20')
            self.assertEqual(b''.join(response.streaming_content), b'45678')

            full = protected_file_response(factory.get('/asset?preview=1'), FakeFieldFile())
            self.assertEqual(full.status_code, 200)
            self.assertEqual(full['Accept-Ranges'], 'bytes')


class FFmpegRenderSmokeTests(SimpleTestCase):
    @staticmethod
    def _silent_wav(path, duration_ms=4000):
        with wave.open(str(path), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(16000)
            stream.writeframes(b'\x00\x00' * round(16000 * duration_ms / 1000))

    @staticmethod
    def _wav_with_voice(path, duration_ms, voice_ranges):
        """Build a small PCM fixture with clearly detectable speech-like activity."""
        rate = 16000
        samples = []
        for index in range(round(rate * duration_ms / 1000)):
            time_ms = index * 1000 / rate
            active = any(start <= time_ms < end for start, end in voice_ranges)
            value = round(12000 * math.sin(2 * math.pi * 220 * index / rate)) if active else 0
            samples.append(value)
        with wave.open(str(path), 'wb') as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(rate)
            stream.writeframes(struct.pack(f'<{len(samples)}h', *samples))

    def test_speech_edit_reduces_medium_silence_but_keeps_a_natural_pause(self):
        with tempfile.TemporaryDirectory() as directory:
            wav_path = Path(directory) / 'analysis.wav'
            self._silent_wav(wav_path)
            words = [
                TranscriptionSegment(100, 500, 'Olá', 'word'),
                TranscriptionSegment(1700, 2100, 'pessoal', 'word'),
            ]
            plan = SpeechEditAnalyzer().analyze(
                words, wav_path, 3000, remove_fillers=False,
                configuration={'profile': 'balanced'},
            )
        self.assertEqual(plan.silence_count, 1)
        self.assertEqual(plan.cuts[0].duration_ms, 560)
        self.assertEqual(1700 - 500 - plan.cuts[0].duration_ms, 640)

    def test_proxy_trim_uses_accurate_output_seeking(self):
        runner = Mock()
        service = VideoAssemblyService(runner=runner)
        service._video_dimensions = Mock(return_value=(1920, 1080))

        service.create_proxy(Path('/tmp/take.mov'), Path('/tmp/proxy.mp4'), 1250, 8250)

        command = runner.run.call_args.args[0]
        self.assertLess(command.index('-i'), command.index('-ss'))
        self.assertEqual(command[command.index('-ss') + 1], '1.250')
        self.assertEqual(command[command.index('-t') + 1], '7.000')

    def test_speech_edit_removes_leading_breath_before_each_take(self):
        with tempfile.TemporaryDirectory() as directory:
            wav_path = Path(directory) / 'analysis.wav'
            self._wav_with_voice(wav_path, 3000, [(700, 1200), (2000, 2500)])
            words = [
                TranscriptionSegment(900, 1200, 'Olá', 'word'),
                TranscriptionSegment(2200, 2500, 'pessoal', 'word'),
            ]
            plan = SpeechEditAnalyzer().analyze(
                words, wav_path, 3000, remove_fillers=False,
                configuration={'profile': 'balanced', 'trim_take_lead_silence': True},
                block_ranges=[
                    {'start_ms': 0, 'end_ms': 1500},
                    {'start_ms': 1500, 'end_ms': 3000},
                ],
            )

        self.assertIn(SpeechCut(0, 340, 'silence'), plan.cuts)
        self.assertTrue(
            any(cut.start_ms == 1500 for cut in plan.cuts),
        )

    def test_speech_edit_preserves_a_take_start_when_voice_onset_is_uncertain(self):
        with tempfile.TemporaryDirectory() as directory:
            wav_path = Path(directory) / 'analysis.wav'
            self._silent_wav(wav_path, 1500)
            plan = SpeechEditAnalyzer().analyze(
                [TranscriptionSegment(900, 1200, 'Olá', 'word')],
                wav_path,
                1500,
                remove_fillers=False,
                configuration={'profile': 'balanced'},
                block_ranges=[{'start_ms': 0, 'end_ms': 1500}],
            )

        self.assertFalse(any(cut.start_ms == 0 for cut in plan.cuts))

    def test_speech_edit_never_uses_a_cross_take_gap_to_cut_the_next_take(self):
        with tempfile.TemporaryDirectory() as directory:
            wav_path = Path(directory) / 'analysis.wav'
            self._silent_wav(wav_path, 2000)
            plan = SpeechEditAnalyzer().analyze(
                [
                    TranscriptionSegment(100, 400, 'Fim', 'word'),
                    TranscriptionSegment(1200, 1500, 'Início', 'word'),
                ],
                wav_path,
                2000,
                remove_fillers=False,
                configuration={'profile': 'balanced'},
                block_ranges=[
                    {'start_ms': 0, 'end_ms': 700},
                    {'start_ms': 700, 'end_ms': 2000},
                ],
            )

        self.assertEqual(plan.silence_count, 0)

    def test_speech_edit_keeps_an_internal_gap_when_it_contains_voice_activity(self):
        with tempfile.TemporaryDirectory() as directory:
            wav_path = Path(directory) / 'analysis.wav'
            self._wav_with_voice(wav_path, 2600, [(100, 500), (1000, 1200), (2000, 2400)])
            plan = SpeechEditAnalyzer().analyze(
                [
                    TranscriptionSegment(100, 500, 'Primeira', 'word'),
                    TranscriptionSegment(2000, 2400, 'segunda', 'word'),
                ],
                wav_path,
                2600,
                remove_fillers=False,
                configuration={'profile': 'balanced'},
            )

        self.assertEqual(plan.silence_count, 0)

    def test_speech_edit_removes_verified_silence_after_the_last_phrase_of_a_take(self):
        with tempfile.TemporaryDirectory() as directory:
            wav_path = Path(directory) / 'analysis.wav'
            self._silent_wav(wav_path)
            plan = SpeechEditAnalyzer().analyze(
                [TranscriptionSegment(200, 600, 'Encerramos', 'word')],
                wav_path,
                2000,
                remove_fillers=False,
                configuration={'profile': 'balanced'},
                block_ranges=[{'start_ms': 0, 'end_ms': 2000}],
            )

        # A 350 ms safety margin remains after the word; the residual room tone
        # at the end of the take is removed.
        self.assertIn(SpeechCut(950, 2000, 'silence'), plan.cuts)

    def test_speech_edit_keeps_a_pause_without_safe_margins(self):
        with tempfile.TemporaryDirectory() as directory:
            wav_path = Path(directory) / 'analysis.wav'
            self._silent_wav(wav_path)
            words = [
                TranscriptionSegment(100, 400, 'Uma', 'word'),
                TranscriptionSegment(900, 1200, 'frase', 'word'),
            ]
            plan = SpeechEditAnalyzer().analyze(
                words, wav_path, 1600, remove_fillers=False,
                configuration={'profile': 'balanced'},
            )

        self.assertEqual(plan.silence_count, 0)

    def test_speech_edit_only_removes_an_isolated_filler(self):
        with tempfile.TemporaryDirectory() as directory:
            wav_path = Path(directory) / 'analysis.wav'
            self._silent_wav(wav_path)
            words = [
                TranscriptionSegment(100, 400, 'Olá', 'word'),
                TranscriptionSegment(800, 1000, 'hum', 'word'),
                TranscriptionSegment(1400, 1800, 'pessoal', 'word'),
                TranscriptionSegment(1850, 2000, 'eh', 'word'),
                TranscriptionSegment(2050, 2400, 'vamos', 'word'),
            ]
            plan = SpeechEditAnalyzer().analyze(
                words, wav_path, 3000, remove_silence=False,
                configuration={'profile': 'balanced'},
            )
        self.assertEqual(plan.filler_count, 1)
        self.assertEqual(plan.cuts[0].label, 'hum')
        self.assertNotIn('hum', [item.text for item in plan.remap_words(words)])
        self.assertIn('eh', [item.text for item in plan.remap_words(words)])

    def test_speech_edit_removes_isolated_filler_with_short_verified_pauses(self):
        with tempfile.TemporaryDirectory() as directory:
            wav_path = Path(directory) / 'analysis.wav'
            self._silent_wav(wav_path)
            words = [
                TranscriptionSegment(100, 450, 'Olá', 'word'),
                TranscriptionSegment(570, 720, 'hum', 'word'),
                TranscriptionSegment(840, 1200, 'pessoal', 'word'),
            ]
            plan = SpeechEditAnalyzer().analyze(
                words, wav_path, 2000, remove_silence=False,
                configuration={'profile': 'balanced'},
            )

        self.assertEqual(plan.filler_count, 1)
        self.assertEqual(plan.cuts[0].label, 'hum')

    def test_speech_edit_recognizes_stretched_filler_spellings(self):
        with tempfile.TemporaryDirectory() as directory:
            wav_path = Path(directory) / 'analysis.wav'
            self._silent_wav(wav_path)
            words = [
                TranscriptionSegment(100, 400, 'Olá', 'word'),
                TranscriptionSegment(650, 900, 'eee', 'word'),
                TranscriptionSegment(1150, 1450, 'pessoal', 'word'),
                TranscriptionSegment(1700, 1950, 'hamm', 'word'),
                TranscriptionSegment(2200, 2500, 'vamos', 'word'),
            ]
            plan = SpeechEditAnalyzer().analyze(
                words,
                wav_path,
                3000,
                remove_silence=False,
                configuration={'profile': 'balanced', 'filler_words': ['eh', 'hum']},
            )

        self.assertEqual(plan.filler_count, 2)
        self.assertEqual([cut.label for cut in plan.cuts], ['eee', 'hamm'])

    def test_speech_edit_keeps_adjacent_filler_even_when_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            wav_path = Path(directory) / 'analysis.wav'
            self._silent_wav(wav_path)
            words = [
                TranscriptionSegment(100, 350, 'É', 'word'),
                TranscriptionSegment(420, 780, 'verdade', 'word'),
            ]
            plan = SpeechEditAnalyzer().analyze(
                words, wav_path, 1200, remove_silence=False,
                configuration={'profile': 'balanced', 'filler_words': ['é']},
            )

        self.assertEqual(plan.filler_count, 0)

    def test_background_voice_removal_cuts_only_clearly_quieter_utterances(self):
        service = BackgroundVoiceRemovalService(Mock())
        utterances = [
            QuietUtterance(0, 800, -22),
            QuietUtterance(1200, 1700, -36),
            QuietUtterance(2100, 2900, -21),
        ]
        cuts = service._quiet_cuts(utterances, [(0, 4000)], -21, 4000)

        self.assertEqual(len(cuts), 1)
        self.assertEqual((cuts[0].start_ms, cuts[0].end_ms), (1165, 2020))

    def test_background_voice_removal_splits_when_the_featured_speaker_returns(self):
        service = BackgroundVoiceRemovalService(Mock())
        activity = Mock()
        activity.average_db.side_effect = lambda start_ms, _end_ms: -42 if start_ms < 800 else -24
        words = [
            TranscriptionSegment(0, 300, 'Pergunta', 'word'),
            TranscriptionSegment(400, 700, 'do entrevistador', 'word'),
            TranscriptionSegment(800, 1100, 'Resposta', 'word'),
            TranscriptionSegment(1200, 1500, 'final', 'word'),
        ]

        utterances = service._utterances(words, [(0, 2000)], activity)
        cuts = service._quiet_cuts(utterances, [(0, 2000)], reference_db=-24, duration_ms=2000)

        self.assertEqual([(item.start_ms, item.end_ms) for item in utterances], [(0, 700), (800, 1500)])
        self.assertEqual([(cut.start_ms, cut.end_ms) for cut in cuts], [(0, 720)])

    def test_background_voice_removal_can_remove_a_long_clear_interviewer_turn(self):
        service = BackgroundVoiceRemovalService(Mock())
        utterances = [
            QuietUtterance(0, 4500, -42),
            QuietUtterance(5000, 5800, -24),
        ]

        cuts = service._quiet_cuts(utterances, [(0, 6000)], reference_db=-24, duration_ms=6000)

        self.assertEqual([(cut.start_ms, cut.end_ms) for cut in cuts], [(0, 4920)])

    def test_background_voice_removal_keeps_the_last_utterance_in_a_testimony(self):
        service = BackgroundVoiceRemovalService(Mock())
        utterances = [
            QuietUtterance(0, 700, -42),
            QuietUtterance(800, 1400, -24),
            QuietUtterance(1600, 2200, -42),
        ]

        cuts = service._quiet_cuts(utterances, [(0, 2400)], reference_db=-24, duration_ms=2400)

        self.assertEqual([(cut.start_ms, cut.end_ms) for cut in cuts], [(0, 720)])

    def test_community_diarization_removes_interviewer_between_featured_turns(self):
        service = BackgroundVoiceRemovalService(Mock(), diarizer=Mock())
        activity = Mock()
        activity.average_db.side_effect = lambda start, _end: -22 if start in {0, 2200} else -40
        turns = [
            SpeakerTurn(0, 900, 'speaker-a'),
            SpeakerTurn(1000, 2100, 'speaker-b'),
            SpeakerTurn(2200, 3200, 'speaker-a'),
        ]

        cuts, reference_db = service._speaker_cuts(turns, [(0, 3400)], activity, 3400)

        self.assertEqual(reference_db, -22)
        self.assertEqual([(cut.start_ms, cut.end_ms) for cut in cuts], [(980, 2120)])

    def test_protected_range_only_trims_the_overlapping_part_of_a_silence_cut(self):
        plan = SpeechEditPlan((SpeechCut(800, 1200, 'silence'),), 2000)

        safe = plan.without_ranges([{'start_ms': 1000, 'end_ms': 1500}])

        self.assertEqual(safe.cuts, (SpeechCut(800, 1000, 'silence'),))

    def test_speech_edit_detects_an_untranscribed_voiced_hesitation(self):
        activity = Mock()
        activity.voiced_regions.return_value = [(520, 850)]
        words = [
            TranscriptionSegment(100, 400, 'Eu', 'word'),
            TranscriptionSegment(1000, 1300, 'fui', 'word'),
        ]

        cuts = SpeechEditAnalyzer._untranscribed_filler_cuts(words, activity)

        self.assertEqual(cuts, [SpeechCut(490, 880, 'filler', 'hesitação')])

    def test_speech_edit_remaps_words_after_cut_and_crossfade(self):
        plan = SpeechEditPlan((SpeechCut(500, 1000, 'silence'),), 2000, crossfade_ms=40)
        remapped = plan.remap_words([TranscriptionSegment(1200, 1500, 'Depois', 'word')])
        self.assertEqual(remapped[0].start_ms, 700)
        self.assertEqual(remapped[0].end_ms, 1000)

    def test_speech_edit_maps_post_edit_timestamps_back_to_source(self):
        plan = SpeechEditPlan((SpeechCut(500, 1000, 'background_voice'),), 2000, crossfade_ms=40)
        self.assertEqual(plan.source_time(300), 300)
        self.assertEqual(plan.source_time(700), 1200)
        self.assertEqual(plan.source_time(0), 0)

    def test_remap_time_shifts_only_timestamps_after_the_cut(self):
        plan = SpeechEditPlan((SpeechCut(500, 1000, 'silence'),), 2000, crossfade_ms=40)
        self.assertEqual(plan.remap_time(300), 300)
        self.assertEqual(plan.remap_time(1200), 700)
        self.assertEqual(plan.remap_time(0), 0)

    def test_speech_edit_plan_normalizes_overlapping_cuts_before_remapping(self):
        plan = SpeechEditPlan.normalized([
            SpeechCut(500, 1200, 'silence'),
            SpeechCut(900, 1500, 'background_voice'),
        ], 3000)

        self.assertEqual(plan.cuts, (SpeechCut(500, 1500, 'background_voice'),))
        self.assertEqual(plan.saved_ms, 1000)
        self.assertEqual(plan.remap_time(2000), 1000)

    def test_proxy_timeline_validation_rejects_large_duration_drift(self):
        configuration = {
            'analysis_source_duration_ms': 10000,
            'analysis_source_block_ranges': [
                {'block_key': 'testimony', 'block_name': 'Testemunho', 'start_ms': 0, 'end_ms': 10000},
            ],
        }

        with self.assertRaisesMessage(ExternalMediaError, 'perderam sincronismo'):
            ExternalMediaProjectPipeline._validate_analysis_timeline(
                configuration,
                12000,
                [{'block_key': 'testimony', 'start_ms': 0, 'end_ms': 12000}],
            )

    def test_quality_control_rejects_subtitles_over_intact_blocks(self):
        cue = SimpleNamespace(start_ms=900, end_ms=1400, cue_index=3)
        track = SimpleNamespace(language='pt', cues=SimpleNamespace(all=lambda: [cue]))

        report = MediaQualityService.validate_subtitles(
            [track], [{'start_ms': 1000, 'end_ms': 2000}],
        )

        self.assertFalse(report.ok)
        self.assertEqual(report.metrics['protected_subtitle_overlaps'], [{'language': 'pt', 'cue': 3}])

    def test_quality_control_validates_real_audio_and_video_streams(self):
        runner = FFmpegRunner()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'quality.mp4'
            runner.run([
                'ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=320x240:d=2',
                '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-shortest',
                '-c:v', 'libx264', '-c:a', 'aac', str(output),
            ])
            report = MediaQualityService(runner).validate_media(
                output, expected_duration_ms=2000, deep_audio=True,
            )

        self.assertTrue(report.ok, report.errors)
        self.assertTrue(report.metrics['has_video'])
        self.assertTrue(report.metrics['has_audio'])

    def test_speech_edit_ffmpeg_applies_lightweight_join(self):
        runner = FFmpegRunner()
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            source = workdir / 'source.mp4'
            output = workdir / 'edited.mp4'
            runner.run([
                'ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=320x240:d=2',
                '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-shortest',
                '-c:v', 'libx264', '-c:a', 'aac', str(source),
            ])
            SpeechEditService(runner).apply(
                source, output, SpeechEditPlan((SpeechCut(700, 1200, 'silence'),), 2000, 40),
            )
            output_exists = output.exists()
            duration = SpeechEditService(runner).duration_ms(output)
        self.assertTrue(output_exists)
        self.assertLess(duration, 1600)
        self.assertGreater(duration, 1450)

    def test_speech_edit_keeps_audio_and_video_aligned_after_many_cuts(self):
        runner = FFmpegRunner()
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            source = workdir / 'source.mp4'
            output = workdir / 'edited.mp4'
            runner.run([
                'ffmpeg', '-y', '-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=24:duration=8',
                '-f', 'lavfi', '-i', 'sine=frequency=440:duration=8', '-shortest',
                '-c:v', 'libx264', '-c:a', 'aac', str(source),
            ])
            cuts = tuple(SpeechCut(start, start + 120, 'silence') for start in range(800, 7200, 800))
            SpeechEditService(runner).apply(source, output, SpeechEditPlan(cuts, 8000, 40))
            output_exists = output.exists()
            duration = SpeechEditService(runner).duration_ms(output)
            stream_info = json.loads(runner.run([
                'ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,duration',
                '-of', 'json', str(output),
            ]))
            stream_durations = {
                item['codec_type']: float(item['duration'])
                for item in stream_info['streams']
                if item.get('duration')
            }
        self.assertTrue(output_exists)
        self.assertLess(duration, 7300)
        self.assertLess(abs(stream_durations['video'] - stream_durations['audio']), 0.08)

    def test_cover_filters_fill_the_canvas_without_padding(self):
        preset = SimpleNamespace(width=3840, height=1200)
        filters = RenderService.build_video_filters(
            preset, '/tmp/subtitles.ass', VideoMetadata(),
        )
        self.assertIn(
            'scale=3840:1200:force_original_aspect_ratio=increase:flags=lanczos',
            filters,
        )
        self.assertIn('crop=3840:1200:(iw-ow)/2:(ih-oh)/2', filters)
        self.assertFalse(any(item.startswith('pad=') for item in filters))

    def test_auto_reframe_plan_interpolates_crop_position(self):
        plan = AutoReframePlan(
            crop_width=1080,
            crop_height=1920,
            keyframes=(
                ReframeKeyframe(0.0, 10.0, 20.0),
                ReframeKeyframe(1.0, 30.0, 40.0),
            ),
        )
        filters = plan.ffmpeg_filters(1080, 1920)
        self.assertTrue(filters[0].startswith("crop=1080:1920:x='if(lt(t\\,1.000)"))
        self.assertEqual(filters[1], 'scale=1080:1920:flags=lanczos')

    def test_auto_reframe_plan_can_scale_from_proxy_to_original(self):
        plan = AutoReframePlan(
            crop_width=480,
            crop_height=270,
            keyframes=(ReframeKeyframe(0.0, 20.0, 30.0),),
        )
        scaled = plan.scaled(4.0, 4.0)
        self.assertEqual((scaled.crop_width, scaled.crop_height), (1920, 1080))
        self.assertEqual((scaled.keyframes[0].x, scaled.keyframes[0].y), (80.0, 120.0))

    def test_cover_crop_geometry_supports_ultrawide_and_vertical_outputs(self):
        self.assertEqual(
            AutoReframeService.cover_crop_size(1920, 1080, 3840, 1200),
            (1920, 600),
        )
        self.assertEqual(
            AutoReframeService.cover_crop_size(1920, 1080, 1080, 1920),
            (608, 1080),
        )

    def test_auto_reframe_never_zooms_in_to_center_on_wide_ratio_when_person_is_tall(self):
        # 16:5 banner output from a 16:9 source: cover_crop_size uses the full source width
        # (1920, 600), leaving zero room to pan horizontally. A "centering zoom" that shrinks
        # crop_width to gain pan room was tried before and reverted: since width/height are
        # locked to the target ratio, that shrink also crops the vertical framing by the same
        # factor, which cuts off the head/chin on a close/medium shot like this one (tall
        # relative to the very wide 16:5 ratio). Keeping the person fully framed must win over
        # perfect horizontal centering, so no extra zoom should be applied here at all.
        service = AutoReframeService(priority='face', safe_margin=0.15, top_margin=0.18, smoothing=1.0)
        source_width, source_height = 1920, 1080
        cover_width, cover_height = AutoReframeService.cover_crop_size(source_width, source_height, 3840, 1200)
        target_ratio = 3840 / 1200
        # Face box left-of-center in the source frame.
        observations = [(0.0, (346.0, 273.6, 874.0, 840.0))]
        crop_width, crop_height = service._smart_crop_size(
            observations, cover_width, cover_height, source_width, source_height, target_ratio,
        )
        self.assertEqual((crop_width, crop_height), (cover_width, cover_height))

    def test_auto_reframe_zooms_in_on_small_person_without_centering_pressure(self):
        # A small, roughly centered person (box aspect close to the target ratio) can still be
        # zoomed in on normally — this isn't the "centering zoom" case, just the regular
        # size-driven crop, and it must keep working.
        service = AutoReframeService(priority='body', safe_margin=0.15, top_margin=0.12, smoothing=1.0)
        source_width, source_height = 1920, 1080
        cover_width, cover_height = AutoReframeService.cover_crop_size(source_width, source_height, 1080, 1920)
        target_ratio = 1080 / 1920
        observations = [(0.0, (860.0, 300.0, 1060.0, 780.0))]
        crop_width, crop_height = service._smart_crop_size(
            observations, cover_width, cover_height, source_width, source_height, target_ratio,
        )
        self.assertLess(crop_width, cover_width)
        self.assertLess(crop_height, cover_height)
        box_height_with_margin = (780.0 - 300.0) * 1.3
        self.assertGreaterEqual(crop_height + 1, box_height_with_margin)

    def test_auto_reframe_vertical_crop_preserves_headroom(self):
        service = AutoReframeService(priority='face', safe_margin=0.15, top_margin=0.18, smoothing=1.0)
        centered_y = ((290 + 850) / 2) - (600 / 2)
        target_y = service._target_crop_y(top=290, bottom=850, crop_height=600, max_y=480)
        self.assertGreater(target_y, centered_y)
        self.assertEqual(round(290 - target_y), 15)

    def test_auto_reframe_vertical_crop_keeps_head_when_person_is_taller_than_crop(self):
        service = AutoReframeService(priority='face', safe_margin=0.15, top_margin=0.12, smoothing=1.0)
        target_y = service._target_crop_y(top=290, bottom=950, crop_height=600, max_y=480)
        # Older templates persisted larger values; face framing caps them at 2.5%
        # to avoid excessive empty space above the head.
        self.assertEqual(round(target_y), round(290 - (600 * 0.025)))

    def test_auto_reframe_normalizes_body_box_with_head_padding_and_minimum_height(self):
        service = AutoReframeService(priority='face')
        left, top, right, bottom = service._normalize_detection_box(
            700.0, 400.0, 980.0, 780.0, 1920.0, 1080.0,
        )
        self.assertLess(top, 400.0)
        self.assertGreaterEqual(bottom - top, 1080 * 0.48 - 1)

    def test_auto_reframe_face_box_keeps_top_close_to_head(self):
        left, top, width, height = AutoReframeService._face_priority_box(800, 260, 120, 120)
        self.assertEqual(round(left), 596)
        self.assertEqual(round(top), 224)
        self.assertEqual(round(width), 528)
        self.assertEqual(round(height), 540)

    def test_auto_reframe_uses_small_tracking_zoom_only_for_persistent_lateral_offset(self):
        centered = [(0.0, (760, 200, 1160, 900)), (1.0, (770, 200, 1170, 900))]
        off_center = [(0.0, (1180, 200, 1580, 900)), (1.0, (1190, 200, 1590, 900))]
        self.assertFalse(AutoReframeService._needs_tracking_pan(centered, 1920))
        self.assertTrue(AutoReframeService._needs_tracking_pan(off_center, 1920))

    def test_auto_reframe_does_not_pan_for_a_transient_hand_detection(self):
        # The last, very wide box mimics a detector including an extended arm.
        # It must not turn a normally centred talking-head take into a moving crop.
        observations = [
            (0.0, (760, 250, 1160, 900)),
            (1.0, (770, 250, 1170, 900)),
            (2.0, (740, 250, 1140, 900)),
            (3.0, (1120, 250, 1760, 900)),
        ]
        self.assertFalse(AutoReframeService._needs_tracking_pan(observations, 1920))

    def test_auto_reframe_face_plan_keeps_one_stable_position_and_zoom(self):
        service = AutoReframeService(priority='face')
        keyframes = service._stable_face_keyframes(
            observations=[
                (0.0, (720, 290, 1220, 850)),
                (1.0, (740, 300, 1240, 860)),
                # Detector outlier caused by a gesture.
                (2.0, (1060, 300, 1720, 860)),
                (3.0, (730, 295, 1230, 855)),
            ],
            crop_width=960,
            crop_height=600,
            source_width=1920,
            source_height=1080,
        )
        self.assertEqual(len(keyframes), 1)
        self.assertEqual(keyframes[0].time_seconds, 0.0)
        self.assertAlmostEqual(keyframes[0].x, 480.0)

    def test_auto_reframe_vertical_crop_reduces_excessive_headroom(self):
        service = AutoReframeService(priority='face', safe_margin=0.15, top_margin=0.18, smoothing=1.0)
        centered_y = ((260 + 560) / 2) - (600 / 2)
        target_y = service._target_crop_y(top=260, bottom=560, crop_height=600, max_y=480)
        self.assertGreater(target_y, centered_y)
        self.assertEqual(round(260 - target_y), 15)

    def test_auto_reframe_face_detection_tries_contrast_fallbacks(self):
        gray = SimpleNamespace(shape=(720, 1280))
        detector = Mock()
        detector.detectMultiScale.side_effect = [[], [(100, 120, 80, 80)]]
        fake_cv2 = SimpleNamespace(
            createCLAHE=lambda clipLimit, tileGridSize: SimpleNamespace(apply=lambda value: value),
            equalizeHist=lambda value: value,
            bilateralFilter=lambda value, _a, _b, _c: value,
        )
        faces = AutoReframeService._detect_faces(gray, [detector], fake_cv2)
        self.assertEqual(len(faces), 1)
        self.assertGreater(detector.detectMultiScale.call_count, 1)

    def test_auto_reframe_keyframes_apply_top_margin_before_smoothing(self):
        service = AutoReframeService(priority='face', safe_margin=0.15, top_margin=0.18, smoothing=1.0)
        keyframes = service._smooth_keyframes(
            observations=[(0.0, (700, 290, 1200, 850))],
            crop_width=1920,
            crop_height=600,
            source_width=1920,
            source_height=1080,
        )
        self.assertEqual(len(keyframes), 1)
        self.assertEqual(round(keyframes[0].y), 275)

    def test_auto_reframe_locks_vertical_position_for_face_tracking(self):
        service = AutoReframeService(priority='face', safe_margin=0.15, top_margin=0.18, smoothing=1.0)
        keyframes = service._smooth_keyframes(
            observations=[
                (0.0, (700, 290, 1200, 850)),
                (1.0, (760, 360, 1260, 920)),
                (2.0, (820, 300, 1320, 860)),
            ],
            crop_width=960,
            crop_height=600,
            source_width=1920,
            source_height=1080,
        )
        self.assertGreater(len(keyframes), 1)
        self.assertEqual(len({round(keyframe.y) for keyframe in keyframes}), 1)

    def test_auto_reframe_horizontal_tracking_is_more_responsive_than_base_smoothing(self):
        responsive = AutoReframeService(
            priority='face',
            safe_margin=0.15,
            top_margin=0.18,
            smoothing=0.18,
            horizontal_smoothing=0.65,
        )
        stable = AutoReframeService(
            priority='face',
            safe_margin=0.15,
            top_margin=0.18,
            smoothing=0.18,
        )
        observations = [
            (0.0, (100, 290, 300, 850)),
            (1.0, (700, 290, 900, 850)),
            (2.0, (760, 290, 960, 850)),
        ]
        responsive_keyframes = responsive._smooth_keyframes(
            observations=observations,
            crop_width=608,
            crop_height=1080,
            source_width=1920,
            source_height=1080,
        )
        stable_keyframes = stable._smooth_keyframes(
            observations=observations,
            crop_width=608,
            crop_height=1080,
            source_width=1920,
            source_height=1080,
        )
        self.assertGreater(
            responsive_keyframes[-1].x,
            stable_keyframes[-1].x,
        )

    def test_auto_reframe_horizontal_anchor_uses_torso_core(self):
        anchor = AutoReframeService._horizontal_anchor_x(100.0, 200.0, 500.0, 900.0)
        self.assertAlmostEqual(anchor, 300.0)
        self.assertGreater(anchor, 100.0 + ((500.0 - 100.0) * 0.24))
        self.assertLess(anchor, 500.0 - ((500.0 - 100.0) * 0.24))

    def test_auto_reframe_stable_body_box_keeps_center_on_face(self):
        left, top, width, height = AutoReframeService._stable_body_box_from_face(800, 260, 120, 120)
        self.assertAlmostEqual(left + (width / 2.0), 860.0)
        self.assertAlmostEqual(width, 336.0)

    def test_auto_reframe_detect_bodies_tries_contrast_fallback(self):
        service = AutoReframeService(priority='face')
        analyzed = Mock()
        body_detector = Mock()
        body_detector.detectMultiScale.side_effect = [([], None), ([(120, 80, 180, 360)], None)]
        fake_cv2 = SimpleNamespace(
            COLOR_BGR2GRAY='gray',
            cvtColor=lambda value, _code: Mock(shape=(720, 1280)),
            COLOR_GRAY2BGR='bgr',
            createCLAHE=lambda clipLimit, tileGridSize: SimpleNamespace(apply=lambda value: value),
        )
        boxes = service._detect_bodies(analyzed, body_detector, fake_cv2)
        self.assertEqual(len(boxes), 1)
        self.assertEqual(body_detector.detectMultiScale.call_count, 2)

    def test_auto_reframe_fast_start_centers_horizontally_within_opening_second(self):
        service = AutoReframeService(
            priority='face',
            safe_margin=0.15,
            top_margin=0.18,
            smoothing=0.18,
        )
        keyframes = service._smooth_keyframes(
            observations=[
                (0.0, (100, 290, 300, 850)),
                (0.33, (700, 290, 900, 850)),
                (0.66, (700, 290, 900, 850)),
                (1.0, (700, 290, 900, 850)),
            ],
            crop_width=608,
            crop_height=1080,
            source_width=1920,
            source_height=1080,
        )
        target_x = min(1920 - 608, max(0.0, 800 - 608 / 2))
        by_time = {round(keyframe.time_seconds, 2): round(keyframe.x) for keyframe in keyframes}
        self.assertIn(0.0, [round(keyframe.time_seconds, 2) for keyframe in keyframes])
        self.assertGreaterEqual(by_time[1.0], round(target_x * 0.60))

    def test_auto_reframe_expression_holds_first_keyframe_before_sample_time(self):
        plan = AutoReframePlan(
            crop_width=1080,
            crop_height=1920,
            keyframes=(
                ReframeKeyframe(0.5, 120.0, 40.0),
                ReframeKeyframe(2.0, 420.0, 40.0),
            ),
        )
        x_expression = plan._expression(plan.keyframes, 'x')
        self.assertIn('if(lt(t\\,0.500)', x_expression)
        self.assertIn('120.000', x_expression)

    def test_limit_keyframes_for_ffmpeg_caps_long_plans(self):
        keyframes = tuple(ReframeKeyframe(index, float(index), 0.0) for index in range(200))
        limited = limit_keyframes_for_ffmpeg(keyframes)
        self.assertLessEqual(len(limited), 48)
        self.assertEqual(limited[0], keyframes[0])
        self.assertEqual(limited[-1], keyframes[-1])

    def test_auto_reframe_ffmpeg_filters_caps_expression_size_for_long_clips(self):
        keyframes = tuple(
            ReframeKeyframe(index / 30.0, 50.0 + (index % 7), 10.0 + (index % 5))
            for index in range(3000)
        )
        plan = AutoReframePlan(crop_width=734, crop_height=228, keyframes=keyframes)
        filters = plan.ffmpeg_filters(854, 266)
        crop_filter = filters[0]
        # Each axis gets at most len(keyframes)-1 nested branches; both x and y live in one string.
        self.assertLessEqual(crop_filter.count('if(lt(t\\,'), 96)
        self.assertIn("crop=734:228", crop_filter)

    def test_video_assembly_normalizes_and_concatenates_two_clips(self):
        runner = FFmpegRunner()
        preset = SimpleNamespace(width=320, height=240)
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            sources = []
            for index, color in enumerate(('red', 'blue')):
                source = workdir / f'source_{index}.mp4'
                runner.run([
                    'ffmpeg', '-y', '-f', 'lavfi', '-i', f'color=c={color}:s=160x120:d=0.3',
                    '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-shortest',
                    '-c:v', 'libx264', '-c:a', 'aac', str(source),
                ])
                sources.append(source)
            output = workdir / 'assembled.mp4'
            service = VideoAssemblyService(runner=runner)
            service.assemble(sources, output, preset, workdir)
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 0)
            # Every clip (not just "manter intacto" ones) must have a timeline range recorded,
            # so subtitles generated later can be kept from bleeding across block boundaries.
            self.assertEqual(len(service.last_block_ranges), 2)
            self.assertEqual(service.last_block_ranges[0]['start_ms'], 0)
            self.assertEqual(service.last_block_ranges[0]['end_ms'], service.last_block_ranges[1]['start_ms'])
            self.assertGreater(service.last_block_ranges[1]['end_ms'], service.last_block_ranges[1]['start_ms'])

    def test_video_assembly_creates_lightweight_proxy(self):
        runner = FFmpegRunner()
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            source = workdir / 'source.mp4'
            proxy = workdir / 'proxy.mp4'
            runner.run([
                'ffmpeg', '-y', '-f', 'lavfi', '-i', 'testsrc=size=1280x720:rate=24:duration=0.3',
                '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-shortest',
                '-c:v', 'libx264', '-c:a', 'aac', str(source),
            ])
            service = VideoAssemblyService(runner=runner)
            service.create_proxy(source, proxy)
            width, height = service._video_dimensions(proxy)
            proxy_exists = proxy.exists()
        self.assertTrue(proxy_exists)
        self.assertLessEqual(width, 854)
        self.assertEqual(width % 2, 0)
        self.assertEqual(height % 2, 0)

    def test_video_assembly_blends_lut_at_configured_intensity(self):
        runner = FFmpegRunner()
        preset = SimpleNamespace(width=160, height=120)
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            source = workdir / 'source.mp4'
            lut = workdir / 'identity.cube'
            output = workdir / 'assembled.mp4'
            runner.run([
                'ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=red:s=160x120:d=0.3',
                '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-shortest',
                '-c:v', 'libx264', '-c:a', 'aac', str(source),
            ])
            lut.write_text(
                'LUT_3D_SIZE 2\n'
                '0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n',
                encoding='utf-8',
            )
            partial_lut = LUTService.with_intensity(lut, 50, workdir)
            self.assertNotEqual(partial_lut, lut)
            # A 50% identity LUT must remain identity; this also validates the
            # red/green/blue table ordering used when precomputing the cube.
            self.assertIn('1.00000000 0.00000000 0.00000000', partial_lut.read_text())
            VideoAssemblyService(runner=runner).assemble(
                [source], output, preset, workdir, lut_path=lut, lut_intensity=50,
            )
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 0)

    def test_video_assembly_reuses_saved_reframe_plan(self):
        runner = Mock()
        runner.run.side_effect = [
            '{"streams":[{}]}',
            '',
            '',
        ]
        source = Path('/tmp/source.mp4')
        output = Path('/tmp/output.mp4')
        workdir = Path('/tmp')
        preset = SimpleNamespace(width=3840, height=1200)
        plan = {
            'analysis_width': 960,
            'analysis_height': 540,
            'plan': AutoReframePlan(
                crop_width=960,
                crop_height=300,
                keyframes=(ReframeKeyframe(0.0, 20.0, 30.0),),
            ).as_dict(),
        }

        with patch.object(VideoAssemblyService, '_duration_ms', return_value=1000), \
                patch.object(VideoAssemblyService, '_has_audio', return_value=True), \
                patch.object(VideoAssemblyService, '_video_dimensions', return_value=(1920, 1080)), \
                patch.object(AutoReframeService, 'analyze') as analyze:
            service = VideoAssemblyService(runner=runner)
            service.assemble(
                [source], output, preset, workdir,
                auto_reframe_config={'priority': 'face'},
                reframe_plans=[plan],
            )

        analyze.assert_not_called()
        command = runner.run.call_args_list[1].args[0]
        filters = command[command.index('-vf') + 1]
        self.assertIn('crop=1920:600', filters)

    def test_video_assembly_skips_extra_processing_for_protected_source(self):
        runner = Mock()
        runner.run.side_effect = [
            '{"streams":[{}]}',
            '',
            '',
        ]
        source = AssemblySource(
            Path('/tmp/intro.mp4'),
            label='intro',
            block_id=7,
            block_key='intro',
            block_name='Intro',
            skip_extra_processing=True,
        )
        output = Path('/tmp/output.mp4')
        workdir = Path('/tmp')
        preset = SimpleNamespace(width=3840, height=1200)

        with patch.object(VideoAssemblyService, '_duration_ms', return_value=2300), \
                patch.object(VideoAssemblyService, '_has_audio', return_value=True), \
                patch.object(VideoAssemblyService, '_video_dimensions', return_value=(1920, 1080)), \
                patch.object(AutoReframeService, 'analyze') as analyze:
            service = VideoAssemblyService(runner=runner)
            service.assemble(
                [source], output, preset, workdir,
                lut_path=Path('/tmp/template.cube'), lut_intensity=50,
                auto_reframe_config={'priority': 'face'},
            )

        analyze.assert_not_called()
        self.assertEqual(service.last_reframe_plans, [None])
        self.assertEqual(service.last_protected_ranges[0]['block_key'], 'intro')
        self.assertEqual(service.last_protected_ranges[0]['start_ms'], 0)
        self.assertEqual(service.last_protected_ranges[0]['end_ms'], 2300)
        self.assertEqual(service.last_block_ranges[0]['block_key'], 'intro')
        self.assertEqual(service.last_block_ranges[0]['start_ms'], 0)
        self.assertEqual(service.last_block_ranges[0]['end_ms'], 2300)
        command = runner.run.call_args_list[1].args[0]
        filters = command[command.index('-vf') + 1]
        self.assertIn('force_original_aspect_ratio=decrease', filters)
        self.assertIn('pad=3840:1200', filters)
        self.assertNotIn('crop=3840:1200', filters)
        self.assertNotIn('lut3d=', filters)

    def test_render_command_uses_fast_h264_and_bt709_output(self):
        class CueList(list):
            def all(self):
                return self

        runner = Mock()
        runner.run.side_effect = [
            '{"streams":[{"color_space":"bt709","color_transfer":"bt709","color_primaries":"bt709"}]}',
            '',
        ]
        track = SimpleNamespace(
            language='pt',
            cues=CueList([SimpleNamespace(cue_index=1, start_ms=0, end_ms=1000, text='Legenda')]),
        )
        style = SimpleNamespace(
            font_name='Arial', font_size=22, primary_color='#FFFFFF',
            outline_color='#000000', outline_width=2, shadow=1,
            alignment=2, margin_bottom=20, max_characters=42, max_lines=2,
        )
        preset = SimpleNamespace(
            width=1920, height=1080, video_codec='libx264', audio_codec='copy',
            video_crf=23, extra_ffmpeg_args=[],
        )
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            RenderService(runner=runner).render(
                workdir / 'source.mp4', track, workdir / 'output.mp4', preset, style, workdir,
            )
        command = runner.run.call_args_list[1].args[0]
        self.assertIn('-preset', command)
        self.assertIn('superfast', command)
        self.assertIn('-crf', command)
        self.assertIn('23', command)
        self.assertIn('-pix_fmt', command)
        self.assertIn('yuv420p', command)
        self.assertIn('-colorspace', command)
        self.assertIn('bt709', command)
        self.assertIn('-c:a', command)
        self.assertIn('copy', command)

    def test_hdr_video_filters_include_tonemap_to_bt709(self):
        preset = SimpleNamespace(width=1920, height=1080)
        filters = RenderService.build_video_filters(
            preset,
            '/tmp/subtitles.ass',
            VideoMetadata(
                color_space='bt2020nc',
                color_transfer='smpte2084',
                color_primaries='bt2020',
            ),
        )

        self.assertIn('format=gbrpf32le', filters)
        self.assertIn('tonemap=tonemap=hable:desat=0:peak=100', filters)
        self.assertEqual(filters[-1], 'format=yuv420p')

    def test_audio_copy_falls_back_to_aac_when_ffmpeg_rejects_it(self):
        class CueList(list):
            def all(self):
                return self

        runner = Mock()
        runner.run.side_effect = [
            '{"streams":[{"color_space":"bt709","color_transfer":"bt709","color_primaries":"bt709"}]}',
            ExternalMediaError('copy failed'),
            '',
        ]
        track = SimpleNamespace(
            language='pt',
            cues=CueList([SimpleNamespace(cue_index=1, start_ms=0, end_ms=1000, text='Legenda')]),
        )
        style = SimpleNamespace(
            font_name='Arial', font_size=22, primary_color='#FFFFFF',
            outline_color='#000000', outline_width=2, shadow=1,
            alignment=2, margin_bottom=20, max_characters=42, max_lines=2,
        )
        preset = SimpleNamespace(
            width=1920, height=1080, video_codec='libx264', audio_codec='copy',
            video_crf=23, extra_ffmpeg_args=[],
        )
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            RenderService(runner=runner).render(
                workdir / 'source.mp4', track, workdir / 'output.mp4', preset, style, workdir,
            )

        retry_command = runner.run.call_args_list[2].args[0]
        self.assertEqual(retry_command[retry_command.index('-c:a') + 1], 'aac')

    def test_ffmpeg_burns_ass_subtitles(self):
        class CueList(list):
            def all(self):
                return self

        track = SimpleNamespace(
            language='en',
            cues=CueList([
                SimpleNamespace(cue_index=1, start_ms=100, end_ms=900, text='Natural English subtitle'),
            ]),
        )
        style = SimpleNamespace(
            font_name='Arial', font_size=22, primary_color='#FFFFFF',
            outline_color='#000000', outline_width=2, shadow=1,
            alignment=2, margin_bottom=20, max_characters=42, max_lines=2,
        )
        preset = SimpleNamespace(
            width=320, height=240, video_codec='libx264', audio_codec='aac',
            video_crf=28, extra_ffmpeg_args=[],
        )
        runner = FFmpegRunner()
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            source = workdir / 'source.mp4'
            output = workdir / 'output.mp4'
            runner.run([
                'ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=black:s=320x240:d=1',
                '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo', '-shortest',
                '-c:v', 'libx264', '-c:a', 'aac', str(source),
            ])
            RenderService(runner=runner).render(
                source, track, output, preset, style, workdir,
            )
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 0)


class AudioMixingUnitTests(SimpleTestCase):
    def test_group_speech_blocks_merges_close_intervals(self):
        blocks = group_speech_blocks([(0, 1000), (1300, 2000), (2100, 2400)], gap_threshold_ms=450)
        self.assertEqual(len(blocks), 1)
        self.assertEqual((blocks[0].start_ms, blocks[0].end_ms), (0, 2400))

    def test_group_speech_blocks_keeps_far_intervals_separate(self):
        blocks = group_speech_blocks([(0, 1000), (2500, 3000)], gap_threshold_ms=450)
        self.assertEqual(len(blocks), 2)
        self.assertEqual((blocks[1].start_ms, blocks[1].end_ms), (2500, 3000))

    def test_group_speech_blocks_ignores_invalid_intervals(self):
        blocks = group_speech_blocks([(500, 500), (None, 200), (100, 900)])
        self.assertEqual(len(blocks), 1)
        self.assertEqual((blocks[0].start_ms, blocks[0].end_ms), (100, 900))

    def test_clip_blocks_against_protected_ranges_trims_overlap(self):
        blocks = [SpeechBlock(0, 5000)]
        clipped = clip_blocks_against_protected_ranges(blocks, [(2000, 3000)])
        self.assertEqual([(item.start_ms, item.end_ms) for item in clipped], [(0, 2000), (3000, 5000)])

    def test_clip_blocks_against_protected_ranges_drops_fully_covered_block(self):
        blocks = [SpeechBlock(1000, 2000)]
        clipped = clip_blocks_against_protected_ranges(blocks, [(0, 3000)])
        self.assertEqual(clipped, [])

    def test_estimate_duck_db_uses_less_ducking_when_voice_is_already_louder(self):
        settings_ = DuckingSettings()
        soft_music = AudioMixingService.estimate_duck_db(-30.0, -14.0, settings_)
        dense_music = AudioMixingService.estimate_duck_db(-16.0, -18.0, settings_)
        self.assertLess(soft_music, dense_music)
        self.assertGreaterEqual(soft_music, settings_.min_duck_db)
        self.assertLessEqual(dense_music, settings_.max_duck_db)

    def test_estimate_duck_db_falls_back_to_base_when_unmeasured(self):
        settings_ = DuckingSettings()
        self.assertEqual(AudioMixingService.estimate_duck_db(None, -14.0, settings_), settings_.base_duck_db)

    def test_default_ducking_keeps_background_music_audible(self):
        settings_ = DuckingSettings()
        self.assertEqual(settings_.base_duck_db, 11.0)
        self.assertEqual(settings_.min_duck_db, 8.0)
        self.assertEqual(settings_.max_duck_db, 15.0)

    def test_dynamic_ducking_uses_voice_and_music_levels_after_template_gain(self):
        settings_ = DuckingSettings(target_voice_to_music_gap_db=14)
        quiet_voice = AudioMixingService.estimate_block_duck_db(-8, -24, 0.5, settings_)
        loud_voice = AudioMixingService.estimate_block_duck_db(-8, -12, 0.5, settings_)
        self.assertGreater(quiet_voice, loud_voice)
        self.assertGreaterEqual(quiet_voice, settings_.min_duck_db)

    def test_dynamic_ducking_envelope_keeps_individual_speech_block_levels(self):
        settings_ = DuckingSettings(attack_ms=100, release_ms=200)
        envelope = build_dynamic_ducking_envelope(
            [SpeechBlock(1000, 1800), SpeechBlock(3000, 3800)], 5000, [0.2, 0.5], settings_,
        )
        self.assertIn((1.1, 0.2), envelope)
        self.assertIn((3.1, 0.5), envelope)

    def test_build_ducking_envelope_holds_through_the_block_and_releases_after(self):
        settings_ = DuckingSettings(attack_ms=200, hold_ms=180, release_ms=400)
        blocks = [SpeechBlock(1000, 3000)]
        envelope = build_ducking_envelope(blocks, duration_ms=5000, duck_gain=0.4, settings_=settings_)
        times = [point[0] for point in envelope]
        self.assertEqual(times, sorted(times))
        before_speech = next(value for time, value in envelope if time == 1.0)
        self.assertEqual(before_speech, 1.0)
        during_hold = next(value for time, value in envelope if time == 3.0)
        self.assertAlmostEqual(during_hold, 0.4)
        after_release = next(value for time, value in envelope if time == 3.4)
        self.assertEqual(after_release, 1.0)

    def test_build_ducking_envelope_prevents_release_from_overlapping_next_block(self):
        # A 2s release would normally end at t=2.5, well past the next block's start (t=0.7);
        # the envelope must keep the music ducked through the short pause instead of
        # inserting a full-volume keyframe at the next block's start.
        settings_ = DuckingSettings(attack_ms=100, hold_ms=100, release_ms=2000)
        blocks = [SpeechBlock(0, 500), SpeechBlock(700, 1200)]
        envelope = build_ducking_envelope(blocks, duration_ms=2000, duck_gain=0.5, settings_=settings_)
        times = [point[0] for point in envelope]
        self.assertEqual(times, sorted(times))
        self.assertNotIn(2.5, times)
        matching = [value for time, value in envelope if abs(time - 0.7) < 0.01]
        self.assertTrue(matching)
        self.assertEqual(matching[0], 0.5)
        self.assertFalse(any(value > 0.5 for time, value in envelope if 0.5 <= time <= 1.2))

    def test_music_loop_plan_crossfades_short_track_until_video_end(self):
        filters, label, metrics = AudioMixingService._music_loop_plan(10.0, 3.0)
        self.assertEqual(label, '[music_looped]')
        self.assertEqual(metrics['music_loop_count'], 5)
        self.assertEqual(metrics['music_crossfade_s'], 0.75)
        self.assertIn('asplit=5', filters[0])
        self.assertEqual(sum('acrossfade=' in item for item in filters), 4)
        self.assertIn('atrim=duration=10.000', filters[-1])

    def test_build_spectral_windows_skips_when_no_cut(self):
        self.assertEqual(build_spectral_windows([SpeechBlock(0, 1000)], cut_db=0), [])

    def test_build_spectral_windows_caps_block_count(self):
        blocks = [SpeechBlock(index * 1000, index * 1000 + 500) for index in range(100)]
        windows = build_spectral_windows(blocks, cut_db=3)
        self.assertLessEqual(len(windows), 40)

    def test_ducking_settings_from_config_overrides_defaults(self):
        settings_ = DuckingSettings.from_config({'base_duck_db': 6, 'attack_ms': 300})
        self.assertEqual(settings_.base_duck_db, 6.0)
        self.assertEqual(settings_.attack_ms, 300)
        self.assertEqual(settings_.hold_ms, DuckingSettings().hold_ms)


class AudioMixingSmokeTests(SimpleTestCase):
    @staticmethod
    def _render_tone(path, frequency, duration_s, volume=1.0):
        runner = FFmpegRunner()
        runner.run([
            'ffmpeg', '-y', '-f', 'lavfi', '-i', f'color=c=blue:s=320x240:d={duration_s}',
            '-f', 'lavfi', '-i', f'sine=frequency={frequency}:duration={duration_s}',
            '-filter_complex', f'[1:a]volume={volume}[a]', '-map', '0:v', '-map', '[a]',
            '-shortest', '-c:v', 'libx264', '-c:a', 'aac', str(path),
        ])

    def test_mix_falls_back_to_flat_when_ducking_has_no_speech_blocks(self):
        service = AudioMixingService()
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            video = workdir / 'video.mp4'
            music = workdir / 'music.mp4'
            output = workdir / 'mixed.mp4'
            self._render_tone(video, 220, 3)
            self._render_tone(music, 440, 3)
            result = service.mix(
                video, music, output, music_volume=0.2, duration_ms=3000,
                speech_blocks=[], ducking_enabled=True,
            )
            self.assertTrue(output.exists())
            self.assertEqual(result.metrics['mode'], 'flat')

    def test_mix_adaptive_applies_ducking_and_reports_metrics(self):
        service = AudioMixingService()
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            video = workdir / 'video.mp4'
            music = workdir / 'music.mp4'
            output = workdir / 'mixed.mp4'
            self._render_tone(video, 220, 4, volume=1.0)
            self._render_tone(music, 440, 4, volume=1.0)
            result = service.mix(
                video, music, output, music_volume=0.5, duration_ms=4000,
                speech_blocks=[SpeechBlock(1000, 3000)], ducking_enabled=True,
            )
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 0)
            self.assertEqual(result.metrics['mode'], 'adaptive')
            self.assertEqual(result.metrics['speech_block_count'], 1)
            self.assertGreater(result.metrics['duck_db'], 0)

    def test_mix_crossfades_a_track_shorter_than_the_video(self):
        service = AudioMixingService()
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            video = workdir / 'video.mp4'
            music = workdir / 'music.mp4'
            output = workdir / 'mixed.mp4'
            self._render_tone(video, 220, 4)
            self._render_tone(music, 440, 1)
            result = service.mix(
                video, music, output, music_volume=0.5, duration_ms=4000,
                speech_blocks=[SpeechBlock(500, 3500)], ducking_enabled=True,
            )
            self.assertTrue(output.exists())
            self.assertGreater(result.metrics['music_loop_count'], 1)
            self.assertGreater(result.metrics['music_crossfade_s'], 0)

    def test_mix_keeps_music_ducked_through_a_short_speech_pause(self):
        service = AudioMixingService()
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            video = workdir / 'video.mp4'
            music = workdir / 'music.mp4'
            output = workdir / 'mixed.mp4'
            self._render_tone(video, 220, 4, volume=1.0)
            self._render_tone(music, 440, 4, volume=1.0)
            result = service.mix(
                video, music, output, music_volume=0.5, duration_ms=4000,
                speech_blocks=[SpeechBlock(200, 1000), SpeechBlock(2000, 3600)],
                ducking_enabled=True,
            )
        self.assertEqual(result.metrics['mode'], 'adaptive')
        self.assertEqual(result.metrics['speech_block_count'], 1)
        self.assertEqual(result.metrics['speech_gap_hold_ms'], 1800)

    def test_mix_spectral_ducking_produces_equalizer_filter(self):
        service = AudioMixingService()
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            video = workdir / 'video.mp4'
            music = workdir / 'music.mp4'
            output = workdir / 'mixed.mp4'
            self._render_tone(video, 220, 3, volume=1.0)
            self._render_tone(music, 1000, 3, volume=1.0)
            result = service.mix(
                video, music, output, music_volume=0.5, duration_ms=3000,
                speech_blocks=[SpeechBlock(500, 2000)], ducking_enabled=True, spectral_enabled=True,
            )
            self.assertTrue(output.exists())
            self.assertTrue(result.metrics['spectral_applied'])

    def test_protected_range_does_not_disable_music_ducking(self):
        blocks = clip_blocks_against_protected_ranges([SpeechBlock(0, 3000)], [(0, 3000)])
        self.assertEqual(blocks, [])
        service = AudioMixingService()
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            video = workdir / 'video.mp4'
            music = workdir / 'music.mp4'
            output = workdir / 'mixed.mp4'
            self._render_tone(video, 220, 3)
            self._render_tone(music, 440, 3)
            result = service.mix(
                video, music, output, music_volume=0.3, duration_ms=3000,
                speech_blocks=[SpeechBlock(0, 3000)], protected_ranges=[(0, 3000)], ducking_enabled=True,
            )
        self.assertEqual(result.metrics['mode'], 'adaptive')
        self.assertEqual(result.metrics['speech_block_count'], 1)


class AudioMasteringUnitTests(SimpleTestCase):
    def test_parse_loudnorm_json_extracts_measurements(self):
        stderr_text = (
            '[Parsed_loudnorm_0 @ 0x0]\n'
            '{\n'
            '\t"input_i" : "-23.00",\n'
            '\t"input_tp" : "-5.00",\n'
            '\t"input_lra" : "4.00",\n'
            '\t"input_thresh" : "-33.20",\n'
            '\t"output_i" : "-16.00",\n'
            '\t"output_tp" : "-1.00",\n'
            '\t"output_lra" : "4.00",\n'
            '\t"output_thresh" : "-26.10",\n'
            '\t"normalization_type" : "dynamic",\n'
            '\t"target_offset" : "0.00"\n'
            '}\n'
        )
        measured = AudioMasteringService._parse_loudnorm_json(stderr_text)
        self.assertEqual(measured['input_i'], '-23.00')
        self.assertEqual(measured['target_offset'], '0.00')

    def test_parse_loudnorm_json_returns_empty_dict_when_missing(self):
        self.assertEqual(AudioMasteringService._parse_loudnorm_json('no json here'), {})

    def test_result_metrics_computes_gain_applied(self):
        target = MasteringTarget(target_lufs=-16.0, true_peak_db=-1.0, profile_code='church_pa')
        metrics = AudioMasteringService._result_metrics({'input_i': '-23.00'}, target)
        self.assertEqual(metrics['gain_applied_db'], 7.0)
        self.assertEqual(metrics['profile'], 'church_pa')
        self.assertTrue(metrics['applied'])

    def test_mastering_target_from_profile_reads_model_fields(self):
        profile = SimpleNamespace(
            target_lufs=-14.5, true_peak_db=-1.2, bus_compression_enabled=True,
            limiter_enabled=False, code='instagram', name='Instagram',
        )
        target = MasteringTarget.from_profile(profile)
        self.assertEqual(target.target_lufs, -14.5)
        self.assertTrue(target.bus_compression_enabled)
        self.assertFalse(target.limiter_enabled)

    def test_mastering_target_from_profile_handles_none(self):
        target = MasteringTarget.from_profile(None)
        self.assertEqual(target, MasteringTarget())


class AudioMasteringSmokeTests(SimpleTestCase):
    def test_master_normalizes_loudness_and_reports_metrics(self):
        runner = FFmpegRunner()
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            source = workdir / 'source.mp4'
            output = workdir / 'mastered.mp4'
            runner.run([
                'ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=320x240:d=3',
                '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3',
                '-filter_complex', '[1:a]volume=0.05[a]', '-map', '0:v', '-map', '[a]',
                '-shortest', '-c:v', 'libx264', '-c:a', 'aac', str(source),
            ])
            target = MasteringTarget(target_lufs=-16.0, true_peak_db=-1.0, profile_code='church_pa')
            result = AudioMasteringService(runner).master(source, output, target)
            self.assertTrue(output.exists())
            self.assertTrue(result.metrics.get('applied'))
            self.assertEqual(result.metrics['target_lufs'], -16.0)
            self.assertIsNotNone(result.metrics.get('loudness_before_lufs'))

    def test_master_with_bus_compression_and_limiter_enabled(self):
        runner = FFmpegRunner()
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            source = workdir / 'source.mp4'
            output = workdir / 'mastered.mp4'
            runner.run([
                'ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=320x240:d=2',
                '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-shortest',
                '-c:v', 'libx264', '-c:a', 'aac', str(source),
            ])
            target = MasteringTarget(
                target_lufs=-16.0, true_peak_db=-1.0, bus_compression_enabled=True,
                limiter_enabled=True, profile_code='church_pa',
            )
            result = AudioMasteringService(runner).master(source, output, target)
            self.assertTrue(output.exists())
            self.assertTrue(result.metrics.get('applied'))


class DialogueProcessingUnitTests(SimpleTestCase):
    def test_build_leveling_envelope_holds_block_gain_and_settles_to_neutral(self):
        blocks = [(SpeechBlock(1000, 3000), 0.7)]
        envelope = build_leveling_envelope(blocks, duration_ms=5000, transition_ms=100)
        times = [point[0] for point in envelope]
        self.assertEqual(times, sorted(times))
        before_speech = next(value for time, value in envelope if time == 1.0)
        self.assertEqual(before_speech, 1.0)
        during_block = next(value for time, value in envelope if time == 3.0)
        self.assertAlmostEqual(during_block, 0.7)
        after_transition = next(value for time, value in envelope if time == 3.1)
        self.assertEqual(after_transition, 1.0)

    def test_build_leveling_envelope_clamps_transition_against_next_block(self):
        # A 1s release from the first block would normally end at t=1.5, well past
        # the second block's start (t=0.6); it must be clamped so gain is already back
        # to neutral (1.0) by the time the next block starts its own correction,
        # instead of overshooting into it.
        blocks = [(SpeechBlock(0, 500), 0.5), (SpeechBlock(600, 1200), 1.3)]
        envelope = build_leveling_envelope(blocks, duration_ms=2000, transition_ms=1000)
        times = [point[0] for point in envelope]
        self.assertEqual(times, sorted(times))
        self.assertNotIn(1.5, times)
        matching = [value for time, value in envelope if abs(time - 0.6) < 0.01]
        self.assertTrue(matching)
        self.assertEqual(matching[0], 1.0)

    def test_dialogue_settings_from_config_overrides_defaults(self):
        settings_ = DialogueSettings.from_config({'compression_ratio': 3.5, 'deesser_enabled': True})
        self.assertEqual(settings_.compression_ratio, 3.5)
        self.assertTrue(settings_.deesser_enabled)
        self.assertEqual(settings_.highpass_hz, DialogueSettings().highpass_hz)

    def test_static_chain_respects_individually_disabled_stages(self):
        settings_ = DialogueSettings(
            highpass_enabled=False, eq_enabled=False, compression_enabled=True, deesser_enabled=False,
        )
        chain = DialogueProcessor._static_chain(settings_)
        self.assertEqual(len(chain), 1)
        self.assertIn('acompressor', chain[0])

    def test_static_chain_includes_deesser_only_when_enabled(self):
        enabled = DialogueProcessor._static_chain(DialogueSettings(deesser_enabled=True))
        disabled = DialogueProcessor._static_chain(DialogueSettings(deesser_enabled=False))
        self.assertIn('deesser', enabled)
        self.assertNotIn('deesser', disabled)

    def test_compute_block_gains_skips_leveling_with_fewer_than_two_blocks(self):
        processor = DialogueProcessor(runner=Mock())
        block_gains, reference_db = processor._compute_block_gains(
            Path('/tmp/does-not-matter.mp4'), [SpeechBlock(0, 1000)], [], DialogueSettings(),
        )
        self.assertEqual(block_gains, [])
        self.assertIsNone(reference_db)

    def test_compute_block_gains_normalizes_towards_the_median_level(self):
        processor = DialogueProcessor(runner=Mock())
        levels = {(0, 1000): -30.0, (2000, 3000): -20.0, (4000, 5000): -18.0}
        processor.measure_block_mean_db = lambda path, block: levels[(block.start_ms, block.end_ms)]
        blocks = [SpeechBlock(start, end) for start, end in levels]
        block_gains, reference_db = processor._compute_block_gains(
            Path('/tmp/does-not-matter.mp4'), blocks, [], DialogueSettings(leveling_max_gain_db=6.0),
        )
        self.assertAlmostEqual(reference_db, -20.0)
        gains_by_block = {(block.start_ms, block.end_ms): gain_db for block, gain_db in block_gains}
        # The quietest block would need +10dB to reach the median, but that's clamped
        # to the configured safety cap so a single bad take can't be over-corrected.
        self.assertAlmostEqual(gains_by_block[(0, 1000)], 6.0)
        # The loudest block gets a (smaller, unclamped) negative correction towards the median.
        self.assertAlmostEqual(gains_by_block[(4000, 5000)], -2.0)
        # The block that's already at the reference level needs no correction at all.
        self.assertNotIn((2000, 3000), gains_by_block)


class DialogueProcessingSmokeTests(SimpleTestCase):
    @staticmethod
    def _render_two_speaker_tone(path, duration_s=4):
        """A single audio file with a quiet segment (0-2s) and a loud one (2-4s),
        simulating two speakers recorded at very different levels.
        """
        runner = FFmpegRunner()
        runner.run([
            'ffmpeg', '-y', '-f', 'lavfi', '-i', f'color=c=blue:s=320x240:d={duration_s}',
            '-f', 'lavfi', '-i', f'sine=frequency=220:duration={duration_s / 2}',
            '-f', 'lavfi', '-i', f'sine=frequency=220:duration={duration_s / 2}',
            '-filter_complex',
            '[1:a]volume=0.05[quiet];[2:a]volume=0.8[loud];[quiet][loud]concat=n=2:v=0:a=1[a]',
            '-map', '0:v', '-map', '[a]', '-shortest', '-c:v', 'libx264', '-c:a', 'aac', str(path),
        ])

    def test_process_applies_static_chain_and_leveling(self):
        service = DialogueProcessor()
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            video = workdir / 'video.mp4'
            output = workdir / 'dialogue.mp4'
            self._render_two_speaker_tone(video, duration_s=4)
            speech_blocks = [SpeechBlock(0, 2000), SpeechBlock(2000, 4000)]
            result = service.process(
                video, output, duration_ms=4000, speech_blocks=speech_blocks,
                settings_=DialogueSettings(),
            )
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 0)
            self.assertTrue(result.metrics['leveling_applied'])
            self.assertEqual(result.metrics['leveling_block_count'], 2)

    def test_process_skips_protected_ranges_when_leveling(self):
        service = DialogueProcessor()
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            video = workdir / 'video.mp4'
            output = workdir / 'dialogue.mp4'
            self._render_two_speaker_tone(video, duration_s=4)
            speech_blocks = [SpeechBlock(0, 2000), SpeechBlock(2000, 4000)]
            result = service.process(
                video, output, duration_ms=4000, speech_blocks=speech_blocks,
                protected_ranges=[(0, 4000)], settings_=DialogueSettings(),
            )
            self.assertTrue(output.exists())
            self.assertFalse(result.metrics['leveling_applied'])
            self.assertEqual(result.metrics['leveling_block_count'], 0)

    def test_process_is_a_no_op_copy_when_everything_is_disabled(self):
        service = DialogueProcessor()
        with tempfile.TemporaryDirectory() as directory:
            workdir = Path(directory)
            video = workdir / 'video.mp4'
            output = workdir / 'dialogue.mp4'
            self._render_two_speaker_tone(video, duration_s=2)
            disabled = DialogueSettings(
                highpass_enabled=False, eq_enabled=False, compression_enabled=False,
                deesser_enabled=False, leveling_enabled=False,
            )
            result = service.process(video, output, duration_ms=2000, speech_blocks=[], settings_=disabled)
            self.assertTrue(output.exists())
            self.assertFalse(result.metrics['leveling_applied'])


class EditableTimelineExportTests(SimpleTestCase):
    def test_premiere_archive_filename_uses_a_safe_project_name(self):
        project = SimpleNamespace(name='Anúncio: Setembro / 2026', public_id='a4e7396e-a96b-49ea-866a-19bbb4d52ef3')

        self.assertEqual(_premiere_archive_filename(project), 'anuncio-setembro-2026-premiere.zip')

    def test_selected_background_music_is_exported_without_a_legacy_plugin(self):
        builder = InternalTimelineBuilder()
        builder._copy_field = Mock()
        builder._probe = Mock(return_value={'duration_ms': 1000})
        music_file = SimpleNamespace(name='background/podcast-theme.mp3')
        project = SimpleNamespace(
            template_version=SimpleNamespace(
                background_music=SimpleNamespace(name='Podcast Theme', audio_file=music_file),
                music_file=None,
                audio_mixing_config={},
                audio_mixing_enabled=False,
                audio_ducking_enabled=False,
                music_volume=0.2,
            ),
            render_job_id=None,
            configuration={},
        )
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'Audio').mkdir()
            asset, track = builder._music(project, Path(directory), 2500)

        self.assertEqual(asset['id'], 'audio_music')
        self.assertEqual(len(track['clips']), 3)
        self.assertEqual(track['role'], 'music')

    def test_premiere_json_payload_converts_decimals_to_numbers(self):
        payload = json_compatible({
            'mastering': {'target_lufs': Decimal('-16.0')},
            'mix': [Decimal('0.75')],
        })

        self.assertEqual(payload['mastering']['target_lufs'], -16.0)
        self.assertEqual(payload['mix'], [0.75])
        self.assertEqual(json.loads(json.dumps(payload)), payload)

    def test_static_reframe_uses_a_centered_subtle_zoom(self):
        plan = AutoReframeService(priority='static')._static_center_plan(
            1920, 1080, 1920, 1080,
        )

        self.assertLess(plan.crop_width, 1920)
        self.assertLess(plan.crop_height, 1080)
        self.assertEqual(len(plan.keyframes), 1)
        self.assertGreater(plan.keyframes[0].x, 0)
        self.assertGreater(plan.keyframes[0].y, 0)

    def test_normalize_edit_ranges_merges_overlaps_and_respects_protection(self):
        ranges = [
            {'start_ms': 100, 'end_ms': 400, 'kind': 'silence'},
            {'start_ms': 350, 'end_ms': 700, 'kind': 'filler'},
            {'start_ms': 800, 'end_ms': 900, 'kind': 'background_voice'},
        ]
        self.assertEqual(
            normalize_edit_ranges(ranges, [{'start_ms': 500, 'end_ms': 600}]),
            [
                {'start_ms': 100, 'end_ms': 500, 'kind': 'filler'},
                {'start_ms': 600, 'end_ms': 700, 'kind': 'filler'},
                {'start_ms': 800, 'end_ms': 900, 'kind': 'background_voice'},
            ],
        )

    def test_dialogue_is_exported_as_an_independent_wav_track(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Media').mkdir()
            (root / 'Audio').mkdir()
            source_path = root / 'Media' / 'take.mp4'
            FFmpegRunner().run([
                'ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=black:s=320x240:d=1',
                '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1', '-shortest',
                '-c:v', 'libx264', '-c:a', 'aac', str(source_path),
            ])
            source = TimelineSource('video_1', None, 'Media/take.mp4', 'take.mp4', 'main')
            clips = [{
                'id': 'clip_1', 'asset_id': 'video_1', 'name': 'Take',
                'timeline_in_ms': 0, 'timeline_out_ms': 1000,
                'source_in_ms': 0, 'source_out_ms': 1000,
            }]
            assets, dialogue_clips = InternalTimelineBuilder()._dialogue_assets(
                [(source, {})], clips, root,
            )
            self.assertEqual(len(assets), 1)
            self.assertTrue((root / 'Audio' / 'dialogue_001_take.wav').exists())
            self.assertEqual(dialogue_clips[0]['asset_id'], 'video_1_dialogue')

    def test_keyframe_simplifier_keeps_trajectory_endpoints(self):
        points = [
            {'time_ms': index * 100, 'x': float(index), 'y': float(index), 'scale': 110.0}
            for index in range(100)
        ]
        simplified = KeyframeSimplifier.simplify(points, tolerance=0.1, max_points=12)
        self.assertEqual(simplified[0], points[0])
        self.assertEqual(simplified[-1], points[-1])
        self.assertLessEqual(len(simplified), 12)

    def test_keyframe_simplifier_preserves_normalized_tracking_curve(self):
        points = [
            {'time_ms': 0, 'center_x': 0.5, 'center_y': 0.5, 'zoom': 1.0},
            {'time_ms': 500, 'center_x': 0.62, 'center_y': 0.46, 'zoom': 1.08},
            {'time_ms': 1000, 'center_x': 0.5, 'center_y': 0.5, 'zoom': 1.0},
        ]

        self.assertEqual(KeyframeSimplifier.simplify(points), points)

    def test_reframe_interval_gets_interpolated_boundary_keyframes(self):
        keyframes = [
            {'source_time_ms': 0, 'x': 0, 'y': 10},
            {'source_time_ms': 1000, 'x': 100, 'y': 30},
        ]
        selected = InternalTimelineBuilder._keyframes_for_interval(keyframes, 250, 750)
        self.assertEqual([item['source_time_ms'] for item in selected], [250, 750])
        self.assertAlmostEqual(selected[0]['x'], 25)
        self.assertAlmostEqual(selected[-1]['y'], 25)

    def test_subtract_cuts_preserves_recoverable_source_ranges(self):
        cuts = [SpeechCut(1000, 1500, 'silence'), SpeechCut(2300, 2600, 'filler')]
        self.assertEqual(
            InternalTimelineBuilder._subtract_cuts(0, 3000, cuts),
            [(0, 1000), (1500, 2300), (2600, 3000)],
        )

    def test_premiere_xml_has_portable_paths_and_reuses_file_definition(self):
        timeline = {
            'project': {'name': 'Anúncio'},
            'sequence': {
                'name': 'Anúncio', 'duration_ms': 2000, 'width': 1920, 'height': 1080,
                'fps': 30.0, 'timebase': 30, 'ntsc': False,
                'audio_sample_rate': 48000, 'audio_channels': 2,
            },
            'assets': [{
                'id': 'video_1', 'name': 'take.mp4', 'path': './Media/take.mp4',
                'type': 'video', 'duration_ms': 3000, 'width': 1920, 'height': 1080, 'fps': 30.0,
            }, {
                'id': 'caption_overlay_pt', 'name': 'captions_pt_styled.mov',
                'path': './Graphics/captions_pt_styled.mov', 'type': 'video', 'has_audio': False,
                'alpha_mode': 'straight',
                'duration_ms': 2000, 'width': 1920, 'height': 1080, 'fps': 30.0,
            }],
            'video_tracks': [{'clips': [
                {
                    'id': 'clip_1', 'asset_id': 'video_1', 'name': 'Take',
                    'timeline_in_ms': 0, 'timeline_out_ms': 1000,
                    'source_in_ms': 0, 'source_out_ms': 1000, 'effects': [],
                },
                {
                    'id': 'clip_2', 'asset_id': 'video_1', 'name': 'Take',
                    'timeline_in_ms': 1000, 'timeline_out_ms': 2000,
                    'source_in_ms': 1500, 'source_out_ms': 2500, 'effects': [],
                },
            ]}, {
                'name': 'Legendas estilizadas (visual final)', 'locked': True, 'clips': [{
                    'id': 'caption_overlay_pt_clip', 'asset_id': 'caption_overlay_pt',
                    'name': 'Legendas estilizadas', 'timeline_in_ms': 0, 'timeline_out_ms': 2000,
                    'source_in_ms': 0, 'source_out_ms': 2000, 'audio_enabled': False,
                }],
            }],
            'audio_tracks': [{'clips': [
                {
                    'id': 'clip_1', 'asset_id': 'video_1', 'name': 'Take',
                    'timeline_in_ms': 0, 'timeline_out_ms': 1000,
                    'source_in_ms': 0, 'source_out_ms': 1000,
                },
            ]}],
            'clips': [{'id': 'clip_1'}, {'id': 'clip_2'}],
            'captions': [{
                'language': 'pt', 'is_source': True,
                'style': {'font': 'Arial', 'font_size': 48},
                'cues': [{'start_ms': 100, 'end_ms': 900, 'text': 'Bem-vindos à Filadélfia'}],
            }],
            'markers': [], 'compatibility': {'warnings': []},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'Media').mkdir()
            (root / 'Graphics').mkdir()
            (root / 'Project').mkdir()
            (root / 'Media' / 'take.mp4').write_bytes(b'original')
            (root / 'Graphics' / 'captions_pt_styled.mov').write_bytes(b'alpha captions')
            xml_path = root / 'Project' / 'timeline.xml'
            PremiereExporter().export(timeline, xml_path)
            report = ExportValidationService().validate(root, timeline, xml_path)
            xml = xml_path.read_text(encoding='utf-8')
        self.assertTrue(report['valid'])
        self.assertIn('../Media/take.mp4', xml)
        self.assertNotIn('/Users/', xml)
        # Há um asset para o take e outro para a camada ProRes transparente das legendas.
        self.assertEqual(xml.count('<pathurl>'), 2)
        self.assertIn('<mediatype>video</mediatype>', xml)
        self.assertIn('<mediatype>audio</mediatype>', xml)
        self.assertIn('generatoritem', xml)
        self.assertIn('Bem-vindos à Filadélfia', xml)
        self.assertIn('Legendas estilizadas (visual final)', xml)
        self.assertIn('../Graphics/captions_pt_styled.mov', xml)
        self.assertIn('<alphatype>straight</alphatype>', xml)

    def test_premiere_xml_applies_normalized_reframe_motion(self):
        timeline = {
            'project': {'name': 'Projeto'},
            'sequence': {
                'name': 'Projeto', 'duration_ms': 1000, 'width': 1920, 'height': 1080,
                'fps': 30.0, 'timebase': 30, 'ntsc': False,
                'audio_sample_rate': 48000, 'audio_channels': 2,
            },
            'assets': [{
                'id': 'video_1', 'name': 'take.mp4', 'path': './Media/take.mp4',
                'type': 'video', 'duration_ms': 1000, 'width': 1920, 'height': 1080, 'fps': 30.0,
            }],
            'video_tracks': [{'clips': [{
                'id': 'clip_1', 'asset_id': 'video_1', 'timeline_in_ms': 0, 'timeline_out_ms': 1000,
                'source_in_ms': 0, 'source_out_ms': 1000,
                'effects': [{
                    'type': 'transform', 'coordinate_space': 'normalized_source', 'fit': 'cover',
                    'source_width': 1920, 'source_height': 1080,
                    'keyframes': [{'time_ms': 0, 'center_x': 0.5, 'center_y': 0.5, 'zoom': 1}],
                }],
            }]}],
            'audio_tracks': [], 'clips': [{'id': 'clip_1'}], 'captions': [], 'markers': [],
        }
        with tempfile.TemporaryDirectory() as directory:
            xml_path = Path(directory) / 'timeline.xml'
            PremiereExporter().export(timeline, xml_path)
            xml = xml_path.read_text(encoding='utf-8')

        self.assertIn('<effectid>basic</effectid>', xml)
        self.assertIn('<horiz>0.0</horiz>', xml)
        self.assertIn('<vert>0.0</vert>', xml)
        self.assertNotIn('<horiz>1920</horiz>', xml)

    def test_reframe_keeps_proxy_plan_as_normalized_geometry(self):
        effect = InternalTimelineBuilder()._transform_effect(
            {
                'plan': {
                    'crop_width': 854,
                    'crop_height': 266,
                    'keyframes': [{'time_seconds': 0, 'x': 0, 'y': 31}],
                },
                'analysis_width': 854,
                'analysis_height': 480,
            },
            0,
            1000,
            0,
            {'width': 3840, 'height': 1200},
            {'width': 1920, 'height': 1080},
        )

        point = effect['keyframes'][0]
        self.assertEqual(effect['coordinate_space'], 'normalized_source')
        self.assertEqual(point['center_x'], 0.5)
        self.assertAlmostEqual(point['center_y'], 0.34143519)
        self.assertAlmostEqual(point['zoom'], 1.00334448)

    def test_premiere_transform_adapter_handles_common_source_and_sequence_sizes(self):
        cases = (
            ((1920, 1080), (1080, 1920), 177.777778),
            ((3840, 2160), (1080, 1920), 88.888889),
            ((1920, 1080), (1920, 1080), 100.0),
            ((3840, 2160), (1920, 1080), 50.0),
        )
        for source_size, sequence_size, expected_scale in cases:
            with self.subTest(source=source_size, sequence=sequence_size):
                effect = {
                    'coordinate_space': 'normalized_source',
                    'source_width': source_size[0], 'source_height': source_size[1],
                    'keyframes': [{'time_ms': 0, 'center_x': 0.5, 'center_y': 0.5, 'zoom': 1}],
                }
                points = PremiereTransformAdapter.adapt(
                    effect,
                    {'width': source_size[0], 'height': source_size[1]},
                    {'width': sequence_size[0], 'height': sequence_size[1]},
                )
                self.assertEqual(points[0].center_x, 0.0)
                self.assertEqual(points[0].center_y, 0.0)
                self.assertAlmostEqual(points[0].scale, expected_scale, places=5)

    def test_premiere_transform_adapter_bounds_edges_and_applies_zoom(self):
        effect = {
            'coordinate_space': 'normalized_source',
            'source_width': 1920, 'source_height': 1080,
            'keyframes': [
                {'time_ms': 1000, 'center_x': 0.0, 'center_y': 0.0, 'zoom': 1.2},
                {'time_ms': 2000, 'center_x': 1.0, 'center_y': 1.0, 'zoom': 1.2},
            ],
        }
        points = PremiereTransformAdapter.adapt(
            effect, {'width': 1920, 'height': 1080}, {'width': 1080, 'height': 1920},
            clip_start_ms=1000,
        )

        self.assertEqual([point.time_ms for point in points], [0.0, 1000.0])
        self.assertTrue(all(-100 <= point.center_x <= 100 for point in points))
        self.assertTrue(all(-100 <= point.center_y <= 100 for point in points))
        self.assertAlmostEqual(points[0].scale, 213.333333, places=5)
        self.assertNotEqual(points[0].center_x, points[1].center_x)
        self.assertNotEqual(points[0].center_y, points[1].center_y)

    def test_caption_overlay_explicitly_processes_alpha_channel(self):
        runner = Mock()
        builder = InternalTimelineBuilder(runner=runner)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            builder._render_alpha_caption_movie(
                root / 'captions.ass', root / 'captions.mov',
                {'width': 1920, 'height': 1080, 'fps': 30}, 1000,
            )

        command = runner.run.call_args.args[0]
        self.assertIn(':alpha=1', command[command.index('-vf') + 1])
        self.assertIn('yuva444p10le', command)

    def test_caption_overlay_movie_has_transparent_background_and_visible_text(self):
        ass = """[Script Info]
ScriptType: v4.00+
PlayResX: 320
PlayResY: 180

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,40,&H00FFFFFF,&H000000FF,&H00000000,&HFF000000,-1,0,0,0,100,100,0,0,1,2,0,5,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.00,Default,,0,0,0,,TESTE
"""
        runner = FFmpegRunner()
        builder = InternalTimelineBuilder(runner=runner)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ass_path = root / 'captions.ass'
            movie_path = root / 'captions.mov'
            ass_path.write_text(ass, encoding='utf-8')
            builder._render_alpha_caption_movie(
                ass_path, movie_path, {'width': 320, 'height': 180, 'fps': 1}, 1000,
            )
            result = runner.run_capture([
                'ffmpeg', '-hide_banner', '-i', str(movie_path),
                '-vf', 'alphaextract,blackframe=amount=0:threshold=1',
                '-frames:v', '1', '-f', 'null', '-',
            ])

        percentages = [float(value) for value in re.findall(r'pblack:([0-9.]+)', result.stderr)]
        self.assertTrue(percentages)
        self.assertGreater(percentages[0], 50)
        self.assertLess(percentages[0], 100)


class AudioNoiseCleanupUnitTests(SimpleTestCase):
    def test_decision_builder_marks_transient_events_for_review_by_default(self):
        plan = NoiseAnalysisPlan(
            (
                NoiseEvent(
                    NoiseType.TRANSIENT_NOISE,
                    742300,
                    746800,
                    0.94,
                    True,
                    RecommendedAction.REVIEW,
                    'Possível veículo passando',
                ),
            ),
            900000,
        )
        settings_ = NoiseCleanupSettings(
            enabled=True,
            global_mode=ReductionStrength.OFF,
            detect_transient_noise=True,
            transient_action=RecommendedAction.REVIEW,
        )
        decisions = NoiseReductionDecisionBuilder.build(plan, settings_)
        self.assertEqual(len(decisions), 1)
        self.assertFalse(decisions[0].enabled)
        self.assertEqual(decisions[0].recommended_action, RecommendedAction.REVIEW)

    def test_decision_builder_adds_global_cleanup_for_continuous_noise(self):
        plan = NoiseAnalysisPlan((), 600000, has_continuous_noise=True)
        settings_ = NoiseCleanupSettings(
            enabled=True,
            global_mode=ReductionStrength.LIGHT,
            auto_apply_continuous=True,
        )
        decisions = NoiseReductionDecisionBuilder.build(plan, settings_)
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].mode, ReductionMode.GLOBAL)
        self.assertTrue(decisions[0].enabled)

    def test_edit_decision_snapshot_includes_noise_reduction_operations(self):
        project = SimpleNamespace(
            public_id='00000000-0000-0000-0000-000000000001',
            template_version=SimpleNamespace(
                pk=1,
                background_music_id=None,
                music_file='',
                music_volume=0.15,
                audio_ducking_enabled=True,
                color_lut_id=None,
                lut_file='',
                plugins=SimpleNamespace(filter=lambda **kwargs: SimpleNamespace(exists=lambda: False)),
            ),
            configuration={
                'noise_reduction_decisions': [{
                    'start_ms': 742300,
                    'end_ms': 746800,
                    'mode': ReductionMode.LOCAL,
                    'strength': ReductionStrength.LIGHT,
                    'source': 'AUTO_NOISE_ANALYSIS',
                    'noise_type': NoiseType.ENVIRONMENTAL_NOISE,
                    'enabled': False,
                    'recommended_action': RecommendedAction.REVIEW,
                    'speech_overlap': True,
                    'confidence': 0.94,
                    'label': 'Possível veículo passando',
                }],
            },
        )
        snapshot = EditDecisionSetBuilder.build(project, {'sources': []})
        noise_ops = [item for item in snapshot['operations'] if item['type'] == 'audio_noise_reduction']
        self.assertEqual(len(noise_ops), 1)
        self.assertEqual(noise_ops[0]['metadata']['mode'], ReductionMode.LOCAL)
        self.assertFalse(noise_ops[0]['enabled'])

    def test_merge_events_combines_adjacent_same_type(self):
        events = [
            NoiseEvent(NoiseType.TRANSIENT_NOISE, 1000, 1800, 0.8, False, RecommendedAction.REVIEW),
            NoiseEvent(NoiseType.TRANSIENT_NOISE, 1900, 2600, 0.85, False, RecommendedAction.REVIEW),
        ]
        merged = AudioNoiseAnalysisService._merge_events(events, 5000)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].end_ms, 2600)
