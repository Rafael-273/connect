import base64
import datetime
import hashlib
import json
import tempfile
import uuid
from unittest.mock import MagicMock, patch

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile as DjangoFile
from django.core.files.storage import storages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.http import QueryDict
from django.test import TestCase, override_settings
from django.urls import reverse

from website.models import (
    ContentFile, Event, ExternalMediaJob, ExternalMediaProject, MediaAsset,
    MediaContent, MediaDelivery, MediaDeliveryRequirement, MediaTask, MediaTemplate,
    MediaTemplateVersion, Member, Ministry, MinistryMembership, RenderPreset, SubtitleStyle, User,
)
from website.services import content_deliveries as service
from website.services.demands_hub import build_assignment_rows, parse_demand_assignments, sync_content_assignments

PNG = b'\x89PNG\r\n\x1a\n' + b'a' * 80
VIDEO = b'\x00\x00\x00\x18ftypmp42' + b'x' * 64


class ContentDeliveryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ministry, _ = Ministry.objects.get_or_create(code='midia_externa', defaults={'name': 'Mídia Externa'})
        for name, role in [('worker', 'member'), ('other', 'member'), ('leader', 'leader')]:
            user = User.objects.create_user(f'delivery-{name}@example.test')
            member = Member.objects.create(name=name, user=user)
            MinistryMembership.objects.create(ministry=cls.ministry, member=member, role=role)
            setattr(cls, name, user)
        cls.outsider = User.objects.create_user('delivery-outsider@example.test')
        Member.objects.create(user=cls.outsider, name='Outsider')
        cls.content = MediaContent.objects.create(title='Arte avulsa', content_type='artwork')
        cls.task = MediaTask.objects.create(content=cls.content, title='Criar arte', assigned_to=cls.worker)
        cls.preset = RenderPreset.objects.create(name='Horizontal', code='delivery-horizontal', width=1920, height=1080)
        cls.style = SubtitleStyle.objects.create(name='Delivery test')
        template = MediaTemplate.objects.create(name='Delivery template', slug='delivery-template')
        version = MediaTemplateVersion.objects.create(template=template, preset=cls.preset)
        cls.project = ExternalMediaProject.objects.create(name='Chamada', template_version=version, created_by=cls.worker.member)
        cls.job = ExternalMediaJob.objects.create(name='Render', preset=cls.preset, subtitle_style=cls.style,
            created_by=cls.worker.member, status='FINISHED', processing_project=cls.project)
        cls.project.render_job = cls.job
        cls.project.save()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.override = override_settings(CONTENT_FILES_ROOT=self.directory.name,
            STORAGES={'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
                'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
                'external_media': {'BACKEND': 'django.core.files.storage.FileSystemStorage', 'OPTIONS': {'location': self.directory.name}}})
        self.override.enable()
        self.addCleanup(self.override.disable)
        from website.content_file_storage import get_content_file_storage
        # FileField resolves its callable storage at model import; patch the
        # fields too so tests never write into the developer's media directory.
        for model, storage in [(ContentFile, get_content_file_storage()), (MediaAsset, storages['external_media'])]:
            storage_patch = patch.object(model._meta.get_field('file'), 'storage', storage)
            storage_patch.start()
            self.addCleanup(storage_patch.stop)
        self.client.force_login(self.worker)

    def decide(self, decision='rejected', note='Ajustar o arquivo'):
        from website.services.delivery_reviews import decide_review, review_token
        task = MediaTask.objects.get(pk=self.task.pk)
        requirement = task.delivery_requirements.filter(active=True, deliveries__status='submitted').distinct().first()
        return decide_review(task.pk, self.leader, requirement.pk, decision, note, review_token(task))

    def confirm_corrections(self):
        response = self.client.post(reverse('media_my_work_status', args=[self.task.pk]), {'status': 'completed'})
        self.assertEqual(response.status_code, 302, response.content)

    def requirement(self, code='story'):
        return MediaDeliveryRequirement.objects.create(task=self.task, format=code, label=dict(service.FORMATS)[code])

    def post(self, requirement, **data):
        return self.client.post(reverse('media_delivery_action', args=[requirement.pk]), json.dumps(data), content_type='application/json')

    def upload(self, requirement, content=PNG, submission=None):
        begin = self.post(requirement, action='begin', name='story.png', size=len(content), mime='image/png', sha256=hashlib.sha256(content).hexdigest())
        self.assertEqual(begin.status_code, 200, begin.content)
        data = begin.json()
        response = self.client.post(reverse('media_delivery_action', args=[requirement.pk]), {
            'action': 'complete', 'ticket': data['ticket'], 'submission_id': str(submission or uuid.uuid4()),
            'name': 'story.png', 'file': SimpleUploadedFile('story.png', content, 'image/png'),
        })
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_art_multiselect_and_other(self):
        service.configure_requirements(self.task, ['feed', 'story', 'telao', 'other'], 'Banner do site')
        self.assertEqual(self.task.delivery_requirements.count(), 4)
        self.assertTrue(self.task.delivery_requirements.filter(format='other', label='Banner do site').exists())
        self.assertEqual(self.task.delivery_requirements.get(format='story').metadata['guidance'], '9:16 · 1080 × 1920 recomendado')

    def test_video_multiselect(self):
        self.content.content_type = 'video'; self.content.save()
        service.configure_requirements(self.task, ['reels', 'telao'])
        self.assertEqual(set(self.task.delivery_requirements.values_list('format', flat=True)), {'reels', 'telao'})

    def test_other_requires_name_and_unknown_format_rejected(self):
        for formats, name in [(['other'], ''), (['unknown'], ''), (['other'], 'x' * 101)]:
            with self.assertRaises(ValidationError):
                service.configure_requirements(self.task, formats, name)

    def test_assignment_configuration_roundtrip(self):
        post = QueryDict('', mutable=True)
        post.setlist('demand-assignment_role', ['Criar arte'])
        post.setlist('demand-assignment_user', [str(self.worker.pk)])
        post.setlist('demand-assignment_id', [str(self.task.pk)])
        post.setlist('demand-assignment_formats', ['["feed", "story"]'])
        rows = parse_demand_assignments(post, absolute_dates=True)
        sync_content_assignments(self.content, rows)
        self.assertEqual(self.task.delivery_requirements.count(), 2)
        self.assertEqual(set(json.loads(build_assignment_rows(self.content)[0]['delivery_formats_json'])), {'feed', 'story'})

    def test_manual_upload_links_right_step_without_completing(self):
        requirement = self.requirement()
        result = self.upload(requirement)
        delivery = MediaDelivery.objects.get()
        self.assertEqual(delivery.requirement.task_id, self.task.pk)
        self.assertEqual(delivery.asset.sha256, hashlib.sha256(PNG).hexdigest())
        self.assertTrue(delivery.asset.ready)
        self.assertEqual(result['version'], 1)
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'in_review')

    def test_dedup_does_not_save_or_upload_again(self):
        requirement = self.requirement()
        self.upload(requirement)
        asset = ContentFile.objects.get()
        self.decide()
        with patch.object(type(asset.storage), 'save', side_effect=AssertionError('duplicate upload')):
            self.upload(requirement)
        self.assertEqual(ContentFile.objects.count(), 1)
        self.assertEqual(MediaDelivery.objects.count(), 2)

    def test_history_preserved_and_retry_idempotent(self):
        req = self.requirement()
        submission = uuid.uuid4()
        self.upload(req, submission=submission)
        existing = MediaDelivery.objects.get()
        self.assertEqual(service.deliver(req, existing.asset, self.worker, submission).pk, existing.pk)
        self.assertEqual(MediaDelivery.objects.count(), 1)
        self.decide()
        self.upload(req, PNG + b'new')
        self.assertEqual(list(req.deliveries.values_list('version', flat=True)), [2, 1])
        self.assertEqual(ContentFile.objects.count(), 2)

    def test_required_deliveries_block_all_completion_paths(self):
        req = self.requirement()
        response = self.client.post(reverse('media_my_work_status', args=[self.task.pk]), {'status': 'completed'})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, service.MESSAGE, status_code=400)
        self.task.status = 'completed'
        with self.assertRaises(ValidationError): self.task.save()
        self.client.force_login(self.leader)
        response = self.client.post(reverse('media_task_update_status', args=[self.task.pk]), {'status': 'completed'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(req.deliveries.exists())

    def test_approval_completes_when_all_required_files_exist(self):
        req = self.requirement(); self.upload(req)
        response = self.client.post(reverse('media_my_work_status', args=[self.task.pk]), {'status': 'completed'})
        self.assertEqual(response.status_code, 400)
        self.decide('approved')
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'completed')

    def test_no_requirements_preserves_legacy_completion(self):
        response = self.client.post(reverse('media_my_work_status', args=[self.task.pk]), {'status': 'completed'})
        self.assertEqual(response.status_code, 302)
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'completed')

    def test_requirement_changes_keep_history_and_reopen_step(self):
        req = self.requirement(); self.upload(req)
        self.decide('approved')
        service.configure_requirements(self.task, ['feed'])
        self.assertEqual(req.deliveries.count(), 1)
        req.refresh_from_db(); self.assertFalse(req.active)
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'pending')

    def test_member_cannot_submit_or_read_others_deliveries(self):
        req = self.requirement(); self.upload(req)
        self.client.force_login(self.other)
        self.assertEqual(self.post(req, action='begin').status_code, 404)
        self.assertEqual(self.client.get(reverse('media_delivery_file', args=[MediaDelivery.objects.get().pk])).status_code, 404)

    def test_leader_can_download_and_read_history_but_not_submit(self):
        req = self.requirement(); self.upload(req); self.decide(); self.upload(req, PNG + b'new')
        self.confirm_corrections()
        self.client.force_login(self.leader)
        response = self.client.get(reverse('media_delivery_file', args=[MediaDelivery.objects.first().pk]))
        self.assertEqual(response.status_code, 200); response.close()
        self.assertEqual(self.post(req, action='begin').status_code, 404)
        response = self.client.get(reverse('media_demand_detail_page', args=[self.content.pk]))
        self.assertContains(response, 'Revisar entregas')
        self.assertNotContains(response, 'story.png')
        response = self.client.get(reverse('media_delivery_review', args=[self.task.pk]))
        self.assertContains(response, 'Prévia da entrega Story')
        self.assertNotContains(response, 'Versões anteriores')
        self.assertNotContains(response, 'story.png')

    def test_outsider_rejected(self):
        req = self.requirement(); self.client.force_login(self.outsider)
        self.assertEqual(self.post(req, action='begin').status_code, 403)

    def test_event_and_free_demands(self):
        req = self.requirement(); self.upload(req)
        self.assertIsNone(self.content.event_id)
        event = Event.objects.create(title='Conferência', event_date=datetime.date(2026, 11, 11), display_start=datetime.date(2026, 11, 1), display_end=datetime.date(2026, 11, 11))
        self.content.event = event; self.content.save()
        self.decide()
        self.upload(req, PNG + b'event')
        self.assertEqual(req.deliveries.count(), 2)

    def test_bad_content_and_mime_rejected(self):
        req = self.requirement()
        self.assertEqual(self.post(req, action='begin', name='image.png', mime='video/mp4', size=5, sha256='a' * 64).status_code, 400)
        payload = b'not a PNG'
        begin = self.post(req, action='begin', name='image.png', mime='image/png', size=len(payload), sha256=hashlib.sha256(payload).hexdigest()).json()
        response = self.client.post(reverse('media_delivery_action', args=[req.pk]), {
            'action': 'complete', 'ticket': begin['ticket'], 'submission_id': str(uuid.uuid4()),
            'file': SimpleUploadedFile('image.png', payload),
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(MediaDelivery.objects.exists())

    def test_forged_hash_and_cross_requirement_ticket_rejected(self):
        req = self.requirement(); other = self.requirement('feed')
        result = service.begin_upload(req, self.worker, 'a.png', len(PNG), 'image/png', 'a' * 64)
        with self.assertRaises(PermissionDenied): service.ticket_asset(result['ticket'], self.worker, other)
        asset = ContentFile.objects.get()
        with self.assertRaises(ValidationError): service.save_local(asset, SimpleUploadedFile('a.png', PNG))
        self.assertFalse(asset.ready)

    def test_unique_hash_and_versions_enforced_in_database(self):
        req = self.requirement(); self.upload(req)
        asset = ContentFile.objects.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            ContentFile.objects.create(file='elsewhere.png', sha256=asset.sha256, size=asset.size, mime_type=asset.mime_type, original_name='x')
        delivery = MediaDelivery.objects.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            MediaDelivery.objects.create(requirement=req, asset=asset, version=delivery.version, submitted_by=self.worker, original_name='x')

    def test_parallel_upload_reservation_before_s3(self):
        req = self.requirement()
        service.begin_upload(req, self.worker, 'a.png', len(PNG), 'image/png', hashlib.sha256(PNG).hexdigest())
        with self.assertRaises(ValidationError):
            service.begin_upload(req, self.other, 'a.png', len(PNG), 'image/png', hashlib.sha256(PNG).hexdigest())
        self.assertEqual(ContentFile.objects.count(), 1)

    def test_s3_large_video_signed_put_and_checksum_verification(self):
        self.content.content_type = 'video'; self.content.save()
        req = self.requirement('reels')
        storage = MagicMock(); storage.bucket_name = 'private'; storage.location = 'private_media'
        storage.connection.meta.client.generate_presigned_url.return_value = 'https://signed.test/upload'
        with patch.object(ContentFile, 'storage', new_callable=lambda: property(lambda _: storage)):
            result = service.begin_upload(req, self.worker, 'reels.mp4', 800 * 1024 * 1024, 'video/mp4', 'f' * 64)
        self.assertEqual(result['upload_url'], 'https://signed.test/upload')
        params = storage.connection.meta.client.generate_presigned_url.call_args.kwargs['Params']
        self.assertEqual(params['ContentLength'], 800 * 1024 * 1024)
        self.assertEqual(params['ChecksumSHA256'], base64.b64encode(bytes.fromhex('f' * 64)).decode())
        self.assertIn('x-amz-checksum-sha256', result['headers'])

    def test_s3_confirmation_rejects_wrong_checksum(self):
        req = self.requirement(); service.begin_upload(req, self.worker, 'a.png', len(PNG), 'image/png', 'a' * 64)
        asset = ContentFile.objects.get(); storage = MagicMock(); storage.location = ''
        storage.connection.meta.client.head_object.return_value = {'ContentLength': len(PNG), 'ChecksumSHA256': 'wrong'}
        with patch.object(ContentFile, 'storage', new_callable=lambda: property(lambda _: storage)):
            with self.assertRaises(ValidationError): service.verify_remote(asset)
        asset.refresh_from_db(); self.assertFalse(asset.ready)

    def video_requirement(self):
        self.content.content_type = 'video'; self.content.save()
        return self.requirement('reels')

    def test_editor_project_without_export_is_not_deliverable(self):
        req = self.video_requirement()
        response = self.client.get(reverse('media_delivery_projects', args=[req.pk]), {'project': self.project.pk})
        self.assertEqual(response.json()['exports'], [])
        response = self.post(req, action='editor', source_id='99999', submission_id=str(uuid.uuid4()))
        self.assertEqual(response.status_code, 404)

    def test_editor_reuses_final_file_and_lists_dimensions(self):
        req = self.video_requirement()
        source = MediaAsset.objects.create(job=self.job, kind='VIDEO', language='pt', file_size=len(VIDEO))
        source.file.save('final.mp4', DjangoFile(VIDEO))
        service.index_render_file(source)
        response = self.client.get(reverse('media_delivery_projects', args=[req.pk]), {'project': self.project.pk})
        self.assertEqual(response.json()['exports'][0]['width'], 1920)
        with patch.object(type(source.file.storage), 'save', side_effect=AssertionError('must not upload again')):
            response = self.post(req, action='editor', source_id=str(source.pk), submission_id=str(uuid.uuid4()))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(MediaDelivery.objects.get().asset.file.name, source.file.name)
        self.assertTrue(service.is_retained_file(source.file.name))

    def test_replaced_editor_file_keeps_delivery_binary(self):
        req = self.video_requirement()
        source = MediaAsset.objects.create(job=self.job, kind='VIDEO', language='pt', file_size=len(VIDEO))
        source.file.save('old.mp4', DjangoFile(VIDEO))
        asset = service.register_editor_file(source, self.worker)
        service.deliver(req, asset, self.worker, uuid.uuid4())
        old_name = source.file.name
        from pathlib import Path
        path = Path(self.directory.name) / 'replacement.mp4'; path.write_bytes(VIDEO + b'new')
        from website.external_media.services import replace_file_safely
        replace_file_safely(source, 'file', path, 'new.mp4')
        self.assertTrue(source.file.storage.exists(old_name))
        self.assertEqual(MediaDelivery.objects.get().asset.file.name, old_name)

    def test_personal_detail_shows_deliveries(self):
        req = self.requirement(); self.upload(req)
        response = self.client.get(reverse('media_my_work_detail', args=[self.task.pk]))
        self.assertContains(response, 'Entregas')
        self.assertContains(response, 'Aguardando revisão da liderança')
        self.assertContains(response, 'Baixar')
        self.assertNotContains(response, 'V1 · atual')

    def test_legacy_editor_file_is_hashed_in_worker_before_delivery(self):
        req = self.video_requirement()
        source = MediaAsset.objects.create(job=self.job, kind='VIDEO', language='pt', file_size=len(VIDEO))
        source.file.save('legacy.mp4', DjangoFile(VIDEO))
        submission = str(uuid.uuid4())
        with patch('website.services.delivery_file_jobs.index_editor_file.delay') as delay:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.post(req, action='editor', source_id=str(source.pk), submission_id=submission)
            self.assertEqual(response.status_code, 202)
            delay.assert_called_once_with(source.pk)
        self.assertFalse(MediaDelivery.objects.exists())
        from website.services.delivery_file_jobs import index_editor_file
        index_editor_file(source.pk)
        response = self.post(req, action='editor', source_id=str(source.pk), submission_id=submission)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(MediaDelivery.objects.get().asset.sha256, hashlib.sha256(VIDEO).hexdigest())

    def test_manual_reuses_indexed_editor_file_without_upload(self):
        req = self.video_requirement()
        source = MediaAsset.objects.create(job=self.job, kind='VIDEO', language='pt', file_size=len(VIDEO))
        source.file.save('indexed.mp4', DjangoFile(VIDEO))
        service.index_render_file(source)
        with patch('website.services.content_deliveries.S3DirectUpload', side_effect=AssertionError('must not upload')):
            result = service.begin_upload(req, self.worker, 'video.mp4', len(VIDEO), 'video/mp4', hashlib.sha256(VIDEO).hexdigest())
        self.assertTrue(result['existing'])
        self.assertNotIn('upload_url', result)
        self.assertEqual(ContentFile.objects.count(), 1)

    def test_expired_upload_cannot_overwrite_current_reservation(self):
        from django.utils import timezone
        req = self.requirement()
        result = service.begin_upload(req, self.worker, 'a.png', len(PNG), 'image/png', 'a' * 64)
        asset = ContentFile.objects.get()
        asset.upload_expires_at = timezone.now() - datetime.timedelta(seconds=1)
        asset.save()
        service.begin_upload(req, self.other, 'a.png', len(PNG), 'image/png', 'a' * 64)
        with self.assertRaises(PermissionDenied): service.ticket_asset(result['ticket'], self.worker, req)

    def test_signed_download_expires_and_is_not_cached(self):
        req = self.requirement(); self.upload(req)
        storage = MagicMock(); storage.location = 'private_media'; storage.bucket_name = 'private'
        storage.connection.meta.client.generate_presigned_url.return_value = 'https://signed.test/download'
        with patch.object(ContentFile, 'storage', new_callable=lambda: property(lambda _: storage)):
            response = self.client.get(reverse('media_delivery_file', args=[MediaDelivery.objects.get().pk]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertEqual(storage.connection.meta.client.generate_presigned_url.call_args.kwargs['ExpiresIn'], 300)

    def test_inactive_requirements_do_not_block_completion(self):
        req = self.requirement(); req.active = False; req.save()
        self.task.status = 'completed'; self.task.save()
        self.assertEqual(MediaTask.objects.get(pk=self.task.pk).status, 'completed')

    def test_optional_requirement_does_not_block_completion(self):
        req = self.requirement(); req.required = False; req.save()
        self.task.status = 'completed'; self.task.save()
        self.assertEqual(MediaTask.objects.get(pk=self.task.pk).status, 'completed')

    def test_deleting_step_soft_archives_delivery_and_preserves_file(self):
        req = self.requirement(); self.upload(req)
        delivery = MediaDelivery.objects.get(); asset = delivery.asset
        self.task.delete()
        self.assertFalse(MediaDelivery.objects.filter(pk=delivery.pk).exists())
        self.assertTrue(MediaDelivery.all_objects.filter(pk=delivery.pk).exists())
        self.assertTrue(asset.storage.exists(asset.file.name))

    def test_soft_deleted_delivery_does_not_satisfy_requirement(self):
        req = self.requirement(); self.upload(req)
        MediaDelivery.objects.get().delete()
        with self.assertRaises(ValidationError): service.assert_complete(self.task)

    def test_create_demand_from_hub_saves_multiple_delivery_requirements(self):
        self.client.force_login(self.leader)
        response = self.client.post(reverse('media_demand_quick_create'), {
            'demand-title': 'Nova arte com entregas', 'demand-content_type': 'artwork',
            'demand-assignment_role': ['Criar arte'], 'demand-assignment_user': [str(self.worker.pk)],
            'demand-assignment_formats': ['["feed", "story", "other"]'],
            'demand-assignment_other_format': ['Banner do site'],
        })
        self.assertEqual(response.status_code, 302, response.content)
        task = MediaContent.objects.get(title='Nova arte com entregas').tasks.get()
        self.assertEqual(task.delivery_requirements.count(), 3)

    def test_new_render_hashing_preserves_original_delivery_when_replaced(self):
        from pathlib import Path
        from website.external_media.services import StorageService
        req = self.video_requirement()
        path = Path(self.directory.name) / 'source.mp4'; path.write_bytes(VIDEO)
        storage = StorageService()
        source = storage.save_asset(self.job, 'VIDEO', 'pt', path, 'first.mp4')
        original = source.content_file
        service.deliver(req, original, self.worker, uuid.uuid4())
        path.write_bytes(VIDEO + b'new')
        source = storage.save_asset(self.job, 'VIDEO', 'pt', path, 'second.mp4')
        self.assertNotEqual(source.content_file_id, original.pk)
        self.assertEqual(MediaDelivery.objects.get().asset_id, original.pk)
        self.assertTrue(original.storage.exists(original.file.name))

    def test_legacy_indexing_resolves_competing_manual_reservation(self):
        req = self.video_requirement()
        source = MediaAsset.objects.create(job=self.job, kind='VIDEO', language='pt', file_size=len(VIDEO))
        source.file.save('legacy-race.mp4', DjangoFile(VIDEO))
        physical = service.register_editor_file(source, self.worker)
        reserved = service.begin_upload(req, self.worker, 'video.mp4', len(VIDEO), 'video/mp4', hashlib.sha256(VIDEO).hexdigest())
        indexed = service.index_render_file(source)
        self.assertEqual(indexed.pk, physical.pk)
        self.assertTrue(indexed.ready)
        self.assertEqual(indexed.sha256, hashlib.sha256(VIDEO).hexdigest())
        with self.assertRaises(ValidationError): service.ticket_asset(reserved['ticket'], self.worker, req)

    def test_preview_is_inline_only_for_authorized_user_and_keeps_download_attachment(self):
        req = self.requirement(); self.upload(req)
        url = reverse('media_delivery_file', args=[MediaDelivery.objects.get().pk])
        preview = self.client.get(url, {'preview': '1'})
        self.assertEqual(preview.status_code, 200)
        self.assertTrue(preview['Content-Disposition'].startswith('inline;'))
        self.assertEqual(preview['X-Content-Type-Options'], 'nosniff')
        preview.close()
        download = self.client.get(url)
        self.assertTrue(download['Content-Disposition'].startswith('attachment;'))
        download.close()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url, {'preview': '1'}).status_code, 404)

    def test_personal_detail_has_image_preview_and_short_file_actions(self):
        req = self.requirement(); self.upload(req)
        response = self.client.get(reverse('media_my_work_detail', args=[self.task.pk]))
        self.assertContains(response, '?preview=1')
        self.assertContains(response, 'Prévia de story.png')
        self.assertNotContains(response, 'Versões anteriores')

    def test_personal_detail_hides_history_after_new_version(self):
        req = self.requirement()
        self.upload(req)
        self.decide()
        result = self.upload(req, PNG + b'new')
        self.assertNotIn('Versões anteriores', result['html'])
        response = self.client.get(reverse('media_my_work_detail', args=[self.task.pk]))
        self.assertNotContains(response, 'Versões anteriores')
        self.assertContains(response, 'Baixar')
        self.assertNotContains(response, 'V2 · atual')
        self.assertEqual(req.deliveries.count(), 2)

    def test_review_starts_only_when_all_required_deliveries_arrive(self):
        feed, story = self.requirement('feed'), self.requirement('story')
        self.upload(feed)
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'in_progress')
        self.upload(story, PNG + b'story')
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'in_review')
        self.content.refresh_from_db(); self.assertNotEqual(self.content.status, 'approved')
        response = self.client.get(reverse('media_my_work_detail', args=[self.task.pk]))
        self.assertContains(response, 'Em revisão')
        self.assertContains(response, 'Aguardando revisão da liderança')
        self.assertNotContains(response, 'Enviar nova versão')

    def test_leader_approves_exact_submitted_versions(self):
        req = self.requirement(); self.upload(req)
        from website.services.delivery_reviews import review_token
        self.client.force_login(self.leader)
        response = self.client.post(reverse('media_delivery_review', args=[self.task.pk]), {
            'decision': 'approved', 'note': 'Aprovado!', 'requirement_id': req.pk, 'review_token': review_token(MediaTask.objects.get(pk=self.task.pk)),
        })
        self.assertEqual(response.status_code, 302)
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'completed')
        review = self.task.delivery_reviews.get()
        self.assertEqual((review.decision, review.note, review.reviewer_id), ('approved', 'Aprovado!', self.leader.pk))
        self.assertEqual(review.snapshot[0]['delivery'], req.deliveries.get().pk)
        self.assertEqual(req.deliveries.get().status, 'approved')
        self.content.refresh_from_db(); self.assertEqual(self.content.status, 'approved')
        self.client.force_login(self.worker)
        response = self.client.get(reverse('media_my_work_detail', args=[self.task.pk]))
        self.assertContains(response, 'Aprovada')

    def test_leader_rejects_with_reason_then_member_resubmits(self):
        req = self.requirement(); self.upload(req)
        self.decide('rejected', 'Ajustar contraste e logo.')
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'in_progress')
        response = self.client.get(reverse('media_my_work_detail', args=[self.task.pk]))
        self.assertContains(response, 'Ajustar contraste e logo.')
        self.assertNotContains(response, 'Enviar nova versão')
        self.assertContains(response, 'data-delivery-remove')
        removed = self.post(req, action='withdraw', delivery_id=req.deliveries.first().pk)
        self.assertEqual(removed.status_code, 200)
        self.assertIn('Enviar arquivo', removed.json()['html'])
        self.assertIn('Ajustar contraste e logo.', removed.json()['html'])
        self.upload(req, PNG + b'alterado')
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'in_progress')
        self.confirm_corrections()
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'in_review')
        self.assertEqual(req.deliveries.count(), 2)
        self.decide('approved')
        self.assertEqual(list(self.task.delivery_reviews.values_list('decision', flat=True)), ['approved', 'rejected'])

    def test_member_cannot_review_or_force_completion(self):
        req = self.requirement(); self.upload(req)
        from website.services.delivery_reviews import review_token
        token = review_token(MediaTask.objects.get(pk=self.task.pk))
        response = self.client.post(reverse('media_delivery_review', args=[self.task.pk]), {'decision': 'approved', 'review_token': token, 'requirement_id': req.pk})
        self.assertNotEqual(response.status_code, 200)
        self.assertFalse(self.task.delivery_reviews.exists())
        self.assertEqual(self.client.post(reverse('media_my_work_status', args=[self.task.pk]), {'status': 'completed'}).status_code, 400)
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'in_review')

    def test_stale_or_replayed_review_cannot_approve_a_different_version(self):
        req = self.requirement(); self.upload(req)
        from website.services.delivery_reviews import decide_review, review_token
        task = MediaTask.objects.get(pk=self.task.pk)
        token = review_token(task)
        self.decide('rejected')
        self.upload(req, PNG + b'v2')
        self.confirm_corrections()
        with self.assertRaises(ValidationError):
            decide_review(task.pk, self.leader, req.pk, 'approved', '', token)
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'in_review')
        self.assertEqual(self.task.delivery_reviews.count(), 1)

    def test_modifying_requirements_during_review_returns_step_to_work(self):
        req = self.requirement(); self.upload(req)
        service.configure_requirements(self.task, ['story', 'feed'])
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'in_progress')
        self.assertEqual(req.deliveries.count(), 1)

    def test_leader_cannot_bypass_review_by_editing_stage_status(self):
        req = self.requirement(); self.upload(req)
        self.client.force_login(self.leader)
        response = self.client.post(reverse('media_task_update_status', args=[self.task.pk]), {'status': 'completed'})
        self.assertEqual(response.status_code, 400)
        response = self.client.post(reverse('media_task_update', args=[self.task.pk]), {
            'title': self.task.title, 'assigned_to': self.worker.pk, 'status': 'completed',
        })
        self.assertEqual(response.status_code, 302)
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'in_review')
        self.assertFalse(self.task.delivery_reviews.exists())

    def test_review_card_requires_leadership_and_reject_can_have_no_note(self):
        req = self.requirement(); self.upload(req)
        url = reverse('media_delivery_review', args=[self.task.pk])
        response = self.client.get(url)
        self.assertNotEqual(response.status_code, 200)
        self.client.force_login(self.leader)
        response = self.client.get(url, {'fragment': '1'})
        self.assertContains(response, 'Aprovar')
        self.assertContains(response, 'Reprovar')
        from website.services.delivery_reviews import review_token
        response = self.client.post(url, {'decision': 'rejected', 'note': '', 'requirement_id': req.pk,
            'review_token': review_token(MediaTask.objects.get(pk=self.task.pk))})
        self.assertEqual(response.status_code, 302)
        self.client.force_login(self.worker)
        response = self.client.get(reverse('media_my_work_detail', args=[self.task.pk]))
        self.assertContains(response, 'A liderança solicitou uma nova versão desta entrega.')

    def test_each_delivery_is_reviewed_independently_and_approved_versions_are_kept(self):
        feed, story, screen = self.requirement('feed'), self.requirement('story'), self.requirement('telao')
        self.upload(feed)
        self.upload(story, PNG + b'story')
        self.upload(screen, PNG + b'screen')
        from website.services.delivery_reviews import decide_review, review_token
        def evaluate(requirement, decision, note=''):
            task = MediaTask.objects.get(pk=self.task.pk)
            return decide_review(task.pk, self.leader, requirement.pk, decision, note, review_token(task))
        evaluate(feed, 'approved')
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'in_review')
        self.assertEqual(feed.deliveries.get().status, 'approved')
        self.assertEqual(story.deliveries.get().status, 'submitted')
        evaluate(story, 'rejected', 'Corrigir o contraste do Story.')
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'in_progress')
        response = self.client.get(reverse('media_my_work_detail', args=[self.task.pk]))
        self.assertContains(response, 'Corrigir o contraste do Story.')
        self.assertContains(response, 'Aprovada')
        evaluate(screen, 'approved')
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'in_progress')
        self.upload(story, PNG + b'story-corrigido')
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'in_progress')
        self.confirm_corrections()
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'in_review')
        self.assertEqual(feed.deliveries.get().status, 'approved')
        evaluate(story, 'approved')
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'completed')
        self.assertEqual(list(story.deliveries.values_list('status', flat=True)), ['approved', 'rejected'])
        self.assertEqual(self.task.delivery_reviews.count(), 4)

    def test_review_card_displays_previews_without_file_names_or_required_badges(self):
        req = self.requirement(); self.upload(req)
        self.client.force_login(self.leader)
        response = self.client.get(reverse('media_delivery_review', args=[self.task.pk]), {'fragment': '1'})
        self.assertContains(response, 'Prévia da entrega Story')
        self.assertContains(response, 'Aprovar')
        self.assertContains(response, 'Reprovar')
        self.assertNotContains(response, 'story.png')
        self.assertNotContains(response, 'Obrigatório')
        self.assertNotContains(response, 'Versões anteriores')

    def test_existing_pending_step_with_delivery_can_be_reviewed(self):
        req = self.requirement(); self.upload(req)
        MediaTask.objects.filter(pk=self.task.pk).update(status='pending')
        self.client.force_login(self.leader)
        url = reverse('media_delivery_review', args=[self.task.pk])
        response = self.client.get(url, {'fragment': '1'})
        self.assertContains(response, 'Aprovar')
        from website.services.delivery_reviews import review_token
        response = self.client.post(url, {'requirement_id': req.pk, 'decision': 'approved',
            'review_token': review_token(MediaTask.objects.get(pk=self.task.pk))}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'completed')

    def test_review_rejects_other_step_requirement_and_repeated_decision(self):
        req = self.requirement(); self.upload(req)
        other = MediaContent.objects.create(title='Outra demanda', content_type='artwork')
        other_task = MediaTask.objects.create(content=other, title='Outra etapa', assigned_to=self.other)
        other_requirement = MediaDeliveryRequirement.objects.create(task=other_task, format='feed', label='Feed')
        from website.services.delivery_reviews import decide_review, review_token
        task = MediaTask.objects.get(pk=self.task.pk); token = review_token(task)
        with self.assertRaises(ValidationError):
            decide_review(task.pk, self.leader, other_requirement.pk, 'approved', '', token)
        decide_review(task.pk, self.leader, req.pk, 'approved', '', token)
        with self.assertRaises(ValidationError):
            decide_review(task.pk, self.leader, req.pk, 'rejected', '', token)
        self.assertEqual(self.task.delivery_reviews.count(), 1)

    def test_unavailable_file_cannot_be_approved(self):
        req = self.requirement(); self.upload(req)
        delivery = req.deliveries.get()
        ContentFile.objects.filter(pk=delivery.asset_id).update(ready=False)
        from website.services.delivery_reviews import decide_review, review_token
        task = MediaTask.objects.get(pk=self.task.pk)
        with self.assertRaises(ValidationError):
            decide_review(task.pk, self.leader, req.pk, 'approved', '', review_token(task))
        delivery.refresh_from_db(); self.assertEqual(delivery.status, 'submitted')
        self.assertFalse(task.delivery_reviews.exists())

    def test_corrected_file_requires_owner_confirmation_before_review(self):
        req = self.requirement(); self.upload(req)
        self.decide('rejected', 'Ajustar as cores.')
        result = self.upload(req, PNG + b'correcao')
        self.assertTrue(result['needs_confirmation'])
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'in_progress')
        self.assertEqual(req.deliveries.first().status, 'draft')
        response = self.client.get(reverse('media_my_work_detail', args=[self.task.pk]))
        self.assertContains(response, 'Concluir e enviar para revisão')
        from website.services.delivery_reviews import decide_review, review_token
        task = MediaTask.objects.get(pk=self.task.pk)
        with self.assertRaises(ValidationError):
            decide_review(task.pk, self.leader, req.pk, 'approved', '', review_token(task))
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(reverse('media_my_work_status', args=[self.task.pk]), {'status': 'completed'}).status_code, 404)
        self.client.force_login(self.worker)
        self.confirm_corrections()
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'in_review')
        self.assertEqual(req.deliveries.first().status, 'submitted')
        response = self.client.get(reverse('media_my_work_detail', args=[self.task.pk]))
        self.assertContains(response, 'Aguardando revisão da liderança')
        self.assertNotContains(response, 'Concluir e enviar para revisão')

    def test_confirmation_requires_every_rejected_format_to_be_corrected(self):
        feed, story = self.requirement('feed'), self.requirement('story')
        self.upload(feed); self.upload(story, PNG + b'story')
        from website.services.delivery_reviews import decide_review, review_token
        for req in (feed, story):
            task = MediaTask.objects.get(pk=self.task.pk)
            decide_review(task.pk, self.leader, req.pk, 'rejected', '', review_token(task))
        self.upload(feed, PNG + b'correcao')
        response = self.client.post(reverse('media_my_work_status', args=[self.task.pk]), {'status': 'completed'})
        self.assertContains(response, 'Envie uma nova versão para cada entrega', status_code=400)
        self.assertEqual(feed.deliveries.first().status, 'draft')
        self.upload(story, PNG + b'correcao-story')
        self.confirm_corrections()
        self.assertEqual(feed.deliveries.first().status, 'submitted')
        self.assertEqual(story.deliveries.first().status, 'submitted')

    def test_migration_restores_confirmation_for_existing_corrected_version(self):
        import importlib
        from types import SimpleNamespace
        from django.apps import apps
        from django.db import connection
        req = self.requirement(); self.upload(req)
        self.decide('rejected')
        self.upload(req, PNG + b'correcao')
        corrected = req.deliveries.first()
        MediaDelivery.objects.filter(pk=corrected.pk).update(status='submitted')
        MediaTask.objects.filter(pk=self.task.pk).update(status='in_review')
        migration = importlib.import_module('website.migrations.0126_confirm_corrected_deliveries')
        migration.restore_confirmation_for_corrections(apps, SimpleNamespace(connection=connection))
        corrected.refresh_from_db(); self.task.refresh_from_db()
        self.assertEqual(corrected.status, 'draft')
        self.assertEqual(self.task.status, 'in_progress')

    def test_individual_step_editor_updates_formats_and_preserves_history(self):
        story = self.requirement(); self.upload(story)
        delivery = story.deliveries.get()
        self.client.force_login(self.leader)
        response = self.client.post(reverse('media_task_update', args=[self.task.pk]), {
            'title': 'Designer atualizado', 'assigned_to': self.worker.pk, 'status': 'in_review',
            'delivery_formats_present': '1', 'delivery_formats': ['feed', 'other'],
            'delivery_other_format': 'Banner do site',
        })
        self.assertEqual(response.status_code, 302)
        self.task.refresh_from_db(); story.refresh_from_db()
        self.assertEqual(self.task.title, 'Designer atualizado')
        self.assertEqual(self.task.status, 'in_progress')
        self.assertFalse(story.active)
        self.assertTrue(MediaDelivery.objects.filter(pk=delivery.pk).exists())
        self.assertEqual(set(self.task.delivery_requirements.filter(active=True).values_list('label', flat=True)), {'Feed', 'Banner do site'})

    def test_individual_step_editor_rolls_back_when_other_format_is_empty(self):
        self.client.force_login(self.leader)
        response = self.client.post(reverse('media_task_update', args=[self.task.pk]), {
            'title': 'Título que não deve ser salvo', 'assigned_to': self.worker.pk, 'status': 'pending',
            'delivery_formats_present': '1', 'delivery_formats': ['other'], 'delivery_other_format': '',
        })
        self.assertEqual(response.status_code, 302)
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, 'Criar arte')
        self.assertFalse(self.task.delivery_requirements.exists())
        self.assertIn('Informe o nome do formato Outro.', [str(message) for message in response.wsgi_request._messages])

    def test_individual_step_editor_preserves_formats_when_not_posted(self):
        req = self.requirement()
        self.client.force_login(self.leader)
        response = self.client.post(reverse('media_task_update', args=[self.task.pk]), {
            'title': 'Editar título', 'assigned_to': self.worker.pk, 'status': 'pending',
        })
        self.assertEqual(response.status_code, 302)
        req.refresh_from_db(); self.assertTrue(req.active)

    def test_individual_step_editor_formats_are_available_to_leader_only(self):
        req = self.requirement()
        self.client.force_login(self.leader)
        response = self.client.get(reverse('media_demand_detail_page', args=[self.content.pk]))
        self.assertContains(response, 'Formatos das entregas')
        self.assertContains(response, 'name="delivery_formats" value="feed"')
        self.assertContains(response, 'name="delivery_formats" value="other"')
        self.assertContains(response, 'data-delivery-formats="story,')
        self.client.force_login(self.other)
        self.client.post(reverse('media_task_update', args=[self.task.pk]), {
            'title': 'Invadido', 'status': 'pending', 'delivery_formats_present': '1',
        })
        self.task.refresh_from_db(); req.refresh_from_db()
        self.assertEqual(self.task.title, 'Criar arte')
        self.assertTrue(req.active)

    def test_removing_format_during_review_keeps_remaining_deliveries_reviewable(self):
        feed, story = self.requirement('feed'), self.requirement('story')
        self.upload(feed); self.upload(story, PNG + b'story')
        from website.services.delivery_reviews import decide_review, review_token
        task = MediaTask.objects.get(pk=self.task.pk)
        stale_token = review_token(task)
        self.client.force_login(self.leader)
        response = self.client.post(reverse('media_task_update', args=[task.pk]), {
            'title': task.title, 'assigned_to': self.worker.pk, 'status': 'in_review',
            'delivery_formats_present': '1', 'delivery_formats': ['feed'],
        })
        self.assertEqual(response.status_code, 302)
        task.refresh_from_db(); self.assertEqual(task.status, 'in_review')
        story.refresh_from_db(); self.assertFalse(story.active)
        with self.assertRaises(ValidationError):
            decide_review(task.pk, self.leader, feed.pk, 'approved', '', stale_token)
        decide_review(task.pk, self.leader, feed.pk, 'approved', '', review_token(task))
        task.refresh_from_db(); self.assertEqual(task.status, 'completed')
        self.assertEqual(story.deliveries.count(), 1)

    def test_removing_all_formats_during_review_allows_normal_completion(self):
        req = self.requirement(); self.upload(req)
        self.client.force_login(self.leader)
        response = self.client.post(reverse('media_task_update', args=[self.task.pk]), {
            'title': self.task.title, 'assigned_to': self.worker.pk, 'status': 'in_review',
            'delivery_formats_present': '1',
        })
        self.assertEqual(response.status_code, 302)
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'in_progress')
        self.client.force_login(self.worker)
        response = self.client.post(reverse('media_my_work_status', args=[self.task.pk]), {'status': 'completed'})
        self.assertEqual(response.status_code, 302)
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'completed')
        self.assertEqual(req.deliveries.count(), 1)

    def test_removing_delivery_from_review_preserves_file_and_returns_stage_to_work(self):
        req = self.requirement(); self.upload(req)
        delivery = req.deliveries.get(); asset = delivery.asset
        from website.services.delivery_reviews import decide_review, review_token
        token = review_token(MediaTask.objects.get(pk=self.task.pk))
        response = self.post(req, action='withdraw', delivery_id=delivery.pk)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()['force_refresh'])
        self.assertIn('Ainda não enviado', response.json()['html'])
        self.assertIn('Enviar arquivo', response.json()['html'])
        self.assertNotIn('data-delivery-remove', response.json()['html'])
        self.task.refresh_from_db(); delivery.refresh_from_db()
        self.assertEqual(self.task.status, 'in_progress')
        self.assertEqual(delivery.status, 'withdrawn')
        self.assertEqual(delivery.metadata['withdrawn_previous_status'], 'submitted')
        self.assertTrue(asset.storage.exists(asset.file.name))
        self.assertTrue(service.missing_requirements(self.task).exists())
        with self.assertRaises(ValidationError):
            decide_review(self.task.pk, self.leader, req.pk, 'approved', '', token)
        self.assertFalse(self.task.delivery_reviews.exists())
        response = self.client.get(reverse('media_my_work_detail', args=[self.task.pk]))
        self.assertContains(response, 'Ainda não enviado')
        self.assertNotContains(response, 'Prévia de story.png')
        self.assertNotContains(response, 'Entregas enviadas para revisão')

    def test_removed_latest_delivery_does_not_restore_an_older_version(self):
        req = self.requirement(); self.upload(req)
        self.decide('rejected', 'Trocar imagem')
        self.upload(req, PNG + b'correcao')
        corrected = req.deliveries.first()
        response = self.post(req, action='withdraw', delivery_id=corrected.pk)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Ainda não enviado', response.json()['html'])
        self.assertEqual(list(req.deliveries.values_list('status', flat=True)), ['withdrawn', 'rejected'])
        self.task.refresh_from_db()
        self.assertTrue(service.missing_requirements(self.task).exists())
        result = self.upload(req, PNG + b'nova-correcao')
        self.assertTrue(result['needs_confirmation'])
        self.assertEqual(req.deliveries.first().status, 'draft')
        self.assertEqual(list(req.deliveries.values_list('version', flat=True)), [3, 2, 1])
        self.confirm_corrections()
        self.assertEqual(req.deliveries.first().status, 'submitted')

    def test_remove_cannot_touch_other_members_or_newer_version(self):
        feed, story = self.requirement('feed'), self.requirement('story')
        self.upload(feed)
        first = feed.deliveries.first()
        self.client.force_login(self.other)
        self.assertEqual(self.post(feed, action='withdraw', delivery_id=first.pk).status_code, 404)
        self.client.force_login(self.worker)
        self.upload(feed, PNG + b'newer')
        response = self.post(feed, action='withdraw', delivery_id=first.pk)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(feed.deliveries.first().status, 'submitted')
        self.assertEqual(story.deliveries.count(), 0)

    def test_removing_approved_required_delivery_reopens_stage_and_keeps_review(self):
        req = self.requirement(); self.upload(req); self.decide('approved')
        delivery = req.deliveries.get(); review = self.task.delivery_reviews.get()
        response = self.post(req, action='withdraw', delivery_id=delivery.pk)
        self.assertEqual(response.status_code, 200)
        self.task.refresh_from_db(); delivery.refresh_from_db(); review.refresh_from_db()
        self.assertEqual(self.task.status, 'in_progress')
        self.assertEqual(delivery.metadata['withdrawn_previous_status'], 'approved')
        self.assertEqual(review.decision, 'approved')
        self.assertEqual(review.delivery_id, delivery.pk)
        self.assertTrue(delivery.asset.storage.exists(delivery.asset.file.name))
        result = self.upload(req, PNG + b'nova')
        self.assertTrue(result['needs_confirmation'])
        self.assertEqual(req.deliveries.first().status, 'draft')

    def test_removing_optional_file_does_not_reopen_completed_required_work(self):
        required = self.requirement('feed'); optional = self.requirement('story')
        optional.required = False; optional.save()
        self.upload(optional)
        self.upload(required, PNG + b'feed')
        self.decide('approved'); self.decide('approved')
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'completed')
        response = self.post(optional, action='withdraw', delivery_id=optional.deliveries.get().pk)
        self.assertEqual(response.status_code, 200)
        self.task.refresh_from_db(); self.assertEqual(self.task.status, 'completed')

    def test_review_banner_and_remove_action_appear_after_final_upload(self):
        feed, story = self.requirement('feed'), self.requirement('story')
        self.upload(feed)
        response = self.client.get(reverse('media_my_work_detail', args=[self.task.pk]))
        self.assertNotContains(response, 'Entregas enviadas para revisão')
        result = self.upload(story, PNG + b'story')
        self.assertEqual(result['task_status'], 'in_review')
        self.assertIn('data-delivery-remove', result['html'])
        response = self.client.get(reverse('media_my_work_detail', args=[self.task.pk]))
        self.assertContains(response, 'Entregas enviadas para revisão')
        self.assertContains(response, 'A liderança vai avaliar os arquivos')
        self.assertContains(response, 'data-delivery-remove', count=2)

    def test_legacy_video_format_and_current_video_formats_remain_valid(self):
        self.content.content_type = 'video'; self.content.save()
        service.configure_requirements(self.task, ['video', 'reels', 'horizontal', 'other'], 'Chamada do site')
        self.assertEqual(
            set(self.task.delivery_requirements.filter(active=True).values_list('format', flat=True)),
            {'video', 'reels', 'horizontal', 'other'},
        )
        self.assertTrue(self.task.delivery_requirements.filter(format='other', label='Chamada do site').exists())

    def test_existing_video_design_formats_are_preserved_when_editing(self):
        self.content.content_type = 'video'; self.content.save()
        service.configure_requirements(self.task, ['story', 'telao'])
        self.client.force_login(self.leader)
        response = self.client.get(reverse('media_demand_detail_page', args=[self.content.pk]))
        self.assertContains(response, 'data-delivery-formats="story,telao,')
        self.assertContains(response, 'name="delivery_formats" value="video"')
        self.assertContains(response, 'name="delivery_formats" value="reels"')
        self.assertContains(response, 'name="delivery_formats" value="horizontal"')
        self.assertContains(response, 'data-task-format-group="legacy" hidden')

    def test_editor_search_matches_project_and_owner_terms(self):
        req = self.video_requirement()
        self.project.name = 'Chamada da Conferência'; self.project.save()
        ExternalMediaProject.objects.create(name='Chamada diferente', template_version=self.project.template_version,
                                            created_by=self.other.member)
        response = self.client.get(reverse('media_delivery_projects', args=[req.pk]), {'q': 'conferência worker'})
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual([item['id'] for item in result['projects']], [self.project.pk])
        self.assertTrue(result['projects'][0]['is_owner'])
        self.assertEqual(result['projects'][0]['owner'], self.worker.member.name)

    def test_editor_projects_are_paginated_without_losing_later_results(self):
        req = self.video_requirement()
        for number in range(23):
            ExternalMediaProject.objects.create(name=f'Evento teste {number:02}',
                template_version=self.project.template_version, created_by=self.other.member)
        url = reverse('media_delivery_projects', args=[req.pk])
        first = self.client.get(url).json()
        second = self.client.get(url, {'page': 2}).json()
        self.assertEqual(len(first['projects']), 20)
        self.assertTrue(first['has_more'])
        self.assertEqual(first['projects'][0]['id'], self.project.pk)
        self.assertEqual(len(second['projects']), 4)
        self.assertFalse(second['has_more'])
        self.assertFalse({p['id'] for p in first['projects']} & {p['id'] for p in second['projects']})

    def test_editor_project_search_remains_scoped_to_assigned_member(self):
        req = self.video_requirement()
        self.client.force_login(self.other)
        response = self.client.get(reverse('media_delivery_projects', args=[req.pk]), {'q': 'Chamada'})
        self.assertEqual(response.status_code, 404)
