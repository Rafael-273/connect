import json
import tempfile
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from safedelete.models import HARD_DELETE

from website.external_media.exceptions import ExternalMediaError
from website.external_media.auto_reframe import AutoReframePlan, AutoReframeService, ReframeKeyframe
from website.external_media.speech_edit import SpeechCut, SpeechEditAnalyzer, SpeechEditPlan, SpeechEditService
from website.external_media.services import (
    ExternalMediaPipeline,
    FFmpegRunner,
    RenderService,
    SubtitleService,
    TemplateService,
    TranscriptionSegment,
    TranslationService,
    VideoAssemblyService,
    VideoMetadata,
)
from website.forms.admin_external_media import AdminMediaTemplateBlockForm
from website.models import Member, Ministry, MinistryMembership, Music, User
from website.models.external_media import (
    ExternalMediaJob,
    ExternalMediaProject,
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
    external_media_project_upload_path,
)


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

    def make_job(self, status=ExternalMediaJob.Status.AWAITING_REVIEW):
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
            text='Hello church this is a long translated subtitle',
        )
        service = SubtitleService()
        with tempfile.TemporaryDirectory() as directory:
            ass = Path(directory) / 'dual.ass'
            service.write_dual_ass([translated, self.track], ass, self.style, 1920, 1080, 'pt')
            content = ass.read_text(encoding='utf-8-sig')
        self.assertIn('Style: Original', content)
        self.assertIn('Style: Translated', content)
        self.assertIn(r'{\q2}Olá, igreja!', content)
        self.assertIn(r'{\q2}Hello church this is a long translated subtitle', content)
        self.assertNotIn(r'\N', content)

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

    def test_admin_panel_media_template_pages_render(self):
        urls = [
            reverse('admin_external_media_templates'),
            reverse('admin_external_media_template_detail', args=[self.template.pk]),
            reverse('admin_external_media_template_edit', args=[self.template.pk]),
            reverse('admin_external_media_version_edit', args=[self.version.pk]),
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)

    def test_admin_panel_can_create_media_template(self):
        response = self.client.post(reverse('admin_external_media_template_create'), {
            'name': 'Stories de Testemunho',
            'description': 'Modelo para cortes verticais.',
            'is_active': 'on',
        })
        template = MediaTemplate.objects.get(slug='stories-de-testemunho')
        self.assertRedirects(
            response,
            reverse('admin_external_media_version_create', args=[template.pk]),
        )

    def test_new_block_form_defaults_to_four_videos(self):
        form = AdminMediaTemplateBlockForm()
        self.assertTrue(form.fields['allows_multiple'].initial)
        self.assertEqual(form.fields['min_occurrences'].initial, 1)
        self.assertEqual(form.fields['max_occurrences'].initial, 4)

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
            'preset-extra_ffmpeg_args_raw': '["-maxrate", "8M"]',
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
            'style-font_size': '44',
            'style-primary_color': '#FFFFFF',
            'style-outline_color': '#111111',
            'style-outline_width': '3',
            'style-shadow': '1',
            'style-margin_bottom': '80',
            'style-alignment': '2',
            'style-max_lines': '2',
            'style-max_characters': '38',
            'style-is_active': 'on',
        })
        self.assertRedirects(response, reverse('admin_external_media_version_edit', args=[self.version.pk]))
        style = SubtitleStyle.objects.get(name='Legenda Feed')
        self.assertEqual(style.max_characters, 38)

    def test_admin_panel_can_create_background_music_from_version_page(self):
        response = self.client.post(reverse('admin_external_media_background_music_save'), {
            'next': reverse('admin_external_media_version_edit', args=[self.version.pk]),
            'bgmusic-name': 'Base Instrumental',
            'bgmusic-singer': 'Filadelfia Worship',
            'bgmusic-tempo': 'media',
            'bgmusic-audio_file': SimpleUploadedFile('base.mp3', b'audio', content_type='audio/mpeg'),
        })
        self.assertRedirects(response, reverse('admin_external_media_version_edit', args=[self.version.pk]))
        music = Music.objects.get(name='Base Instrumental')
        self.assertTrue(bool(music.audio_file))

    def test_admin_panel_saves_bilingual_language_strategy(self):
        response = self.client.post(reverse('admin_external_media_version_edit', args=[self.version.pk]), {
            'changelog': 'Configuração bilíngue.',
            'preset': self.preset.pk,
            'subtitle_style': self.style.pk,
            'original_language': 'pt',
            'language_mode': 'bilingual_source',
            'spoken_languages': ['pt', 'en'],
            'translated_language': 'en',
            'default_settings_raw': '{}',
            'allowed_overrides_raw': '[]',
            'music_volume': '0.15',
            'fade_in_seconds': '0',
            'fade_out_seconds': '0',
            'blocks-TOTAL_FORMS': '0',
            'blocks-INITIAL_FORMS': '0',
            'blocks-MIN_NUM_FORMS': '0',
            'blocks-MAX_NUM_FORMS': '1000',
            'plugins-TOTAL_FORMS': '0',
            'plugins-INITIAL_FORMS': '0',
            'plugins-MIN_NUM_FORMS': '0',
            'plugins-MAX_NUM_FORMS': '1000',
        })
        self.assertRedirects(
            response,
            reverse('admin_external_media_template_detail', args=[self.template.pk]),
        )
        self.version.refresh_from_db()
        self.assertEqual(self.version.default_settings['language_mode'], 'bilingual_source')
        self.assertEqual(self.version.default_settings['spoken_languages'], ['pt', 'en'])
        self.assertEqual(self.version.output_languages, ['pt', 'en'])

    def test_admin_panel_single_language_strategy_outputs_only_default_language(self):
        response = self.client.post(reverse('admin_external_media_version_edit', args=[self.version.pk]), {
            'changelog': 'Configuração de idioma único.',
            'preset': self.preset.pk,
            'subtitle_style': self.style.pk,
            'original_language': 'pt',
            'language_mode': 'single',
            'spoken_languages': ['pt', 'en'],
            'translated_language': 'en',
            'default_settings_raw': '{}',
            'allowed_overrides_raw': '[]',
            'music_volume': '0.15',
            'fade_in_seconds': '0',
            'fade_out_seconds': '0',
            'blocks-TOTAL_FORMS': '0',
            'blocks-INITIAL_FORMS': '0',
            'blocks-MIN_NUM_FORMS': '0',
            'blocks-MAX_NUM_FORMS': '1000',
            'plugins-TOTAL_FORMS': '0',
            'plugins-INITIAL_FORMS': '0',
            'plugins-MIN_NUM_FORMS': '0',
            'plugins-MAX_NUM_FORMS': '1000',
        })
        self.assertRedirects(
            response,
            reverse('admin_external_media_template_detail', args=[self.template.pk]),
        )
        self.version.refresh_from_db()
        self.assertEqual(self.version.default_settings['language_mode'], 'single')
        self.assertEqual(self.version.default_settings['spoken_languages'], ['pt'])
        self.assertEqual(self.version.output_languages, ['pt'])

    def test_admin_panel_translated_language_strategy_sets_translation_target(self):
        response = self.client.post(reverse('admin_external_media_version_edit', args=[self.version.pk]), {
            'changelog': 'Configuração traduzida.',
            'preset': self.preset.pk,
            'subtitle_style': self.style.pk,
            'original_language': 'pt',
            'language_mode': 'translated',
            'spoken_languages': ['pt'],
            'translated_language': 'en',
            'default_settings_raw': '{}',
            'allowed_overrides_raw': '[]',
            'music_volume': '0.15',
            'fade_in_seconds': '0',
            'fade_out_seconds': '0',
            'blocks-TOTAL_FORMS': '0',
            'blocks-INITIAL_FORMS': '0',
            'blocks-MIN_NUM_FORMS': '0',
            'blocks-MAX_NUM_FORMS': '1000',
            'plugins-TOTAL_FORMS': '0',
            'plugins-INITIAL_FORMS': '0',
            'plugins-MIN_NUM_FORMS': '0',
            'plugins-MAX_NUM_FORMS': '1000',
        })
        self.assertRedirects(
            response,
            reverse('admin_external_media_template_detail', args=[self.template.pk]),
        )
        self.version.refresh_from_db()
        self.assertEqual(self.version.default_settings['language_mode'], 'translated')
        self.assertEqual(self.version.default_settings['translated_language'], 'en')
        self.assertEqual(self.version.output_languages, ['pt', 'en'])

    def test_admin_panel_can_create_multiple_blocks_in_one_version_save(self):
        response = self.client.post(reverse('admin_external_media_version_edit', args=[self.version.pk]), {
            'changelog': 'Blocos configurados.',
            'preset': self.preset.pk,
            'subtitle_style': self.style.pk,
            'original_language': 'pt',
            'language_mode': 'translated',
            'translated_language': 'en',
            'default_settings_raw': '{}',
            'allowed_overrides_raw': '[]',
            'music_volume': '0.15',
            'fade_in_seconds': '0',
            'fade_out_seconds': '0',
            'blocks-TOTAL_FORMS': '2',
            'blocks-INITIAL_FORMS': '0',
            'blocks-MIN_NUM_FORMS': '0',
            'blocks-MAX_NUM_FORMS': '1000',
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
            'plugins-TOTAL_FORMS': '0',
            'plugins-INITIAL_FORMS': '0',
            'plugins-MIN_NUM_FORMS': '0',
            'plugins-MAX_NUM_FORMS': '1000',
        })
        self.assertRedirects(
            response,
            reverse('admin_external_media_template_detail', args=[self.template.pk]),
        )
        blocks = list(self.version.blocks.order_by('order'))
        self.assertEqual([block.name for block in blocks], ['Mensagem principal', 'Encerramento'])
        self.assertEqual([block.key for block in blocks], ['mensagem-principal', 'encerramento'])
        self.assertEqual([block.max_occurrences for block in blocks], [1, 1])

    def test_admin_panel_uses_checkboxes_for_advanced_plugins(self):
        response = self.client.post(reverse('admin_external_media_version_edit', args=[self.version.pk]), {
            'changelog': 'Com ajustes extras.',
            'preset': self.preset.pk,
            'subtitle_style': self.style.pk,
            'original_language': 'pt',
            'language_mode': 'translated',
            'translated_language': 'en',
            'advanced_plugins': [
                MediaTemplatePlugin.Code.SILENCE_REMOVAL,
                MediaTemplatePlugin.Code.FILLER_REMOVAL,
            ],
            'speech_edit_profile': 'conservative',
            'filler_words': 'eh, hum, tipo',
            'default_settings_raw': '{}',
            'allowed_overrides_raw': '[]',
            'music_volume': '0.15',
            'fade_in_seconds': '0',
            'fade_out_seconds': '0',
            'blocks-TOTAL_FORMS': '0',
            'blocks-INITIAL_FORMS': '0',
            'blocks-MIN_NUM_FORMS': '0',
            'blocks-MAX_NUM_FORMS': '1000',
        })
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
        response = self.client.post(reverse('admin_external_media_version_edit', args=[self.version.pk]), {
            'changelog': 'Auto Reframe para apresentações.',
            'preset': self.preset.pk,
            'subtitle_style': self.style.pk,
            'original_language': 'pt',
            'language_mode': 'single',
            'advanced_plugins': [MediaTemplatePlugin.Code.AUTO_TRACKING],
            'auto_reframe_priority': 'body',
            'default_settings_raw': '{}',
            'allowed_overrides_raw': '[]',
            'blocks-TOTAL_FORMS': '0',
            'blocks-INITIAL_FORMS': '0',
            'blocks-MIN_NUM_FORMS': '0',
            'blocks-MAX_NUM_FORMS': '1000',
        })
        self.assertRedirects(
            response,
            reverse('admin_external_media_template_detail', args=[self.template.pk]),
        )
        plugin = self.version.plugins.get(code=MediaTemplatePlugin.Code.AUTO_TRACKING)
        self.assertTrue(plugin.is_enabled)
        self.assertEqual(plugin.configuration['priority'], 'body')
        self.assertEqual(plugin.configuration['safe_margin'], 0.15)
        self.assertEqual(plugin.configuration['top_margin'], 0.18)


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

    def test_auto_reframe_vertical_crop_preserves_headroom(self):
        service = AutoReframeService(priority='face', safe_margin=0.15, top_margin=0.18, smoothing=1.0)
        centered_y = ((290 + 850) / 2) - (600 / 2)
        target_y = service._target_crop_y(top=290, bottom=850, crop_height=600, max_y=480)
        self.assertLess(target_y, centered_y)
        self.assertEqual(round(290 - target_y), 92)

    def test_auto_reframe_vertical_crop_reduces_excessive_headroom(self):
        service = AutoReframeService(priority='face', safe_margin=0.15, top_margin=0.18, smoothing=1.0)
        centered_y = ((260 + 560) / 2) - (600 / 2)
        target_y = service._target_crop_y(top=260, bottom=560, crop_height=600, max_y=480)
        self.assertGreater(target_y, centered_y)
        self.assertEqual(round(260 - target_y), 108)

    def test_auto_reframe_face_box_keeps_top_close_to_head(self):
        left, top, width, height = AutoReframeService._face_priority_box(800, 260, 120, 120)
        self.assertEqual(round(left), 596)
        self.assertEqual(round(top), 234)
        self.assertEqual(round(width), 528)
        self.assertEqual(round(height), 566)

    def test_auto_reframe_face_detection_tries_contrast_fallbacks(self):
        gray = SimpleNamespace(shape=(720, 1280))
        detector = Mock()
        detector.detectMultiScale.side_effect = [[], [(100, 120, 80, 80)]]
        fake_cv2 = SimpleNamespace(
            createCLAHE=lambda clipLimit, tileGridSize: SimpleNamespace(apply=lambda value: value),
            equalizeHist=lambda value: value,
        )
        faces = AutoReframeService._detect_faces(gray, [detector], fake_cv2)
        self.assertEqual(faces, [(100, 120, 80, 80)])
        self.assertEqual(detector.detectMultiScale.call_count, 2)

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
        self.assertEqual(round(keyframes[0].y), 198)

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
        service = AutoReframeService(
            priority='face',
            safe_margin=0.15,
            top_margin=0.18,
            smoothing=0.18,
        )
        keyframes = service._smooth_keyframes(
            observations=[
                (0.0, (100, 290, 300, 850)),
                (1.0, (700, 290, 900, 850)),
            ],
            crop_width=608,
            crop_height=1080,
            source_width=1920,
            source_height=1080,
        )
        self.assertEqual(round(keyframes[0].x), 0)
        self.assertGreater(round(keyframes[1].x), 160)

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
            VideoAssemblyService(runner=runner).assemble(sources, output, preset, workdir)
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 0)

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

        with patch.object(VideoAssemblyService, '_has_audio', return_value=True), \
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
