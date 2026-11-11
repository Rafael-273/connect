import json
from unittest.mock import Mock

from django.test import SimpleTestCase

from website.ai import AIServiceError
from website.services.manual_generation import SCHEMA, generate_manual
from website.services.manual_formatting import format_manual
from website.utils.manual_content import sanitize_manual_content


class ManualGenerationTests(SimpleTestCase):
    def test_empty_transcription_is_allowed_only_when_requested(self):
        from website.ai.service import AIService
        client = Mock()
        client.audio.transcriptions.create.return_value.text = '  '
        service = AIService(client=client)
        with self.assertRaises(AIServiceError):
            service.transcribe(b'audio')
        self.assertEqual(service.transcribe(b'audio', allow_empty=True), '')

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

    def test_ai_format_preserves_words_links_images_lists_and_tables(self):
        ai = Mock()
        ai.generate_text.return_value = json.dumps({'blocks': [
            {'id': 0, 'segments': [{'start': '', 'style': 'heading'}]},
            {'id': 1, 'segments': [{'start': '', 'style': 'notice'}]},
        ]})
        source = ('<p>Preparação</p><p>Confira o <a href="https://example.com">cabo</a>.</p>'
                  '<ul><li>Passo existente</li></ul><img src="https://example.com/a.png" alt="Mesa">'
                  '<table><tbody><tr><td>Configuração</td></tr></tbody></table>')
        result = format_manual(source, ai=ai)
        self.assertIn('<h2>Preparação</h2>', result)
        self.assertIn('<blockquote>Confira o <a href="https://example.com">cabo</a>.</blockquote>', result)
        for value in ('Passo existente', 'https://example.com/a.png', 'Configuração'):
            self.assertIn(value, result)

    def test_ai_format_rejects_missing_or_duplicate_blocks(self):
        ai = Mock()
        ai.generate_text.return_value = '{"blocks": [{"id": 0, "segments": [{"start": "", "style": "heading"}]}, {"id": 0, "segments": [{"start": "", "style": "notice"}]}]}'
        with self.assertRaises(AIServiceError):
            format_manual('<p>Um</p><p>Dois</p>', ai=ai)

    def test_ai_format_accepts_plain_editor_text(self):
        ai = Mock()
        ai.generate_text.return_value = '{"blocks": [{"id": 0, "segments": [{"start": "", "style": "heading"}]}]}'
        self.assertIn('<h2>Texto simples</h2>', format_manual('Texto simples', ai=ai))

    def test_ai_format_splits_merged_training_without_changing_text(self):
        ai = Mock()
        text = 'TRANSMISSÃO AO VIVO 01 Preparar o tripé ☐ Pegar o tripé. ☐ Posicionar o tripé. 02 Abrir o OBS ☐ Abrir o programa.'
        def classify(prompt, **kwargs):
            items = json.loads(prompt)['blocks']
            return json.dumps({'blocks': [{'id': item['id'], 'segments': [
                {'start': '', 'style': 'heading' if item['id'] == 0 else 'paragraph'},
            ]} for item in items]})
        ai.generate_text.side_effect = classify
        result = format_manual('<p>' + text + '</p>', ai=ai)
        self.assertIn('<h2>TRANSMISSÃO AO VIVO ', result)
        self.assertIn('<h3>01 Preparar o tripé ', result)
        self.assertIn('<p>☐ Pegar o tripé. ', result)
        self.assertIn('<h3>02 Abrir o OBS ', result)
        from django.utils.html import strip_tags
        self.assertEqual(' '.join(strip_tags(result).split()), ' '.join(text.split()))

    def test_ai_format_repairs_checklist_split_across_existing_blocks(self):
        ai = Mock()
        def classify(prompt, **kwargs):
            return json.dumps({'blocks': [{'id': item['id'], 'segments': [
                {'start': '', 'style': 'paragraph'},
            ]} for item in json.loads(prompt)['blocks']]})
        ai.generate_text.side_effect = classify
        source = ('<h3>02 Verificar os computadores □ Confirmar</h3>'
                  '<p>que o computador está ligado.</p>'
                  '<p>□ Confirmar que a multimídia está ligada.</p>'
                  '<h3>03 Conectar a mesa □ Conectar</h3>'
                  '<p>o cabo USB ao computador.</p>'
                  '<p>□ Conferir o vídeo. Manual de transmissão ao vivoMODELOS PRONTOS Use os padrões.</p>'
                  '<p>Link: <a href="https://example.com/fotos">fotografias</a></p>')
        result = format_manual(source, ai=ai)
        self.assertIn('<h3>02 Verificar os computadores ', result)
        self.assertIn('<p>□ Confirmar que o computador está ligado.', result)
        self.assertIn('<h3>03 Conectar a mesa ', result)
        self.assertIn('<p>□ Conectar o cabo USB ao computador.', result)
        self.assertIn('MODELOS PRONTOS', result)
        self.assertIn('href="https://example.com/fotos"', result)
        from django.utils.html import strip_tags
        import re
        # Formatting may introduce whitespace at paragraph boundaries, but no words disappear.
        def words(value):
            return ' '.join(strip_tags(re.sub(r'</(?:p|h2|h3)>', ' ', value)).split()).replace('vivo MODELOS', 'vivoMODELOS')
        self.assertEqual(words(result), words(source))

    def test_ai_format_rejects_invented_split(self):
        ai = Mock()
        ai.generate_text.return_value = json.dumps({'blocks': [{'id': 0, 'segments': [
            {'start': '', 'style': 'heading'}, {'start': 'Texto inventado', 'style': 'paragraph'},
        ]}]})
        with self.assertRaises(AIServiceError):
            format_manual('<p>Um texto existente.</p>', ai=ai)

    def test_checklist_repairs_survive_invalid_model_response(self):
        ai = Mock()
        ai.generate_text.return_value = '{"blocks": []}'
        source = '<p>01 Preparar □ Confirmar</p><p>que está ligado. 02 Ajustar □ Conferir o áudio.</p>'
        result = format_manual(source, ai=ai)
        self.assertIn('<h3>01 Preparar ', result)
        self.assertIn('<p>□ Confirmar que está ligado. ', result)
        self.assertIn('<h3>02 Ajustar ', result)
        self.assertIn('<p>□ Conferir o áudio.</p>', result)

    def test_bullets_and_checklist_items_stay_as_paragraphs(self):
        ai = Mock()
        def classify_as_headings(prompt, **kwargs):
            return json.dumps({'blocks': [{'id': item['id'], 'segments': [
                {'start': '', 'style': 'heading'},
            ]} for item in json.loads(prompt)['blocks']]})
        ai.generate_text.side_effect = classify_as_headings
        result = format_manual('<p>TRANSMISSÃO • Culto Quarta Viva • Checklist 01 Preparar □ Pegar o tripé. 02 Conferir □ Verificar o áudio.</p>', ai=ai)
        self.assertIn('<p>• Culto Quarta Viva ', result)
        self.assertIn('<p>• Checklist ', result)
        self.assertIn('<h3>01 Preparar ', result)
        self.assertIn('<p>□ Pegar o tripé. ', result)
        self.assertIn('<h3>02 Conferir ', result)
        self.assertIn('<p>□ Verificar o áudio.</p>', result)

    def test_repairs_paragraphs_nested_in_headings_by_old_editor(self):
        ai = Mock()
        def classify(prompt, **kwargs):
            result = []
            for item in json.loads(prompt)['blocks']:
                segments = [{'start': '', 'style': 'paragraph'}]
                if item['text'].startswith('TRANSMISSÃO Manual prático'):
                    segments = [{'start': '', 'style': 'heading'}, {'start': 'Manual prático', 'style': 'paragraph'}]
                result.append({'id': item['id'], 'segments': segments})
            return json.dumps({'blocks': result})
        ai.generate_text.side_effect = classify
        source = ('<h2>TRANSMISSÃO</h2><h2><p>Manual prático</p></h2>'
                  '<h3>01 Preparar</h3><h2><p>□ Pegar o tripé.</p></h2>'
                  '<h3>02 Conferir</h3><h2><p>□ Verificar o áudio.</p></h2>')
        result = format_manual(source, ai=ai)
        self.assertNotIn('<h2><p>', result)
        self.assertIn('<p>Manual prático ', result)
        self.assertIn('<p>□ Pegar o tripé. ', result)
        self.assertIn('<p>□ Verificar o áudio.</p>', result)

