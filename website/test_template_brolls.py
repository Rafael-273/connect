import json
import io
import tempfile
from unittest.mock import Mock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.storage import FileSystemStorage
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from website.forms.admin_external_media import AdminMediaTemplateBlockForm
from website.external_media.services import ExternalMediaProjectPipeline
from website.models.external_media import (
    ExternalMediaProject, MediaTemplate, MediaTemplateBlock, MediaTemplateVersion,
    ProjectBlockMedia, ProjectBrollAsset, TemplateBrollAsset,
)
from website.models.ministry_membership import MinistryMembership
from website.test_external_media import ExternalMediaFixtureMixin


class TemplateBrollTests(ExternalMediaFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.storage_settings = override_settings(MEDIA_ROOT=self.directory.name)
        self.storage_settings.enable()
        self.addCleanup(self.storage_settings.disable)
        storage = FileSystemStorage(location=self.directory.name)
        for model in (TemplateBrollAsset, ProjectBrollAsset):
            storage_patch = patch.object(model._meta.get_field('file'), 'storage', storage)
            storage_patch.start()
            self.addCleanup(storage_patch.stop)
        template = MediaTemplate.objects.create(name='Convite', slug='broll-convite', is_active=True)
        self.version = MediaTemplateVersion.objects.create(template=template, preset=self.preset, subtitle_style=self.style)
        self.block = MediaTemplateBlock.objects.create(version=self.version, key='convite', name='Convite')

    def test_multiple_uploads_edits_removal_and_project_snapshot(self):
        entries = [{'upload': 'a', 'description': 'Pessoas adorando'}, {'upload': 'b', 'description': 'Congregação'}]
        data = {'name': 'Convite', 'key': 'convite', 'brolls_raw': json.dumps(entries)}
        files = {'broll-a': SimpleUploadedFile('a.mp4', b'video-a'), 'broll-b': SimpleUploadedFile('b.mp4', b'video-b')}
        form = AdminMediaTemplateBlockForm(data, files, instance=self.block)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        form.save_brolls()
        originals = list(self.block.default_brolls.all())
        self.assertEqual([item.description for item in originals], ['Pessoas adorando', 'Congregação'])
        MinistryMembership.objects.create(member=self.member, ministry=self.ministry)
        self.client.force_login(self.user)
        with patch('website.views.external_media.ExternalMediaProjectBrollUploadView._enqueue_preview'):
            response = self.client.post(reverse('external_media_create'), {'name': 'Projeto', 'template_version': self.version.pk})
        self.assertEqual(response.status_code, 302)
        project = self.member.external_media_projects.get(name='Projeto')
        copied = list(project.broll_assets.all())
        self.assertEqual([item.description for item in copied], ['Pessoas adorando', 'Congregação'])
        self.assertNotEqual(copied[0].file.name, originals[0].file.name)
        data['brolls_raw'] = json.dumps([{'id': originals[1].pk, 'description': 'Pessoas reunidas'}])
        form = AdminMediaTemplateBlockForm(data, instance=self.block)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        form.save_brolls()
        self.assertEqual(list(self.block.default_brolls.values_list('description', flat=True)), ['Pessoas reunidas'])
        self.assertEqual(list(project.broll_assets.values_list('description', flat=True)), ['Pessoas adorando', 'Congregação'])

    def test_existing_project_imports_template_brolls_once(self):
        project = ExternalMediaProject.objects.create(
            name='Projeto anterior', template_version=self.version, created_by=self.member,
            status=ExternalMediaProject.Status.DRAFT,
        )
        TemplateBrollAsset.objects.create(
            block=self.block, original_filename='foto.jpeg', description='Área para crianças',
            file=SimpleUploadedFile('foto.jpeg', b'image'),
        )
        MinistryMembership.objects.create(member=self.member, ministry=self.ministry)
        self.client.force_login(self.user)
        detail = self.client.get(reverse('external_media_project_detail', args=[project.public_id]))
        self.assertContains(detail, 'Adicionar B-rolls do modelo')
        url = reverse('external_media_project_broll_upload', args=[project.public_id, self.block.pk])
        self.client.post(url, {'import_template_brolls': '1'})
        asset = project.broll_assets.get()
        self.assertEqual(asset.description, 'Área para crianças')
        self.assertEqual(asset.media_type, 'IMAGE')
        self.client.post(url, {'import_template_brolls': '1'})
        self.assertEqual(project.broll_assets.count(), 1)

    def test_image_is_validated_and_copied_as_image(self):
        image = io.BytesIO()
        Image.new('RGB', (8, 8), 'red').save(image, format='PNG')
        data = {'name': 'Convite', 'key': 'convite', 'brolls_raw': json.dumps([
            {'upload': 'image', 'description': 'Congregação reunida'},
        ])}
        upload = SimpleUploadedFile('reuniao.png', image.getvalue(), content_type='image/png')
        form = AdminMediaTemplateBlockForm(data, {'broll-image': upload}, instance=self.block)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        form.save_brolls()
        MinistryMembership.objects.create(member=self.member, ministry=self.ministry)
        self.client.force_login(self.user)
        self.client.post(reverse('external_media_create'), {'name': 'Imagem', 'template_version': self.version.pk})
        asset = self.member.external_media_projects.get(name='Imagem').broll_assets.get()
        self.assertEqual(asset.media_type, ProjectBrollAsset.MediaType.IMAGE)
        self.assertEqual(asset.description, 'Congregação reunida')
        with asset.file.open('rb') as copied:
            self.assertEqual(copied.read(), image.getvalue())

        bad_upload = SimpleUploadedFile('quebrada.png', b'invalid')
        invalid = AdminMediaTemplateBlockForm(data, {'broll-image': bad_upload}, instance=self.block)
        self.assertFalse(invalid.is_valid())
        self.assertIn('brolls_raw', invalid.errors)

    def test_jpeg_gif_and_bmp_are_accepted(self):
        for extension, image_format in [('jpeg', 'JPEG'), ('gif', 'GIF'), ('bmp', 'BMP')]:
            with self.subTest(extension=extension):
                buffer = io.BytesIO()
                Image.new('RGB', (8, 8), 'red').save(buffer, format=image_format)
                data = {'name': 'Convite', 'key': 'convite', 'brolls_raw': json.dumps([
                    {'upload': 'image', 'description': 'Congregação', 'name': f'foto.{extension}'},
                ])}
                upload = SimpleUploadedFile(f'foto.{extension}', buffer.getvalue())
                form = AdminMediaTemplateBlockForm(data, {'broll-image': upload}, instance=self.block)
                self.assertTrue(form.is_valid(), form.errors)

    def test_missing_upload_reports_reselection(self):
        data = {'name': 'Convite', 'key': 'convite', 'brolls_raw': json.dumps([
            {'upload': 'image', 'description': 'Congregação', 'name': 'foto.jpeg'},
        ])}
        form = AdminMediaTemplateBlockForm(data, instance=self.block)
        self.assertFalse(form.is_valid())
        self.assertIn('Selecione novamente', str(form.errors['brolls_raw']))

    def test_direct_render_receives_brolls_in_their_blocks_without_transcription(self):
        self.version.interactive_preview_enabled = False
        self.version.subtitles_enabled = False
        self.version.translated_subtitles_enabled = False
        self.version.save(update_fields=['interactive_preview_enabled', 'subtitles_enabled', 'translated_subtitles_enabled'])
        other = MediaTemplateBlock.objects.create(version=self.version, key='final', name='Final', order=2)
        job = self.make_job()
        project = ExternalMediaProject.objects.create(
            name='Projeto sem revisão', template_version=self.version, created_by=self.member, render_job=job,
        )
        ranges = []
        for index, block in enumerate([self.block, other]):
            ProjectBlockMedia.objects.create(
                project=project, block=block, duration_ms=5000, original_filename='take.mp4',
                file=SimpleUploadedFile('take.mp4', b'video'),
            )
            ProjectBrollAsset.objects.create(
                project=project, block=block, media_type=ProjectBrollAsset.MediaType.IMAGE,
                original_filename='foto.png', file=SimpleUploadedFile('foto.png', b'image'),
            )
            ranges.append({'block_key': block.key, 'start_ms': index * 5000, 'end_ms': (index + 1) * 5000})

        pipeline = ExternalMediaProjectPipeline()
        pipeline.assembly.assemble = Mock(return_value=False)
        pipeline.assembly._duration_ms = Mock(return_value=10000)
        pipeline.assembly.last_block_ranges = ranges
        pipeline._materialize = Mock(return_value=([], None, None))
        pipeline._create_render_job = Mock(return_value=job)
        pipeline.render = Mock()
        with patch('website.external_media.broll.get_ai_service') as ai:
            pipeline.run(project.pk)
        ai.assert_not_called()
        pipeline.render.assert_called_once_with(project.pk)
        project.refresh_from_db()
        self.assertEqual([
            (item['block_key'], item['start_ms'], item['end_ms'])
            for item in project.current_timeline_revision.timeline['brolls']
        ], [('convite', 0, 3000), ('final', 5000, 8000)])
