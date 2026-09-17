from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock

from django.test import SimpleTestCase, override_settings

from website.ai import AIConfigurationError, AIService, AIServiceError


class AIServiceTests(SimpleTestCase):
    def setUp(self):
        self.client = Mock()
        self.service = AIService(
            client=self.client,
            text_model="text-model",
            transcription_model="stt-model",
        )

    def test_generate_text_uses_responses_api(self):
        self.client.responses.create.return_value = SimpleNamespace(
            output_text="  resposta gerada  "
        )

        result = self.service.generate_text(
            "Minha pergunta",
            instructions="Responda em português.",
            max_output_tokens=300,
            safety_identifier="user-hash",
        )

        self.assertEqual(result, "resposta gerada")
        self.client.responses.create.assert_called_once_with(
            model="text-model",
            input="Minha pergunta",
            instructions="Responda em português.",
            max_output_tokens=300,
            safety_identifier="user-hash",
        )

    def test_generate_text_rejects_empty_prompt(self):
        with self.assertRaises(ValueError):
            self.service.generate_text("  ")

    def test_generate_text_requires_explicit_model_when_not_configured(self):
        service = AIService(client=self.client, transcription_model="stt-model")

        with self.assertRaisesMessage(
            AIConfigurationError,
            "Informe o modelo de texto na chamada ou ao criar o AIService.",
        ):
            service.generate_text("Olá")

    def test_transcribe_accepts_bytes(self):
        self.client.audio.transcriptions.create.return_value = SimpleNamespace(
            text="  texto transcrito  "
        )

        result = self.service.transcribe(
            b"audio-data",
            filename="culto.webm",
            language="pt",
            prompt="Culto em português do Brasil.",
        )

        self.assertEqual(result, "texto transcrito")
        request = self.client.audio.transcriptions.create.call_args.kwargs
        self.assertEqual(request["model"], "stt-model")
        self.assertEqual(request["language"], "pt")
        self.assertEqual(request["prompt"], "Culto em português do Brasil.")
        self.assertIsInstance(request["file"], BytesIO)
        self.assertEqual(request["file"].name, "culto.webm")

    def test_transcribe_segments_requests_whisper_timestamps(self):
        self.client.audio.transcriptions.create.return_value = SimpleNamespace(
            segments=[SimpleNamespace(start=1.2, end=4.8, text=' Olá igreja! ')],
        )

        result = self.service.transcribe_segments(b'audio', filename='culto.mp3')

        self.assertEqual(result[0].start_ms, 1200)
        self.assertEqual(result[0].end_ms, 4800)
        request = self.client.audio.transcriptions.create.call_args.kwargs
        self.assertEqual(request['model'], 'whisper-1')
        self.assertEqual(request['response_format'], 'verbose_json')
        self.assertEqual(request['timestamp_granularities'], ['word', 'segment'])

    def test_transcribe_segments_can_omit_language_for_auto_detection(self):
        self.client.audio.transcriptions.create.return_value = SimpleNamespace(
            segments=[SimpleNamespace(start=0.0, end=2.0, text=' Hello igreja ')],
        )

        self.service.transcribe_segments(b'audio', filename='culto.mp3', language=None)

        request = self.client.audio.transcriptions.create.call_args.kwargs
        self.assertNotIn('language', request)

    def test_transcribe_segments_falls_back_to_whisper_when_model_rejects_verbose_json(self):
        self.client.audio.transcriptions.create.side_effect = [
            RuntimeError(
                "response_format 'verbose_json' is not compatible with model "
                "'gpt-4o-mini-transcribe-api-ev3'. Use 'json' or 'text' instead."
            ),
            SimpleNamespace(
                segments=[SimpleNamespace(start=0.0, end=1.5, text=' Teste ')],
            ),
        ]
        service = AIService(
            client=self.client,
            text_model="text-model",
            transcription_model="gpt-4o-mini-transcribe",
        )

        result = service.transcribe_segments(
            b'audio', filename='culto.mp3', model='gpt-4o-mini-transcribe',
        )

        self.assertEqual(result[0].text, 'Teste')
        self.assertEqual(self.client.audio.transcriptions.create.call_count, 2)
        first_request = self.client.audio.transcriptions.create.call_args_list[0].kwargs
        second_request = self.client.audio.transcriptions.create.call_args_list[1].kwargs
        self.assertEqual(first_request['model'], 'gpt-4o-mini-transcribe')
        self.assertEqual(second_request['model'], 'whisper-1')

    def test_transcribe_segments_prefers_whisper_for_timestamps_without_retry(self):
        self.client.audio.transcriptions.create.return_value = SimpleNamespace(
            segments=[SimpleNamespace(start=0.0, end=1.5, text=' Teste ')],
        )
        service = AIService(
            client=self.client,
            text_model="text-model",
            transcription_model="gpt-4o-mini-transcribe",
        )

        result = service.transcribe_segments(b'audio', filename='culto.mp3')

        self.assertEqual(result[0].text, 'Teste')
        self.assertEqual(self.client.audio.transcriptions.create.call_count, 1)
        request = self.client.audio.transcriptions.create.call_args.kwargs
        self.assertEqual(request['model'], 'whisper-1')

    def test_provider_errors_are_wrapped(self):
        self.client.responses.create.side_effect = RuntimeError("provider detail")

        with self.assertRaisesMessage(
            AIServiceError,
            "Não foi possível gerar a resposta de IA.",
        ):
            self.service.generate_text("Olá")

    @override_settings(OPENAI_API_KEY="")
    def test_missing_key_fails_only_when_client_is_used(self):
        service = AIService()

        with self.assertRaisesMessage(
            AIConfigurationError,
            "OPENAI_API_KEY não está configurada.",
        ):
            service.generate_text("Olá", model="gpt-5.6-luna")
