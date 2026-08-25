import json
import math
import re
import tempfile
import wave
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from safedelete.models import HARD_DELETE

from website.external_media.exceptions import ExternalMediaError
from website.external_media.audio_mastering import AudioMasteringService, MasteringTarget
from website.external_media.audio_mixing import (
    AudioMixingService,
    DuckingSettings,
    SpeechBlock,
    build_ducking_envelope,
    build_spectral_windows,
    clip_blocks_against_protected_ranges,
    group_speech_blocks,
)
from website.external_media.auto_reframe import AutoReframePlan, AutoReframeService, ReframeKeyframe, limit_keyframes_for_ffmpeg
from website.external_media.dialogue_processing import (
    DialogueProcessor,
    DialogueSettings,
    build_leveling_envelope,
)
from website.external_media.speech_edit import SpeechCut, SpeechEditAnalyzer, SpeechEditPlan, SpeechEditService
from website.external_media.premiere_export import (
    ExportValidationService,
    PremiereExporter,
    PremierePackageService,
)
from website.external_media.timeline import InternalTimelineBuilder, KeyframeSimplifier
from website.external_media.services import (
    AssemblySource,
    ExternalMediaPipeline,
    ExternalMediaProjectPipeline,
    FFmpegRunner,
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
    ProjectBlockMedia,
    RenderPreset,
    SubtitleCue,
    SubtitleStyle,
    SubtitleTrack,
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

    def test_dual_ass_splits_long_text_into_sequential_cues(self):
        # Legenda dupla nunca quebra em múltiplas linhas no mesmo instante — por isso,
        # textos acima do limite viram várias legendas sequenciais (tempo repartido),
        # sem omitir palavras com reticências.
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
        self.assertGreater(len(translated_rows), 1)
        combined = ''.join(row.split(r'{\q2}', 1)[1] for row in translated_rows)
        self.assertIn('This translated line', combined)
        self.assertIn('configured limit', combined)
        self.assertNotIn('…', combined)
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

    def test_dual_ass_keeps_independent_margins(self):
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
        # Cada estilo mantém a própria margem — mexer numa não altera a outra.
        self.assertEqual(logical_original, 120)
        self.assertEqual(logical_translated, 40)
        self.assertEqual(original_margin_v, SubtitleService._ass_margin_v(self.style, 120))
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
        self.assertIn(r'{\an2\pos(1920,1034)}', translated_dialogue)

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

    def test_project_detail_page_renders_blocks_and_plugins(self):
        project = self.make_project()
        response = self.client.get(reverse('external_media_project_detail', args=[project.public_id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Vídeos do projeto')
        self.assertContains(response, 'Legenda PT')

    def test_project_duration_minutes_rounds_up(self):
        project = self.make_project()
        project.started_at = project.created_at
        project.finished_at = project.created_at + timedelta(seconds=1857)
        project.save(update_fields=['started_at', 'finished_at', 'update_at'])

        self.assertEqual(project.duration_minutes, 31)

    def test_project_can_be_renamed_from_the_edit_screen(self):
        project = self.make_project()

        response = self.client.post(
            reverse('external_media_project_edit', args=[project.public_id]),
            {'name': 'Anúncios de setembro'},
        )

        self.assertRedirects(response, reverse('external_media_project_detail', args=[project.public_id]))
        project.refresh_from_db()
        self.assertEqual(project.name, 'Anúncios de setembro')

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

    def test_project_detail_displays_its_processing_history(self):
        project = self.make_project()
        previous_job = self.make_job()
        previous_job.processing_project = project
        previous_job.save(update_fields=['processing_project', 'update_at'])

        response = self.client.get(reverse('external_media_project_detail', args=[project.public_id]))

        self.assertContains(response, 'Histórico de processamentos')
        self.assertContains(response, 'Processamento #1')

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

    @patch('website.views.external_media.run_external_media_project.delay')
    def test_valid_project_can_enter_the_async_queue(self, delay):
        delay.return_value.id = 'task-123'
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
        self.assertEqual(project.celery_task_id, 'task-123')

    def test_required_block_prevents_pipeline_without_upload(self):
        project = self.make_project()
        response = self.client.post(reverse('external_media_project_run', args=[project.public_id]))
        self.assertEqual(response.status_code, 302)
        project.refresh_from_db()
        self.assertEqual(project.status, ExternalMediaProject.Status.DRAFT)

    @patch('website.views.external_media.run_external_media_project.delay')
    def test_project_can_be_retried_after_error(self, delay):
        delay.return_value.id = 'task-retry-1'
        project = self.make_project()
        project.status = ExternalMediaProject.Status.ERROR
        project.error_message = 'Falhou na tradução'
        project.save(update_fields=['status', 'error_message', 'update_at'])
        ProjectBlockMedia.objects.create(
            project=project, block=self.block, position=1, original_filename='video.mp4',
            file=SimpleUploadedFile('video.mp4', b'video', content_type='video/mp4'), file_size=5,
        )
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('external_media_project_run', args=[project.public_id]))
        self.assertRedirects(response, reverse('external_media_project_detail', args=[project.public_id]))
        project.refresh_from_db()
        self.assertEqual(project.status, ExternalMediaProject.Status.PENDING)
        self.assertEqual(project.celery_task_id, 'task-retry-1')

    @patch('website.views.external_media.run_external_media_project.delay')
    def test_finished_project_can_be_reprocessed_with_existing_uploads(self, delay):
        delay.return_value.id = 'task-reprocess-1'
        project = self.make_project()
        project.status = ExternalMediaProject.Status.FINISHED
        project.save(update_fields=['status', 'update_at'])
        ProjectBlockMedia.objects.create(
            project=project, block=self.block, position=1, original_filename='video.mp4',
            file=SimpleUploadedFile('video.mp4', b'video', content_type='video/mp4'), file_size=5,
        )
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse('external_media_project_run', args=[project.public_id]))
        self.assertRedirects(response, reverse('external_media_project_detail', args=[project.public_id]))
        project.refresh_from_db()
        self.assertEqual(project.status, ExternalMediaProject.Status.PENDING)
        self.assertEqual(project.celery_task_id, 'task-reprocess-1')
        self.assertEqual(project.block_media.count(), 1)

    @patch.object(ExternalMediaProjectPipeline, 'render')
    @patch.object(ExternalMediaPipeline, 'prepare_subtitle_tracks')
    @patch.object(ExternalMediaProjectPipeline, '_create_render_job')
    @patch.object(ExternalMediaProjectPipeline, '_create_proxies')
    @patch.object(ExternalMediaProjectPipeline, '_materialize')
    @patch.object(VideoAssemblyService, 'assemble')
    def test_project_pipeline_renders_immediately_after_subtitles(
        self, assemble_mock, materialize_mock, proxies_mock, create_job_mock, prepare_tracks_mock, render_mock,
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
        render_mock.assert_called_once_with(project.pk)
        project.refresh_from_db()
        self.assertNotEqual(project.status, ExternalMediaProject.Status.AWAITING_REVIEW)

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
        self.assertEqual(job.current_step, 'Legendas preparadas')

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
        self.assertEqual(plan.cuts[0].duration_ms, 950)
        self.assertEqual(1700 - 500 - plan.cuts[0].duration_ms, 250)

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

    def test_speech_edit_remaps_words_after_cut_and_crossfade(self):
        plan = SpeechEditPlan((SpeechCut(500, 1000, 'silence'),), 2000, crossfade_ms=40)
        remapped = plan.remap_words([TranscriptionSegment(1200, 1500, 'Depois', 'word')])
        self.assertEqual(remapped[0].start_ms, 700)
        self.assertEqual(remapped[0].end_ms, 1000)

    def test_remap_time_shifts_only_timestamps_after_the_cut(self):
        plan = SpeechEditPlan((SpeechCut(500, 1000, 'silence'),), 2000, crossfade_ms=40)
        self.assertEqual(plan.remap_time(300), 300)
        self.assertEqual(plan.remap_time(1200), 700)
        self.assertEqual(plan.remap_time(0), 0)

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
        self.assertLess(target_y, centered_y)
        self.assertEqual(round(290 - target_y), 42)

    def test_auto_reframe_vertical_crop_keeps_head_when_person_is_taller_than_crop(self):
        service = AutoReframeService(priority='face', safe_margin=0.15, top_margin=0.12, smoothing=1.0)
        target_y = service._target_crop_y(top=290, bottom=950, crop_height=600, max_y=480)
        # Older templates persisted larger values; face framing caps them at 7%
        # to avoid excessive empty space above the head.
        self.assertEqual(round(target_y), round(290 - (600 * 0.07)))

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
        self.assertEqual(round(top), 246)
        self.assertEqual(round(width), 528)
        self.assertEqual(round(height), 518)

    def test_auto_reframe_vertical_crop_reduces_excessive_headroom(self):
        service = AutoReframeService(priority='face', safe_margin=0.15, top_margin=0.18, smoothing=1.0)
        centered_y = ((260 + 560) / 2) - (600 / 2)
        target_y = service._target_crop_y(top=260, bottom=560, crop_height=600, max_y=480)
        self.assertGreater(target_y, centered_y)
        self.assertEqual(round(260 - target_y), 42)

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
        self.assertEqual(round(keyframes[0].y), 248)

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
            horizontal_smoothing=0.36,
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
        # the envelope must clamp the release so it never re-raises volume after the next
        # block has already started ducking again.
        settings_ = DuckingSettings(attack_ms=100, hold_ms=100, release_ms=2000)
        blocks = [SpeechBlock(0, 500), SpeechBlock(700, 1200)]
        envelope = build_ducking_envelope(blocks, duration_ms=2000, duck_gain=0.5, settings_=settings_)
        times = [point[0] for point in envelope]
        self.assertEqual(times, sorted(times))
        self.assertNotIn(2.5, times)
        matching = [value for time, value in envelope if abs(time - 0.7) < 0.01]
        self.assertTrue(matching)
        self.assertEqual(matching[0], 1.0)

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

    def test_clipping_speech_blocks_against_a_protected_range_keeps_music_flat_there(self):
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
        self.assertEqual(result.metrics['mode'], 'flat')


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
    def test_keyframe_simplifier_keeps_trajectory_endpoints(self):
        points = [
            {'time_ms': index * 100, 'x': float(index), 'y': float(index), 'scale': 110.0}
            for index in range(100)
        ]
        simplified = KeyframeSimplifier.simplify(points, tolerance=0.1, max_points=12)
        self.assertEqual(simplified[0], points[0])
        self.assertEqual(simplified[-1], points[-1])
        self.assertLessEqual(len(simplified), 12)

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
        self.assertEqual(xml.count('<pathurl>'), 1)
        self.assertIn('<mediatype>video</mediatype>', xml)
        self.assertIn('<mediatype>audio</mediatype>', xml)
        self.assertIn('generatoritem', xml)
        self.assertIn('Bem-vindos à Filadélfia', xml)
        self.assertIn('Legendas estilizadas (visual final)', xml)
        self.assertIn('../Graphics/captions_pt_styled.mov', xml)
