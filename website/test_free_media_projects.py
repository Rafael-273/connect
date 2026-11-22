import io
import json
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from website.external_media.canonical import SourceManifestBuilder
from website.external_media.preview import TimelineRevisionService
from website.external_media.services import ExternalMediaProjectPipeline
from website.external_media.tasks import create_project_preview
from website.external_media.video_order import clip_range
from website.models import MinistryMembership, SourceTakeAnalysis
from website.models.external_media import (
    BackgroundMusicTrack,
    ExternalMediaProject,
    MediaAsset,
    MediaTemplate,
    MediaTemplateBlock,
    MediaTemplateVersion,
    ProjectBlockMedia,
    get_external_media_storage,
)
from website.test_external_media import ExternalMediaFixtureMixin


class FreeProjectTests(ExternalMediaFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.preset.width, self.preset.height = 160, 90
        self.preset.save(update_fields=['width', 'height'])
        MinistryMembership.objects.create(member=self.member, ministry=self.ministry)
        self.client.force_login(self.user)

    def open_project(self, captions=False):
        create = reverse('external_media_create')
        response = self.client.post(create, {
            'name': 'Montagem manual', 'mode': 'free', 'free_preset': self.preset.pk,
            'automatic_captions': str(captions).lower(),
        })
        self.project = ExternalMediaProject.objects.get(name='Montagem manual')
        self.assertRedirects(response, self.url('preview'), fetch_redirect_response=False)
        return self.project

    def url(self, action):
        return reverse(f'external_media_project_{action}', args=[self.project.public_id])

    def edit(self, action, payload, operation=None, version=None):
        self.project.refresh_from_db()
        return self.client.post(
            self.url(f'preview_{action}'), json.dumps(payload), content_type='application/json',
            HTTP_X_EDITOR_VERSION=str(self.project.editorial_version if version is None else version),
            HTTP_X_EDITOR_OPERATION=str(operation or uuid.uuid4()),
        )

    def upload(self, name, contents=b'media', ready=True):
        with patch('website.views.external_media.ExternalMediaProjectUploadView._enqueue_editor_upload') as enqueue:
            response = self.client.post(self.url('direct_upload') + '?editor=1', {
                'file': SimpleUploadedFile(name, contents, content_type='video/mp4'),
            }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200, response.content)
        item = self.project.block_media.get(pk=response.json()['id'])
        self.assertEqual(enqueue.call_args.args[0].pk, item.pk)
        self.assertTrue(item.editor_only)
        if ready:
            item.preview_status = item.PreviewStatus.READY
            item.duration_ms = 2000
            item.preview_file.name = item.file.name
            item.save(update_fields=['preview_status', 'duration_ms', 'preview_file'])
        return item

    def insert(self, item, start=None, end=None, before=None):
        response = self.edit('media', {'media_id': item.pk, 'start_ms': start, 'end_ms': end, 'before': before})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()['timeline']

    def test_create_modes_and_reopen_empty_project(self):
        self.assertContains(self.client.get(reverse('external_media_dashboard')), 'Criar do zero')
        template = MediaTemplate.objects.create(name='Modelo de teste', slug='modelo-de-teste')
        version = MediaTemplateVersion.objects.create(template=template, preset=self.preset, default_settings={'plugins': {}})
        response = self.client.post(reverse('external_media_create'), {
            'mode': 'template', 'name': 'Com modelo', 'template_version': version.pk,
        })
        project = ExternalMediaProject.objects.get(name='Com modelo')
        self.assertEqual(project.template_version, version)
        self.assertEqual(project.configuration, version.default_settings)
        self.assertRedirects(response, reverse('external_media_project_detail', args=[project.public_id]), fetch_redirect_response=False)

        project = self.open_project()
        self.assertIsNone(project.template_version_id)
        self.assertEqual(project.current_timeline_revision.timeline['clips'], [])
        self.assertContains(self.client.get(self.url('preview')), 'Sua timeline está vazia')
        self.assertRedirects(self.client.get(self.url('detail')), self.url('preview'), fetch_redirect_response=False)
        response = self.edit('approve', {})
        self.assertEqual(response.status_code, 400)

    @override_settings(USE_S3=True)
    def test_direct_object_upload_registers_editor_media_idempotently(self):
        self.open_project()
        contents = b'video'
        with patch('website.views.external_media._prepare_s3_upload', side_effect=lambda key, *_: {
            'storage_key': key, 'upload_url': 'https://example.invalid/signed-upload',
        }):
            signed = self.client.post(
                self.url('direct_upload_presign') + '?editor=1',
                json.dumps({'filename': 'take.mov', 'size': len(contents)}),
                content_type='application/json',
            ).json()
        key = signed['allocation']['storage_key']
        get_external_media_storage().save(key, ContentFile(contents))
        confirmation = json.dumps({field: signed[field] for field in ('allocation', 'allocation_sig')})
        with patch('website.views.external_media.ExternalMediaProjectUploadView._enqueue_editor_upload') as enqueue:
            first = self.client.post(signed['complete_url'], confirmation, content_type='application/json')
            second = self.client.post(signed['complete_url'], confirmation, content_type='application/json')
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(first.json(), second.json())
        enqueue.assert_called_once()
        self.assertTrue(self.project.block_media.get().editor_only)
        self.assertEqual(self.project.current_timeline_revision.timeline['clips'], [])

    def test_invalid_creation_reopens_popup_with_errors(self):
        response = self.client.post(reverse('external_media_create'), {
            'mode': 'free', 'name': '',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'create-project-dialog')
        self.assertFormError(response.context['create_form'], 'name', 'This field is required.')
        self.assertContains(response, 'data-initial-mode="free"')
        self.assertEqual(ExternalMediaProject.objects.count(), 0)

    def test_media_library_tracks_active_clips_and_undo(self):
        self.open_project()
        source = self.upload('original.mp4')
        def state():
            response = self.client.get(self.url('preview_media'))
            self.assertEqual(response.status_code, 200)
            return response.json()['items'][0]['in_timeline']

        self.assertFalse(state())
        timeline = self.insert(source)
        self.assertTrue(state())
        clip = timeline['clips'][0]
        self.edit('cut_create', {'start_ms': clip['master_in_ms'], 'end_ms': clip['master_out_ms']})
        self.assertFalse(state())
        self.edit('undo', {})
        self.assertTrue(state())

    def test_missing_music_waveform_is_not_queued(self):
        from website.models.external_media import ProjectPreviewArtifact

        self.open_project()
        track = BackgroundMusicTrack.objects.create(name='Arquivo ausente', audio_file='missing-song.mp3')
        url = self.url('preview_music') + f'?track_id={track.pk}&waveform=1'
        with patch('website.external_media.preview_artifacts.PreviewArtifactService.enqueue') as enqueue:
            response = self.client.get(url)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['error'], 'O arquivo desta música não está disponível.')
        self.assertFalse(ProjectPreviewArtifact.objects.filter(project=self.project).exists())
        enqueue.assert_not_called()

    def test_insertion_move_trim_removal_history_and_reuse(self):
        self.open_project()
        first = self.upload('original.mp4')
        pending = self.upload('processando.mp4', ready=False)
        self.assertEqual(self.edit('media', {'media_id': pending.pk}).status_code, 400)
        timeline = self.insert(first, 200, 1000)
        a = clip_range(timeline['clips'][0])
        timeline = self.insert(first, 1200, 1900, a)
        b = clip_range(timeline['clips'][0])
        timeline = self.insert(self.upload('outro.mp4'), 0, 1000)
        c = clip_range(timeline['clips'][-1])

        operation = uuid.uuid4()
        self.project.refresh_from_db()
        base = self.project.editorial_version
        payload = {**c, 'before': b}
        response = self.edit('video_move', payload, operation, base)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), self.edit('video_move', payload, operation, base).json())
        self.assertEqual(self.edit('video_move', {**a, 'before': b}, version=base).status_code, 409)
        self.assertEqual([clip_range(clip) for clip in response.json()['timeline']['clips']], [c, b, a])
        timeline = self.edit('video_move', {**c, 'before': None}).json()['timeline']
        self.assertEqual([clip_range(clip) for clip in timeline['clips']], [b, a, c])

        timeline = self.edit('video_trim', {'take': a, 'source_in_ms': 300, 'source_out_ms': 900}).json()['timeline']
        trimmed = timeline['clips'][1]
        self.assertEqual((trimmed['source_in_ms'], trimmed['source_out_ms']), (300, 900))
        self.assertEqual([(c['timeline_in_ms'], c['timeline_out_ms']) for c in timeline['clips']], [(0, 700), (700, 1300), (1300, 2300)])
        removed = self.edit('cut_create', {
            'start_ms': trimmed['master_in_ms'], 'end_ms': trimmed['master_out_ms'],
        }).json()['timeline']
        self.assertEqual([clip_range(clip) for clip in removed['clips']], [b, c])
        self.assertTrue(first.file.storage.exists(first.file.name))
        undo = self.edit('undo', {}).json()['timeline']
        self.assertEqual(len(undo['clips']), 3)
        self.assertEqual(len(self.edit('redo', {}).json()['timeline']['clips']), 2)
        page = self.client.get(self.url('preview'))
        self.assertEqual([clip_range(clip) for clip in page.context['timeline']['clips']], [b, c])
        self.assertTrue(all(asset['url'] for asset in page.context['timeline']['assets']))
        self.assertEqual(self.project.block_media.filter(pk=first.pk).count(), 1)

    def test_template_reprocessing_requires_explicit_confirmation_after_manual_insert(self):
        template = MediaTemplate.objects.create(name='Modelo legado', slug='modelo-legado')
        version = MediaTemplateVersion.objects.create(template=template, preset=self.preset)
        block = MediaTemplateBlock.objects.create(version=version, name='Cena do modelo', key='cena')
        self.project = ExternalMediaProject.objects.create(
            name='Edição existente', created_by=self.member, template_version=version,
            status=ExternalMediaProject.Status.AWAITING_REVIEW,
        )
        legacy = ProjectBlockMedia.objects.create(
            project=self.project, block=block, file=SimpleUploadedFile('original.mp4', b'original'),
            original_filename='original.mp4', duration_ms=2000,
        )
        source = self.upload('editor.mp4')
        manifest = SourceManifestBuilder.build(self.project)
        self.assertEqual([entry['source_reference'] for entry in manifest['sources']], [str(legacy.pk)])
        timeline = self.insert(source)
        self.assertEqual(len(timeline['clips']), 2)
        self.project.refresh_from_db()
        revision = self.project.current_timeline_revision_id
        self.client.post(self.url('run'))
        self.project.refresh_from_db()
        self.assertEqual(self.project.current_timeline_revision_id, revision)

    def test_cached_source_captions_are_reused_and_follow_multiple_clips(self):
        self.open_project(captions=True)
        source = self.upload('fala.mp4')
        source.take_analysis = SourceTakeAnalysis.objects.create(
            project=self.project, sha256='a' * 64, language='pt', duration_ms=2000,
            status='ready', transcript_ready=True,
            transcript=[{'text': 'Olá', 'start_ms': 250, 'end_ms': 600, 'granularity': 'word'}],
        )
        source.save(update_fields=['take_analysis'])
        timeline = self.insert(source, 200, 1100)
        first = clip_range(timeline['clips'][0])
        timeline = self.insert(source, 100, 1300, first)
        self.assertEqual([(cue['start_ms'], cue['end_ms']) for cue in timeline['captions']], [(150, 500), (1250, 1600)])
        timeline = self.edit('video_move', {**first, 'before': clip_range(timeline['clips'][0])}).json()['timeline']
        self.assertEqual(sorted((cue['start_ms'], cue['end_ms']) for cue in timeline['captions']), [(50, 400), (1050, 1400)])
        self.assertEqual(SourceTakeAnalysis.objects.filter(project=self.project).count(), 1)

    def test_translated_captions_follow_reused_and_reordered_takes(self):
        self.open_project(captions=True)
        self.project.configuration = {**self.project.configuration, 'caption_language': 'en'}
        self.project.save(update_fields=['configuration'])
        source = self.upload('fala.mp4')
        source.take_analysis = SourceTakeAnalysis.objects.create(
            project=self.project, sha256='b' * 64, language='pt', duration_ms=2000,
            status='ready', transcript_ready=True,
            transcript=[{'text': 'Olá', 'start_ms': 250, 'end_ms': 600, 'granularity': 'word'}],
            translations={'en': [{'text': 'Hello', 'start_ms': 250, 'end_ms': 600}]},
        )
        source.save(update_fields=['take_analysis'])
        timeline = self.insert(source, 200, 1100)
        first = clip_range(timeline['clips'][0])
        timeline = self.insert(source, 100, 1300, first)
        self.assertEqual([cue['text'] for cue in timeline['captions']].count('Hello'), 2)
        timeline = self.edit('video_move', {**first, 'before': clip_range(timeline['clips'][0])}).json()['timeline']
        self.assertEqual([(cue['start_ms'], cue['end_ms']) for cue in timeline['captions'] if cue['language'] == 'en'],
                         [(50, 400), (1050, 1400)])
        self.project.refresh_from_db()
        self.assertEqual(self.project.render_job.output_languages, ['pt', 'en'])

    def test_bilingual_project_uses_selected_source_and_dual_render(self):
        from website.external_media.services import ExternalMediaPipeline
        response = self.client.post(reverse('external_media_create'), {
            'name': 'Duas legendas', 'mode': 'free', 'free_preset': self.preset.pk,
            'automatic_captions': 'on', 'source_language': 'es',
            'caption_language': 'en', 'bilingual_captions': 'on',
        })
        self.assertEqual(response.status_code, 302)
        self.project = ExternalMediaProject.objects.get(name='Duas legendas')
        self.assertEqual(self.project.editor_settings.original_language, 'es')
        source = self.upload('fala.mp4')
        source.take_analysis = SourceTakeAnalysis.objects.create(
            project=self.project, sha256='c' * 64, language='es', duration_ms=2000,
            status='ready', transcript_ready=True,
            transcript=[{'text': 'Hola', 'start_ms': 250, 'end_ms': 600, 'granularity': 'word'}],
            translations={'en': [{'text': 'Hello', 'start_ms': 250, 'end_ms': 600}]},
        )
        source.save(update_fields=['take_analysis'])
        timeline = self.insert(source)
        self.assertEqual({cue['language'] for cue in timeline['captions']}, {'es', 'en'})
        self.project.refresh_from_db()
        job = self.project.render_job
        tracks = list(job.subtitle_tracks.all())
        self.assertTrue(ExternalMediaPipeline._should_render_dual_subtitle_video(job, tracks))

    def test_bilingual_project_requires_different_languages(self):
        response = self.client.post(reverse('external_media_create'), {
            'name': 'Duas legendas', 'mode': 'free', 'free_preset': self.preset.pk,
            'automatic_captions': 'on', 'source_language': 'pt',
            'caption_language': 'pt', 'bilingual_captions': 'on',
        })
        self.assertEqual(response.status_code, 200)
        self.assertFormError(response.context['create_form'], 'caption_language',
                             'Escolha um idioma diferente do áudio para a tradução.')

    def test_ffmpeg_keeps_approved_order_cuts_broll_captions_and_audio(self):
        if not shutil.which(settings.FFMPEG_BINARY) or not shutil.which(settings.FFPROBE_BINARY):
            self.skipTest('FFmpeg e FFprobe são necessários para validar os quadros exportados.')
        self.open_project(captions=True)
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)

            def ffmpeg(*args):
                return subprocess.check_output([settings.FFMPEG_BINARY, '-hide_banner', '-loglevel', 'error', '-y', *map(str, args)])

            for color, pitch in [('red', 440), ('blue', 550)]:
                ffmpeg('-f', 'lavfi', '-i', f'color={color}:s=160x90:r=30:d=2', '-f', 'lavfi',
                       '-i', f'sine=frequency={pitch}:duration=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                       '-c:a', 'aac', directory / f'{color}.mp4')
            red, blue = [self.upload(f'{color}.mp4', (directory / f'{color}.mp4').read_bytes(), ready=False)
                         for color in ('red', 'blue')]
            for item in (red, blue):
                create_project_preview.run(item.pk)
                item.refresh_from_db()
                item.take_analysis = SourceTakeAnalysis.objects.create(
                    project=self.project, sha256=f'{item.pk:064x}', language='pt', duration_ms=2000,
                    status='ready', transcript_ready=True,
                    transcript=[{'text': 'Teste', 'start_ms': 600, 'end_ms': 800, 'granularity': 'word'}],
                )
                item.save(update_fields=['take_analysis'])
            self.insert(red, 500, 1500)
            self.insert(red, 1000, 1750)
            timeline = self.insert(blue, 250, 1500)
            moved = self.edit('video_move', {**clip_range(timeline['clips'][-1]), 'before': clip_range(timeline['clips'][0])})
            self.assertEqual(moved.status_code, 200, moved.content)
            image = io.BytesIO()
            Image.new('RGB', (160, 90), 'lime').save(image, format='PNG')
            response = self.client.post(self.url('preview_broll_create'), {
                'file': SimpleUploadedFile('verde.png', image.getvalue(), content_type='image/png'),
                'start_ms': '800', 'duration_seconds': '1',
            })
            self.assertEqual(response.status_code, 200, response.content)
            for index, frequency in enumerate((220, 330)):
                file = directory / f'track{index}.wav'
                ffmpeg('-f', 'lavfi', '-i', f'sine=frequency={frequency}:duration=2', file)
                track = BackgroundMusicTrack.objects.create(
                    name=f'Trilha {index}', audio_file=SimpleUploadedFile(file.name, file.read_bytes()),
                )
                if not index:
                    first = track
            response = self.edit('music', {'tracks': [
                {'track_id': first.pk, 'clip_id': 'one', 'start_ms': 0, 'end_ms': 1800},
                {'track_id': track.pk, 'clip_id': 'two', 'start_ms': 1600, 'end_ms': 3000,
                 'audio_transition': {'type': 'CONSTANT_POWER', 'duration_ms': 200, 'from_clip_id': 'one'}},
            ]})
            self.assertEqual(response.status_code, 200, response.content)
            with patch('website.views.external_media.ExternalMediaProjectRenderView._enqueue'):
                approved = self.edit('approve', {})
            self.assertEqual(approved.status_code, 200, approved.content)
            ExternalMediaProjectPipeline().render(self.project.pk)

            self.project.refresh_from_db()
            self.assertEqual(self.project.status, self.project.Status.FINISHED)
            self.assertContains(self.client.get(self.url('detail')), 'Edição livre')
            job = self.project.render_job
            self.assertEqual(job.rendered_from_timeline_revision, self.project.approved_timeline_revision.revision)
            intervals = self.project.configuration['render_contract']['source_intervals']
            self.assertEqual([(i['start_ms'], i['end_ms']) for i in intervals], [(0, 1250), (0, 1000), (0, 750)])
            attachment = job.assets.get(kind=MediaAsset.Kind.VIDEO)
            self.assertEqual(job.assets.get(kind=MediaAsset.Kind.SRT).language, 'pt')

            def pixel(second):
                rgb = ffmpeg('-ss', second, '-i', attachment.file.path, '-vf', 'crop=2:2:4:4,format=rgb24',
                             '-frames:v', '1', '-f', 'rawvideo', '-')
                return max(range(3), key=list(rgb[:3]).__getitem__)

            self.assertEqual([pixel(second) for second in (.15, 1.15, 2.1, 2.6)], [2, 1, 0, 0])
            audio_stream = subprocess.check_output([
                settings.FFPROBE_BINARY, '-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=codec_type',
                '-of', 'csv=p=0', attachment.file.path,
            ])
            self.assertEqual(audio_stream.strip(), b'audio')

    def test_silent_free_video_can_render_without_captions(self):
        if not shutil.which(settings.FFMPEG_BINARY) or not shutil.which(settings.FFPROBE_BINARY):
            self.skipTest('FFmpeg e FFprobe são necessários para validar o arquivo exportado.')
        self.open_project()
        video = subprocess.check_output([
            settings.FFMPEG_BINARY, '-v', 'error', '-f', 'lavfi', '-i',
            'color=red:size=160x90:rate=30:duration=1', '-c:v', 'libx264',
            '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', '-',
        ])
        item = self.upload('silencioso.mp4', video, ready=False)
        create_project_preview.run(item.pk)
        self.insert(item)
        cut = self.edit('cut_create', {'start_ms': 200, 'end_ms': 500})
        self.assertEqual(cut.status_code, 200, cut.content)
        with patch('website.views.external_media.ExternalMediaProjectRenderView._enqueue'):
            self.assertEqual(self.edit('approve', {}).status_code, 200)
        self.project.refresh_from_db()
        self.project.configuration.pop('proxy_pipeline', None)
        self.project.save(update_fields=['configuration'])
        ExternalMediaProjectPipeline().render(self.project.pk)
        self.project.refresh_from_db()
        self.assertEqual(self.project.status, self.project.Status.FINISHED)
        self.assertEqual(self.project.configuration['render_contract']['expected_duration_ms'], 700)
        self.assertTrue(self.project.configuration['quality_report']['metrics']['audio']['silent'])
        self.assertFalse(self.project.render_job.assets.filter(kind=MediaAsset.Kind.SRT).exists())

    def test_existing_template_renders_the_approved_order_with_an_inserted_clip(self):
        if not shutil.which(settings.FFMPEG_BINARY) or not shutil.which(settings.FFPROBE_BINARY):
            self.skipTest('FFmpeg e FFprobe são necessários para validar os quadros exportados.')
        template = MediaTemplate.objects.create(name='Cenas', slug='cenas')
        version = MediaTemplateVersion.objects.create(
            template=template, preset=self.preset, subtitle_style=self.style, subtitles_enabled=False,
        )
        block = MediaTemplateBlock.objects.create(version=version, name='Cena', order=1, key='cena')
        self.project = ExternalMediaProject.objects.create(
            name='Montagem do modelo', template_version=version, created_by=self.member,
        )
        def source_video(color):
            return subprocess.check_output([
                settings.FFMPEG_BINARY, '-v', 'error', '-f', 'lavfi', '-i',
                f'color={color}:size=160x90:rate=30:duration=2', '-f', 'lavfi', '-i',
                'sine=frequency=440:duration=2', '-c:v', 'libx264', '-c:a', 'aac',
                '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', '-',
            ])

        ProjectBlockMedia.objects.create(
            project=self.project, block=block, duration_ms=2000, original_filename='red.mp4',
            file=SimpleUploadedFile('red.mp4', source_video('red')),
        )
        pipeline = ExternalMediaProjectPipeline()
        pipeline.run(self.project.pk)
        self.project.refresh_from_db()
        original = clip_range(self.project.current_timeline_revision.timeline['clips'][0])
        extra = self.upload('blue.mp4', source_video('blue'), ready=False)
        create_project_preview.run(extra.pk)
        timeline = self.insert(extra, 500, 1500)
        response = self.edit('video_move', {**clip_range(timeline['clips'][1]), 'before': original})
        self.assertEqual(response.status_code, 200, response.content)
        with patch('website.views.external_media.ExternalMediaProjectRenderView._enqueue'):
            approved = self.edit('approve', {})
        self.assertEqual(approved.status_code, 200, approved.content)
        pipeline.render(self.project.pk)

        self.project.refresh_from_db()
        self.assertEqual(self.project.status, self.project.Status.FINISHED)
        output = self.project.render_job.assets.get(kind=MediaAsset.Kind.VIDEO)
        frame = subprocess.check_output([
            settings.FFMPEG_BINARY, '-v', 'error', '-ss', '0.25', '-i', output.file.path,
            '-vf', 'crop=2:2:4:4,format=rgb24', '-frames:v', '1', '-f', 'rawvideo', '-',
        ])
        self.assertGreater(frame[2], frame[0])
        self.assertEqual(
            [interval['source_id'] for interval in self.project.configuration['render_contract']['source_intervals']],
            [clip['asset_id'] for clip in response.json()['timeline']['clips']],
        )
