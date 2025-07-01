import base64
import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
import azure.cognitiveservices.speech as speechsdk
from googletrans import Translator
from .azure_keyvault import get_speech_key
from asgiref.sync import async_to_sync, sync_to_async
import asyncio

# Configuração do logger para este módulo
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
    async def connect(self):
        logger.info("--- AudioConsumer: Conectando WebSocket de Áudio ---")
        await self.accept()

        try:
            self.speech_key = get_speech_key()
            self.service_region = "brazilsouth"

            speech_config = speechsdk.SpeechConfig(subscription=self.speech_key, region=self.service_region)
            
            # --- HABILITANDO LOGS DETALHADOS DO SDK DA AZURE ---
            # O SDK criará este arquivo no seu servidor para depuração.
            log_path = "/tmp/azure_speech.log"
            speech_config.set_property(speechsdk.PropertyId.Speech_LogFilename, log_path)
            logger.info(f"SDK da Azure configurado para salvar logs em: {log_path}")

            stream_format = speechsdk.audio.AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1)
            self.audio_stream = speechsdk.audio.PushAudioInputStream(stream_format)
            
            audio_config = speechsdk.audio.AudioConfig(stream=self.audio_stream)

            auto_detect_source_language_config = speechsdk.AutoDetectSourceLanguageConfig(
                languages=["pt-BR"]
            )

            self.speech_recognizer = speechsdk.SpeechRecognizer(
                speech_config=speech_config,
                audio_config=audio_config,
                auto_detect_source_language_config=auto_detect_source_language_config
            )

            # --- Handlers com Logging ---
            def recognizing_handler(evt):
                pt_text = evt.result.text
                logger.debug(f"Azure (parcial): '{pt_text}'")
                async_to_sync(self.channel_layer.group_send)(
                    'transcription_group',
                    {'type': 'send_transcription', 'message_pt': pt_text}
                )

            def recognized_handler(evt):
                logger.info(f"✅ PASSO 1/4: Azure retornou transcrição final: '{evt.result.text}'")
                pt_text = evt.result.text
                translations = translate_text(pt_text)
                
                logger.info("📤 PASSO 2/4: AudioConsumer enviando para o grupo 'transcription_group'")
                async_to_sync(self.channel_layer.group_send)(
                    'transcription_group',
                    {
                        'type': 'send_transcription',
                        'message_pt': pt_text,
                        'translations': translations,
                    }
                )

            self.speech_recognizer.recognizing.connect(recognizing_handler)
            self.speech_recognizer.recognized.connect(recognized_handler)
            self.speech_recognizer.start_continuous_recognition()

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
            await sync_to_async(self.speech_recognizer.stop_continuous_recognition)()
            self.speech_recognizer = None
        if hasattr(self, '_sending_task'):
            self._sending_task.cancel()
            try:
                await self._sending_task
            except asyncio.CancelledError:
                pass

    async def receive(self, text_data=None, bytes_data=None):
        logger.debug("➡️ AudioConsumer: Pacote de áudio recebido.")
        data = json.loads(text_data)
        audio_b64 = data.get("audio")

        if audio_b64:
            try:
                header, encoded = audio_b64.split(",", 1)
                audio_bytes = base64.b64decode(encoded)
                self.audio_buffer += audio_bytes
                if len(self.audio_buffer) >= self.buffer_size:
                    self.audio_stream.write(self.audio_buffer)
                    self.audio_buffer = b""
            except Exception as e:
                logger.error(f"Erro ao processar áudio recebido: {e}")

    async def _send_buffer_periodically(self):
        try:
            while True:
                await asyncio.sleep(self.send_interval)
                if self.audio_buffer:
                    logger.debug("Enviando buffer de áudio para Azure.")
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
        logger.info("🗑️ TranscriptConsumer: Desconectado.")

    async def receive(self, text_data):
        pass

    async def send_transcription(self, event):
        logger.info("🎉 PASSO 3/4: TranscriptConsumer recebeu mensagem do grupo!")
        message_pt = event.get('message_pt', '')
        
        logger.info(f"↪️ PASSO 4/4: Enviando para o frontend de leitura: '{message_pt}'")
        await self.send(text_data=json.dumps({
            'pt': message_pt,
            'translations': event.get('translations', {})
        }))