from unittest.mock import patch
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from website.models import ManualGenerationJob, Member, Ministry, MinistryManual, MinistryMembership, User
from website.tasks import generate_manual_job


class ManualGenerationFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ministry, _ = Ministry.objects.get_or_create(code='midia_externa', defaults={'name': 'Mídia'})
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

    @patch('website.services.manual_jobs.enqueue_manual')
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
        self.assertIn('Formato não aceito', response.json()['error'])
        self.assertFalse(ManualGenerationJob.objects.exists())

    @override_settings(MANUAL_AUDIO_MAX_UPLOAD_MB=128)
    @patch('website.services.manual_jobs.enqueue_manual')
    def test_upload_larger_than_direct_transcription_limit_is_accepted(self, delay):
        upload = SimpleUploadedFile('treino.mp3', b'0' * (25 * 1024 * 1024), content_type='audio/mpeg')
        response = self.client.post(self.url('ministry_manual_generate'), {'audio': upload})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ManualGenerationJob.objects.count(), 1)
        delay.assert_called_once()

    @override_settings(MANUAL_AUDIO_MAX_UPLOAD_MB=1)
    def test_oversized_upload_reports_actual_limit(self):
        upload = SimpleUploadedFile('treino.mp3', b'0' * (1024 * 1024 + 1), content_type='audio/mpeg')
        response = self.client.post(self.url('ministry_manual_generate'), {'audio': upload})
        self.assertEqual(response.status_code, 400)
        self.assertIn('limite de 1 MB', response.json()['error'])
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

    @patch('website.tasks.DIRECT_TRANSCRIPTION_LIMIT', 4)
    @patch('website.external_media.services.AudioExtractor')
    @patch('website.tasks.generate_manual')
    @patch('website.tasks.get_ai_service')
    @patch('website.tasks.validate_audio')
    def test_large_audio_uses_existing_chunker_and_transcribes_each_part(self, validate, get_ai, generate, extractor):
        from pathlib import Path
        from types import SimpleNamespace
        job = ManualGenerationJob.objects.create(ministry=self.ministry, created_by=self.user, filename='treino.mp3', audio=b'audio longo')
        extractor.return_value.extract.return_value = [SimpleNamespace(path=Path('/tmp/part-1.mp3')), SimpleNamespace(path=Path('/tmp/part-2.mp3'))]
        get_ai.return_value.transcribe.side_effect = ['Parte um.', 'Parte dois.']
        generate.return_value = {'title': 'Guia', 'content': 'Instruções', 'prompt_version': '1'}
        generate_manual_job(job.pk, str(job.run_token))
        job.refresh_from_db()
        self.assertEqual(job.status, 'completed')
        self.assertEqual(extractor.return_value.extract.call_args.kwargs['chunk_seconds'], 1200)
        self.assertEqual(get_ai.return_value.transcribe.call_count, 2)
        generate.assert_called_once_with('Parte um.\n\nParte dois.')
        self.assertIsNone(job.audio)

    @patch('website.tasks.DIRECT_TRANSCRIPTION_LIMIT', 4)
    @patch('website.external_media.services.AudioExtractor')
    @patch('website.tasks.generate_manual')
    @patch('website.tasks.get_ai_service')
    @patch('website.tasks.validate_audio')
    def test_silent_chunk_is_skipped_and_retry_resumes_transcription(self, validate, get_ai, generate, extractor):
        from pathlib import Path
        from types import SimpleNamespace
        job = ManualGenerationJob.objects.create(ministry=self.ministry, created_by=self.user, filename='treino.mp3', audio=b'audio longo')
        extractor.return_value.extract.return_value = [SimpleNamespace(path=Path(f'/tmp/part-{i}.mp3')) for i in range(3)]
        get_ai.return_value.transcribe.side_effect = ['Primeira parte.', AIServiceError('falha temporária')]
        generate_manual_job(job.pk, str(job.run_token))
        job.refresh_from_db()
        self.assertEqual(job.status, 'error')
        self.assertIn('Primeira parte.', job.transcript)
        job.status = 'waiting'
        job.save(update_fields=['status'])
        get_ai.return_value.transcribe.reset_mock()
        get_ai.return_value.transcribe.side_effect = ['Segunda parte.', '']
        generate.return_value = {'title': 'Guia', 'content': 'Instruções'}
        generate_manual_job(job.pk, str(job.run_token))
        job.refresh_from_db()
        self.assertEqual(job.status, 'completed')
        self.assertEqual(get_ai.return_value.transcribe.call_count, 2)
        self.assertTrue(get_ai.return_value.transcribe.call_args.kwargs['allow_empty'])
        generate.assert_called_once_with('Primeira parte.\n\nSegunda parte.')
        self.assertEqual(job.transcript, '')

    @patch('website.tasks.DIRECT_TRANSCRIPTION_LIMIT', 4)
    @patch('website.external_media.services.AudioExtractor')
    @patch('website.tasks.get_ai_service')
    @patch('website.tasks.validate_audio')
    def test_all_silent_chunks_report_no_speech(self, validate, get_ai, extractor):
        from pathlib import Path
        from types import SimpleNamespace
        job = ManualGenerationJob.objects.create(ministry=self.ministry, created_by=self.user, filename='treino.mp3', audio=b'audio longo')
        extractor.return_value.extract.return_value = [SimpleNamespace(path=Path('/tmp/part.mp3'))]
        get_ai.return_value.transcribe.return_value = ''
        generate_manual_job(job.pk, str(job.run_token))
        job.refresh_from_db()
        self.assertEqual(job.status, 'error')
        self.assertIn('Não foi identificada fala', job.error)

    @patch('website.tasks.validate_audio', side_effect=ValueError('Áudio inválido ou com duração superior a 2 horas.'))
    def test_failure_keeps_audio_for_retry(self, validate):
        job = ManualGenerationJob.objects.create(ministry=self.ministry, created_by=self.user, filename='a.mp3', audio=b'data')
        generate_manual_job(job.pk, str(job.run_token))
        job.refresh_from_db()
        self.assertEqual(job.status, 'error')
        self.assertEqual(bytes(job.audio), b'data')
        self.assertIn('duração', job.error)

    @patch('website.services.manual_jobs.enqueue_manual')
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

    def test_selected_manual_detail_and_no_floating_navigation(self):
        manual = MinistryManual.objects.create(ministry=self.ministry, title='Guia selecionado', content='Conteúdo selecionado')
        response = self.client.get(self.url('ministry_manual_list'), {'selected': manual.pk})
        self.assertContains(response, f'data-selected="{manual.pk}"')
        self.assertContains(response, f'data-view-manual="{manual.pk}"')
        self.assertNotContains(response, 'manuals-layout has-selection')
        self.assertNotContains(response, 'id="bottomNav"')
        filtered = self.client.get(self.url('ministry_manual_list'), {'selected': manual.pk, 'q': 'inexistente'})
        self.assertNotContains(filtered, f'data-view-manual="{manual.pk}"')

    def test_view_popup_content_is_sanitized(self):
        manual = MinistryManual.objects.create(ministry=self.ministry, title='Guia', content='<p>Texto</p><script>alert(1)</script>')
        response = self.client.get(self.url('ministry_manual_detail', pk=manual.pk), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.assertIn('Texto', response.json()['html'])
        self.assertNotIn('<script', response.json()['html'])

    def test_delete_requires_post_and_soft_deletes_manual(self):
        manual = MinistryManual.objects.create(ministry=self.ministry, title='Guia', content='Texto')
        url = self.url('ministry_manual_delete', pk=manual.pk)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertTrue(MinistryManual.objects.filter(pk=manual.pk).exists())
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertFalse(MinistryManual.objects.filter(pk=manual.pk).exists())
        self.assertIsNotNone(MinistryManual.all_objects.get(pk=manual.pk).deleted)
        self.assertEqual(self.client.get(self.url('ministry_manual_detail', pk=manual.pk)).status_code, 404)

    def test_edit_preserves_existing_summary_without_showing_field(self):
        manual = MinistryManual.objects.create(ministry=self.ministry, title='Guia antigo', summary='Resumo existente', content='Texto')
        response = self.client.get(self.url('ministry_manual_list'))
        self.assertNotContains(response, 'name="summary"')
        response = self.client.post(self.url('ministry_manual_edit', pk=manual.pk), {
            'title': 'Guia atualizado', 'content': 'Novo conteúdo', 'is_active': 'on', 'publication': 'draft',
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        manual.refresh_from_db()
        self.assertEqual(manual.summary, 'Resumo existente')

    def test_archived_manual_can_be_republished_without_active_field(self):
        manual = MinistryManual.objects.create(ministry=self.ministry, title='Arquivado', content='Texto', is_active=False)
        page = self.client.get(self.url('ministry_manual_list'))
        self.assertNotContains(page, 'name="is_active"')
        self.assertNotContains(page, 'Como usar as ferramentas')
        url = self.url('ministry_manual_edit', pk=manual.pk)
        data = {'title': manual.title, 'content': manual.content, 'publication': 'draft'}
        self.assertEqual(self.client.post(url, data, HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code, 200)
        manual.refresh_from_db()
        self.assertFalse(manual.is_active)
        data['publication'] = 'publish'
        self.assertEqual(self.client.post(url, data, HTTP_X_REQUESTED_WITH='XMLHttpRequest').status_code, 200)
        manual.refresh_from_db()
        self.assertTrue(manual.is_active)
        self.assertFalse(manual.is_draft)

    @patch('website.services.manual_formatting.get_ai_service')
    def test_ai_format_endpoint_validates_access_and_returns_safe_html(self, get_ai):
        get_ai.return_value.generate_text.return_value = '{"blocks": [{"id": 0, "segments": [{"start": "", "style": "heading"}]}]}'
        response = self.client.post(self.url('ministry_manual_format'), {'content': '<p>Treinamento</p>'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('<h2>Treinamento</h2>', response.json()['html'])
        self.client.logout()
        self.assertEqual(self.client.post(self.url('ministry_manual_format'), {'content': 'Teste'}).status_code, 302)

    def test_ai_format_rejects_empty_content(self):
        response = self.client.post(self.url('ministry_manual_format'), {'content': ''})
        self.assertEqual(response.status_code, 400)
