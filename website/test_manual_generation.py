import json
from unittest.mock import Mock

from django.test import SimpleTestCase

from website.ai import AIServiceError
from website.services.manual_generation import SCHEMA, generate_manual
from website.utils.manual_content import sanitize_manual_content


class ManualGenerationTests(SimpleTestCase):
    def test_generates_validated_editor_content(self):
        ai = Mock()
        ai.generate_text.return_value = json.dumps({
            'title': 'Áudio', 'introduction': 'Introdução',
            'sections': [{'title': 'Preparação', 'paragraphs': ['Confira a mesa.'],
                          'steps': ['Ligue a mesa', 'Ajuste o volume']}],
            'conclusion': 'Revise o resultado.',
        })
        result = generate_manual('Um treinamento', ai=ai)
        self.assertEqual(result['title'], 'Áudio')
        self.assertIn('## Preparação', result['content'])
        self.assertIn('1. Ligue a mesa', result['content'])
        self.assertEqual(ai.generate_text.call_args.kwargs['response_schema'], SCHEMA)

    def test_rejects_malformed_output(self):
        ai = Mock()
        ai.generate_text.return_value = '{"title": "Manual"}'
        with self.assertRaises(AIServiceError):
            generate_manual('Treinamento', ai=ai)

    def test_model_markup_is_not_rendered_as_html(self):
        ai = Mock()
        ai.generate_text.return_value = json.dumps({
            'title': 'Teste', 'introduction': '<script>alert(1)</script>',
            'sections': [{'title': 'Teste', 'paragraphs': ['[link](javascript:alert(1))'], 'steps': []}],
            'conclusion': '',
        })
        rendered = sanitize_manual_content(generate_manual('Treinamento', ai=ai)['content'])
        self.assertNotIn('<script>', rendered)
        self.assertNotIn('<a ', rendered)

from unittest.mock import patch
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from website.models import ManualGenerationJob, Member, Ministry, MinistryManual, MinistryMembership, User
from website.tasks import generate_manual_job


class ManualGenerationFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ministry = Ministry.objects.create(name='Mídia', code='midia_externa')
        cls.user = User.objects.create_user('manual-leader@example.test', password='test')
        member = Member.objects.create(name='Líder', user=cls.user)
        MinistryMembership.objects.create(ministry=cls.ministry, member=member, role='leader')
        cls.other = User.objects.create_user('other-leader@example.test', password='test')
        member2 = Member.objects.create(name='Outro líder', user=cls.other)
        MinistryMembership.objects.create(ministry=cls.ministry, member=member2, role='leader')

    def setUp(self):
        self.client.force_login(self.user)

    def url(self, name, **kwargs):
        return reverse(name, kwargs={'ministry_id': self.ministry.pk, **kwargs})

    @patch('website.tasks.generate_manual_job.delay')
    def test_upload_and_job_privacy(self, delay):
        response = self.client.post(self.url('ministry_manual_generate'), {
            'audio': SimpleUploadedFile('treino.mp3', b'audio', content_type='audio/mpeg'),
        })
        self.assertEqual(response.status_code, 200)
        job = ManualGenerationJob.objects.get()
        delay.assert_called_once_with(job.pk, str(job.run_token))
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url('ministry_manual_generation_status', pk=job.pk)).status_code, 404)

    def test_invalid_upload_rejected(self):
        response = self.client.post(self.url('ministry_manual_generate'), {
            'audio': SimpleUploadedFile('treino.exe', b'data'),
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ManualGenerationJob.objects.exists())

    @patch('website.tasks.generate_manual')
    @patch('website.tasks.get_ai_service')
    @patch('website.tasks.validate_audio')
    def test_worker_saves_draft_and_removes_sensitive_data(self, validate, get_ai, generate):
        job = ManualGenerationJob.objects.create(ministry=self.ministry, created_by=self.user, filename='a.mp3', audio=b'data')
        get_ai.return_value.transcribe.return_value = 'Transcrição privada'
        generate.return_value = {'title': 'Guia', 'content': 'Instruções', 'prompt_version': '1'}
        generate_manual_job(job.pk, str(job.run_token))
        job.refresh_from_db()
        self.assertEqual(job.status, 'completed')
        self.assertIsNone(job.audio)
        self.assertEqual(job.transcript, '')
        self.assertTrue(job.manual.is_draft)
        self.assertEqual(job.manual.origin, 'ai')
        generate_manual_job(job.pk, str(job.run_token))
        self.assertEqual(MinistryManual.objects.count(), 1)

    @patch('website.tasks.validate_audio', side_effect=ValueError('Áudio inválido ou com duração superior a 2 horas.'))
    def test_failure_keeps_audio_for_retry(self, validate):
        job = ManualGenerationJob.objects.create(ministry=self.ministry, created_by=self.user, filename='a.mp3', audio=b'data')
        generate_manual_job(job.pk, str(job.run_token))
        job.refresh_from_db()
        self.assertEqual(job.status, 'error')
        self.assertEqual(bytes(job.audio), b'data')
        self.assertIn('duração', job.error)

    @patch('website.tasks.generate_manual_job.delay')
    def test_retry_preserves_upload_and_rotates_token(self, delay):
        job = ManualGenerationJob.objects.create(ministry=self.ministry, created_by=self.user, filename='a.mp3', audio=b'data', status='error')
        old_token = job.run_token
        response = self.client.post(self.url('ministry_manual_generation_status', pk=job.pk))
        self.assertEqual(response.status_code, 200)
        job.refresh_from_db()
        self.assertNotEqual(job.run_token, old_token)
        self.assertEqual(bytes(job.audio), b'data')
        delay.assert_called_once_with(job.pk, str(job.run_token))

    def test_manual_draft_and_publish(self):
        url = self.url('ministry_manual_create')
        data = {'title': 'Guia', 'content': '<p>Conteúdo</p>', 'is_active': 'on', 'publication': 'draft'}
        response = self.client.post(url, data, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        manual = MinistryManual.objects.get(title='Guia')
        self.assertTrue(manual.is_draft)
        data['publication'] = 'publish'
        response = self.client.post(self.url('ministry_manual_edit', pk=manual.pk), data, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        manual.refresh_from_db()
        self.assertFalse(manual.is_draft)

    def test_list_contains_modal_and_existing_manual(self):
        MinistryManual.objects.create(ministry=self.ministry, title='Manual antigo', content='Texto')
        response = self.client.get(self.url('ministry_manual_list'))
        self.assertContains(response, 'Manual antigo')
        self.assertContains(response, 'manual-dialog')
