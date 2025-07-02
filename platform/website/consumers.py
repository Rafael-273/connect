import base64
import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
import azure.cognitiveservices.speech as speechsdk
from googletrans import Translator
from .azure_keyvault import get_speech_key
from asgiref.sync import async_to_sync, sync_to_async
import asyncio

# Configura o logger para este módulo
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

# --- CLASSE DO CONSUMER DE ÁUDIO ---
class AudioConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        logger.info("--- AudioConsumer: Conectando WebSocket de Áudio ---")
        await self.accept()

        try:
            self.speech_key = get_speech_key()
            self.service_region = "brazilsouth"

            speech_config = speechsdk.SpeechConfig(subscription=self.speech_key, region=self.service_region)
            
            # Forçando o idioma para simplificar e evitar erros de detecção
            speech_config.speech_recognition_language = "pt-BR"

            # Habilita o log detalhado do SDK da Azure para um arquivo
            log_path = "/tmp/azure_speech.log"
            speech_config.set_property(speechsdk.PropertyId.Speech_LogFilename, log_path)
            logger.info(f"SDK da Azure configurado para salvar logs em: {log_path}")

            stream_format = speechsdk.audio.AudioStreamFormat(samples_per_second=16000, bits_per_sample=16, channels=1)
            self.audio_stream = speechsdk.audio.PushAudioInputStream(stream_format)
            audio_config = speechsdk.audio.AudioConfig(stream=self.audio_stream)

            # Criando o reconhecedor sem a detecção automática de idioma
            self.speech_recognizer = speechsdk.SpeechRecognizer(
                speech_config=speech_config,
                audio_config=audio_config
            )

            # --- Handlers para todos os eventos do SDK ---

            def session_started_handler(evt):
                logger.info(f"🚀 SESSÃO AZURE INICIADA: {evt}")

            def session_stopped_handler(evt):
                logger.info(f"🛑 SESSÃO AZURE TERMINADA: {evt}")

            def canceled_handler(evt):
                logger.error(f"‼️ RECONHECIMENTO CANCELADO: {evt.reason}")
                if evt.reason == speechsdk.CancellationReason.Error:
                    logger.error(f"    CÓDIGO DO ERRO: {evt.error_code}")
                    logger.error(f"    DETALHES DO ERRO: {evt.error_details}")

            def recognizing_handler(evt):
                pt_text = evt.result.text
                logger.info(f"👀 Azure (parcial): '{pt_text}'")
                async_to_sync(self.channel_layer.group_send)('transcription_group', {'type': 'send_transcription', 'message_pt': pt_text, 'translations': {}, 'message_type': 'partial'})

            def recognized_handler(evt):
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

            # Conectando todos os handlers
            self.speech_recognizer.session_started.connect(session_started_handler)
            self.speech_recognizer.session_stopped.connect(session_stopped_handler)
            self.speech_recognizer.canceled.connect(canceled_handler)
            self.speech_recognizer.recognizing.connect(recognizing_handler)
            self.speech_recognizer.recognized.connect(recognized_handler)
            
            # Inicia o reconhecimento
            self.speech_recognizer.start_continuous_recognition()
            logger.info("🎤 AudioConsumer: Reconhecimento contínuo da Azure iniciado.")

        except Exception as e:
            logger.error(f"ERRO CRÍTICO no connect do AudioConsumer: {e}", exc_info=True)
            await self.close()

    async def receive(self, text_data=None, bytes_data=None):
        logger.debug("➡️ AudioConsumer: Pacote de áudio recebido.")
        data = json.loads(text_data)
        audio_b64 = data.get("audio")

        if audio_b64:
            try:
                header, encoded = audio_b64.split(",", 1)
                audio_bytes = base64.b64decode(encoded)
                if hasattr(self, 'audio_stream'):
                    self.audio_stream.write(audio_bytes)
            except Exception as e:
                logger.error(f"Erro ao processar áudio recebido: {e}")

    async def disconnect(self, close_code):
        logger.info(f"❌ AudioConsumer: WebSocket de Áudio desconectado: {close_code}")
        if hasattr(self, 'speech_recognizer') and self.speech_recognizer:
            await sync_to_async(self.speech_recognizer.stop_continuous_recognition_async)()
            self.speech_recognizer = None

# --- CLASSE DO CONSUMER DA TELA DE LEITURA ---
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
        logger.info("🎉 TranscriptConsumer: MENSAGEM RECEBIDA DO GRUPO!")
        message_pt = event.get('message_pt', '')
        
        logger.info(f"↪️ TranscriptConsumer: Enviando para o frontend: '{message_pt}'")
        await self.send(text_data=json.dumps({
            'pt': message_pt,
            'translations': event.get('translations', {})
        }))