import base64
import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
import azure.cognitiveservices.speech as speechsdk
from googletrans import Translator
from .azure_keyvault import get_speech_key
from asgiref.sync import async_to_sync, sync_to_async
import asyncio
import datetime

logger = logging.getLogger(__name__)

translator = Translator()

def translate_text(text):
    translator = Translator()
    targets = ['en', 'nl']
    results = {}
    for lang in targets:
        try:
            results[lang] = translator.translate(text, src='pt', dest=lang).text
        except Exception as e:
            logger.error(f"Erro na tradução para '{lang}': {e}")
            results[lang] = "⚠️ Error"
    return results

class AudioConsumer(AsyncWebsocketConsumer):
    def _session_started_handler(self, evt):
        logger.info(f"🚀 SESSÃO AZURE INICIADA: {evt}")

    def _session_stopped_handler(self, evt):
        logger.info(f"🛑 SESSÃO AZURE TERMINADA: {evt}")

    def _canceled_handler(self, evt):
        logger.error(f"‼️ RECONHECIMENTO CANCELADO: {evt.reason}")
        if evt.reason == speechsdk.CancellationReason.Error:
            logger.error(f"    CÓDIGO DO ERRO: {evt.error_code}")
            logger.error(f"    DETALHES DO ERRO: {evt.error_details}")

    def _recognizing_handler(self, evt):
        pt_text = evt.result.text
        logger.info(f"👀 Azure (parcial): '{pt_text}'")
        async_to_sync(self.channel_layer.group_send)(
            'transcription_group',
            {
                'type': 'send_transcription',
                'message_pt': pt_text,
                'translations': {},
                'message_type': 'partial'
            }
        )

    def _recognized_handler(self, evt):
        if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech:
            pt_text = evt.result.text
            logger.info(f"✅ Azure (FINAL): '{pt_text}'")
            translations = translate_text(pt_text)
            logger.info("📤 AudioConsumer: Enviando para o grupo 'transcription_group'")
            async_to_sync(self.channel_layer.group_send)(
                'transcription_group',
                {
                    'type': 'send_transcription',
                    'message_pt': pt_text,
                    'translations': translations,
                    'message_type': 'final'
                }
            )
        elif evt.result.reason == speechsdk.ResultReason.NoMatch:
            logger.warning("- SEM CORRESPONDÊNCIA: A fala não pôde ser reconhecida.")

    async def connect(self):
        logger.info("--- AudioConsumer: Conectando WebSocket de Áudio ---")
        await self.accept()

        try:
            self.speech_key = get_speech_key()
            self.service_region = "brazilsouth"

            self.speech_config = speechsdk.SpeechConfig(subscription=self.speech_key, region=self.service_region)
            self.speech_config.speech_recognition_language = "pt-BR"
            
            stream_format = speechsdk.audio.AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1)
            self.audio_stream = speechsdk.audio.PushAudioInputStream(stream_format)
            self.audio_config = speechsdk.audio.AudioConfig(stream=self.audio_stream)

            self.speech_recognizer = speechsdk.SpeechRecognizer(
                speech_config=self.speech_config,
                audio_config=self.audio_config
            )

            self.speech_recognizer.session_started.connect(self._session_started_handler)
            self.speech_recognizer.session_stopped.connect(self._session_stopped_handler)
            self.speech_recognizer.canceled.connect(self._canceled_handler)
            self.speech_recognizer.recognizing.connect(self._recognizing_handler)
            self.speech_recognizer.recognized.connect(self._recognized_handler)

            await sync_to_async(self.speech_recognizer.start_continuous_recognition)()
            logger.info("🎤 AudioConsumer: Reconhecimento contínuo da Azure iniciado.")

            self.audio_buffer = b""
            self.buffer_size = 15000
            self.send_interval = 0.5
            self._sending_task = asyncio.create_task(self._send_buffer_periodically())

        except Exception as e:
            logger.error(f"ERRO CRÍTICO no connect do AudioConsumer: {e}", exc_info=True)
            await self.close()

    async def disconnect(self, close_code):
        logger.info(f"❌ AudioConsumer: WebSocket de Áudio desconectado: {close_code}")
        if hasattr(self, 'speech_recognizer') and self.speech_recognizer:
            await sync_to_async(self.speech_recognizer.stop_continuous_recognition_async)()
            self.speech_recognizer = None
        if hasattr(self, '_sending_task') and not self._sending_task.done():
            self._sending_task.cancel()
            try:
                await self._sending_task
            except asyncio.CancelledError:
                pass

    async def receive(self, text_data=None, bytes_data=None):
        logger.info("🎙️ Pacote de áudio recebido via WebSocket")
        logger.info(f"🕒 Recebido em: {datetime.datetime.utcnow().isoformat()} UTC")

        try:
            data = json.loads(text_data)
            audio_b64 = data.get("audio")

            if audio_b64:
                logger.info(f"📦 Tamanho base64: {len(audio_b64)} bytes")
                header, encoded = audio_b64.split(",", 1)
                audio_bytes = base64.b64decode(encoded)
                logger.info(f"🔊 Tamanho do áudio decodificado: {len(audio_bytes)} bytes")

                if hasattr(self, 'audio_stream'):
                    self.audio_stream.write(audio_bytes)
            else:
                logger.warning("⚠️ Nenhum campo 'audio' no pacote recebido.")
        except Exception as e:
            logger.error(f"❌ Erro ao processar áudio recebido: {e}")

    async def _send_buffer_periodically(self):
        try:
            while True:
                await asyncio.sleep(self.send_interval)
                if self.audio_buffer:
                    logger.debug("Enviando buffer de áudio para Azure.")
                    if hasattr(self, 'audio_stream'):
                        self.audio_stream.write(self.audio_buffer)
                        self.audio_buffer = b""
        except asyncio.CancelledError:
            pass

class TranscriptConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        logger.info("\n--- TranscriptConsumer ---")
        self.room_group_name = 'transcription_group'
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()
        logger.info("👍 TranscriptConsumer: Conectado e aguardando mensagens no grupo.")

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
        logger.info(f"🗑️ TranscriptConsumer: Desconectado. Código: {close_code}")

    async def receive(self, text_data):
        pass

    async def send_transcription(self, event):
        logger.info("🎉 TranscriptConsumer: MENSAGEM RECEBIDA DO GRUPO!")
        message_pt = event.get('message_pt', '')
        
        logger.info(f"↪️ TranscriptConsumer: Enviando para o frontend: '{message_pt}'")
        await self.send(text_data=json.dumps({
            'pt': event.get('message_pt', ''),
            'translations': event.get('translations', {})
        }))
