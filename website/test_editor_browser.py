"""Opt-in browser checks against disposable Django/PostgreSQL fixtures."""
import os
import subprocess
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.urls import reverse

from website.test_editor_persistence import EditorFixture
from website.external_media.preview import TimelineRevisionService


@skipUnless(os.environ.get('FILADELFIA_PLAYWRIGHT_MODULE'), 'Set FILADELFIA_PLAYWRIGHT_MODULE to run Chrome checks')
class EditorBrowserTests(EditorFixture, StaticLiveServerTestCase):
    def browser_check(self, filename, extra_env=None, path=None):
        env = {
            **os.environ,
            **(extra_env or {}),
            'FILADELFIA_QA_URL': self.live_server_url + (path or reverse(
                'external_media_project_preview', args=[self.project.public_id],
            )),
            'FILADELFIA_QA_SESSION': self.client.cookies[settings.SESSION_COOKIE_NAME].value,
        }
        result = subprocess.run(
            ['node', str(Path(settings.BASE_DIR) / 'static' / 'tests' / filename)],
            env=env, capture_output=True, text=True, timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout.strip())

    def test_demands_header_mobile_navigation(self):
        self.member.ministry_memberships.filter(ministry=self.ministry).update(role='leader')
        self.browser_check('demands_header.browser.cjs', path=reverse('media_content_list'))

    def test_event_types_mobile_layout(self):
        from website.models import MediaEventType
        self.member.ministry_memberships.filter(ministry=self.ministry).update(role='leader')
        MediaEventType.objects.create(name='Tipo de evento de teste mobile com nome comprido')
        self.browser_check('event_types_mobile.browser.cjs', path=reverse('media_event_type_list'))

    def test_template_detail_mobile_layout(self):
        from website.models.media_event_type import MediaEventType, MediaPlanningTemplate, MediaPlanningTemplateItem, MediaPlanningTemplateItemStep
        self.member.ministry_memberships.filter(ministry=self.ministry).update(role='leader')
        event_type = MediaEventType.objects.create(name='Conferência especial de teste')
        template = MediaPlanningTemplate.objects.create(event_type=event_type, name='Conferência')
        item = MediaPlanningTemplateItem.objects.create(template=template, title='Arte principal para divulgação', content_type='design')
        MediaPlanningTemplateItemStep.objects.create(template_item=item, title='Designer', due_offset_days=-40)
        MediaPlanningTemplateItemStep.objects.create(template_item=item, title='Publicador', due_offset_days=-2)
        self.browser_check('template_detail_mobile.browser.cjs', path=reverse('media_template_detail', args=[template.pk]))

    def test_calendar_mobile_layout(self):
        from django.utils import timezone
        from website.models import MediaContent, MediaTask
        self.member.ministry_memberships.filter(ministry=self.ministry).update(role='leader')
        content = MediaContent.objects.create(title='Campanha do calendário', content_type='video')
        MediaTask.objects.create(content=content, title='Revisar vídeo', due_date=timezone.now(), assigned_to=self.user)
        self.browser_check('calendar_mobile.browser.cjs', {'FILADELFIA_QA_HUB': self.live_server_url + reverse('media_content_list')}, path=reverse('media_dashboard'))

    def test_demand_steps_mobile_layout(self):
        from django.utils import timezone
        from website.models import MediaContent, MediaTask
        from website.models.content_delivery import MediaDeliveryRequirement
        self.member.ministry_memberships.filter(ministry=self.ministry).update(role='leader')
        content = MediaContent.objects.create(title='Campanha especial', content_type='video')
        completed = MediaTask.objects.create(content=content, title='Designer de conteúdo', assigned_to=self.user, due_date=timezone.now(), status='completed')
        task = MediaTask.objects.create(content=content, title='Publicador de conteúdo com título comprido', description='Revisar as informações antes da publicação.')
        MediaDeliveryRequirement.objects.create(task=task, format='video', label='Vídeo')
        self.browser_check('demands_steps.browser.cjs', path=reverse('media_demand_detail_page', args=[content.pk]))

    def test_event_mobile_details(self):
        from django.utils import timezone
        from website.models import Event, MediaContent, MediaEventType
        self.member.ministry_memberships.filter(ministry=self.ministry).update(role='leader')
        today = timezone.localdate()
        event_type = MediaEventType.objects.create(name='Conferência de teste mobile')
        event = Event.objects.create(title='Conferência especial', description='Orientações do evento', event_date=today, display_start=today, display_end=today, event_type=event_type)
        MediaContent.objects.create(title='Vídeo de divulgação', content_type='video', event=event)
        self.browser_check('event_mobile.browser.cjs', {'FILADELFIA_QA_HUB': self.live_server_url + reverse('media_content_list')}, path=reverse('media_event_detail_page', args=[event.pk]))

    def test_delivery_review_sheet_and_downloads(self):
        import base64
        from unittest.mock import patch
        from django.core.files.storage import FileSystemStorage
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        from website.models import MediaContent, MediaTask, MediaDelivery, MediaDeliveryRequirement
        from website.models.content_delivery import ContentFile
        self.member.ministry_memberships.filter(ministry=self.ministry).update(role='leader')
        settings_override = override_settings(CONTENT_FILES_ROOT=settings.MEDIA_ROOT)
        settings_override.enable()
        self.addCleanup(settings_override.disable)
        storage_patch = patch.object(ContentFile._meta.get_field('file'), 'storage', FileSystemStorage(location=settings.MEDIA_ROOT))
        storage_patch.start()
        self.addCleanup(storage_patch.stop)
        content = MediaContent.objects.create(title='Campanha', content_type='video')
        task = MediaTask.objects.create(content=content, title='Produção', assigned_to=self.user)
        image = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')
        for label, name, mime, data in [('Feed','imagem.png','image/png',image),('Vídeo','video.mp4','video/mp4',b'video-de-teste')]:
            requirement = MediaDeliveryRequirement.objects.create(task=task, format=label.lower().replace('í','i'), label=label)
            asset = ContentFile.objects.create(file=SimpleUploadedFile(name, data, content_type=mime), original_name=name, mime_type=mime, size=len(data), created_by=self.user, ready=True)
            self.addCleanup(asset.file.delete, save=False)
            MediaDelivery.objects.create(requirement=requirement, asset=asset, version=1, original_name=name, submitted_by=self.user, status='approved')
        self.browser_check('delivery_review_sheet.browser.cjs', path=reverse('media_demand_detail_page', args=[content.pk]))

    def test_mobile_touch_editing_and_responsive_workspace(self):
        self.browser_check('editor_mobile.browser.cjs')

    def test_transform_handles_on_desktop_and_mobile(self):
        self.browser_check('editor_transform_handles.browser.cjs')

    def test_deselect_by_clicking_empty_preview_area(self):
        self.browser_check('editor_preview_deselect.browser.cjs')

    def test_mobile_real_media_preview(self):
        self.browser_check('editor_media.browser.cjs', {'FILADELFIA_QA_MOBILE': '1'})

    def test_caption_style_matches_output_canvas(self):
        self.browser_check('editor_caption_style.browser.cjs')

    def prepare_template_music(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        version = self.project.template_version
        version.music_file = SimpleUploadedFile('default.wav', b'test-music')
        version.save(update_fields=['music_file'])
        self.addCleanup(version.music_file.delete, save=False)

    def test_remove_template_music(self):
        self.prepare_template_music()
        self.browser_check('editor_music_remove.browser.cjs')

    def test_remove_template_music_on_mobile(self):
        self.prepare_template_music()
        self.browser_check('editor_music_remove.browser.cjs', {'FILADELFIA_QA_MOBILE': '1'})

    def test_retained_nodes_focus_and_large_timeline(self):
        self.browser_check('editor_fluency.browser.cjs')

    def test_real_media_scrubbing_playback_and_waveforms(self):
        self.browser_check('editor_media.browser.cjs')

    def test_caption_visibility_can_be_restored_after_save_and_reload(self):
        self.browser_check('editor_caption_visibility.browser.cjs')

    def test_mobile_caption_icon_toggles_visibility_after_reload(self):
        self.browser_check('editor_caption_visibility.browser.cjs', {'FILADELFIA_QA_MOBILE': '1'})

    def test_magnetic_alignment_precision_and_history(self):
        self.browser_check('editor_precision.browser.cjs')

    def test_framing_without_an_automatic_reframe_decision(self):
        self.preset.width = 1080
        self.preset.height = 1920
        self.preset.save(update_fields=['width', 'height'])
        self.assertFalse(any(item['type'] == 'reframe' for item in self.project.current_timeline_revision.timeline['decisions']))
        TimelineRevisionService.create_video_split(self.project, self.member, 5000)
        self.browser_check('editor_framing.browser.cjs')

    def test_optimistic_cut_deletion_and_retry(self):
        for point in (2000, 4000, 6000):
            TimelineRevisionService.create_video_split(self.project, self.member, point)
        self.browser_check('editor_cut.browser.cjs')

    def test_soundtrack_transitions_and_optimistic_saves(self):
        import io
        import math
        import struct
        import wave
        from django.core.files.uploadedfile import SimpleUploadedFile
        from website.models.external_media import BackgroundMusicTrack
        tracks = []
        for frequency in (240, 480):
            data = io.BytesIO()
            with wave.open(data, 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(8000)
                output.writeframes(b''.join(struct.pack('<h', round(4000 * math.sin(2 * math.pi * frequency * i / 8000))) for i in range(96000)))
            tracks.append(BackgroundMusicTrack.objects.create(name=f'Música {frequency}', audio_file=SimpleUploadedFile(f'music-{frequency}.wav', data.getvalue())))
        TimelineRevisionService.update_music_tracks(self.project, self.member, [
            {'track_id': tracks[0].pk, 'start_ms': 0, 'end_ms': 5000},
            {'track_id': tracks[1].pk, 'start_ms': 5000, 'end_ms': 10000, 'source_in_ms': 1000},
        ])
        self.browser_check('editor_music_transitions.browser.cjs')

    def test_free_timeline_reorders_with_touch_and_mouse(self):
        import shutil

        from django.core.files.uploadedfile import SimpleUploadedFile

        from website.external_media.tasks import create_project_preview
        from website.models.external_media import ExternalMediaProject, ProjectBlockMedia

        if not shutil.which(settings.FFMPEG_BINARY):
            self.skipTest('FFmpeg is required for browser playback')
        self.preset.width, self.preset.height = 320, 180
        self.preset.save(update_fields=['width', 'height'])
        self.project = ExternalMediaProject.objects.create(
            name='Edição livre de teste', free_preset=self.preset, created_by=self.member,
            status=ExternalMediaProject.Status.AWAITING_REVIEW,
        )
        video = subprocess.check_output([
            settings.FFMPEG_BINARY, '-v', 'error', '-f', 'lavfi', '-i',
            'testsrc2=size=320x180:rate=30:duration=4', '-c:v', 'libx264',
            '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', '-',
        ])
        for index in range(9):
            item = ProjectBlockMedia.objects.create(
                project=self.project, original_filename=f'Fonte {index}.mp4', duration_ms=4000, editor_only=True,
                file=SimpleUploadedFile(f'take-{index}.mp4', video),
            )
            create_project_preview.run(item.pk)
        self.browser_check('editor_free_reorder.browser.cjs')

    def test_caption_context_menu_mouse_and_touch(self):
        self.browser_check('editor_caption_context.browser.cjs')
