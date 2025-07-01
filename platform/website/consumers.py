import base64
import json
from channels.generic.websocket import AsyncWebsocketConsumer
import azure.cognitiveservices.speech as speechsdk
from googletrans import Translator
from .azure_keyvault import get_speech_key
from asgiref.sync import async_to_sync, sync_to_async
import asyncio

# --- Classe do Consumer de Áudio ---
class AudioConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        print("\n--- AudioConsumer ---")
        print("🔌 WebSocket de ÁUDIO conectado.")
        await self.accept()

        self.speech_key = get_speech_key()
        self.service_region = "brazilsouth"

        speech_config = speechsdk.SpeechConfig(subscription=self.speech_key, region=self.service_region)
        self.audio_stream = speechsdk.audio.PushAudioInputStream()
        audio_config = speechsdk.audio.AudioConfig(stream=self.audio_stream)
        auto_detect_source_language_config = speechsdk.AutoDetectSourceLanguageConfig(
            languages=["pt-BR"]
        )
        self.speech_recognizer = speechsdk.SpeechRecognizer(
            speech_config=speech_config,
            audio_config=audio_config,
            auto_detect_source_language_config=auto_detect_source_language_config
        )

        # Handler para resultados parciais (enquanto você fala)
        def recognizing_handler(evt):
            pt_text = evt.result.text
            print(f"👀 Azure (parcial): '{pt_text}'")
            async_to_sync(self.channel_layer.group_send)(
                'transcription_group',
                {
                    'type': 'send_transcription',
                    'message_pt': pt_text,
                    'translations': {},
                }
            )

        # Handler para resultados finais (quando você para de falar)
        def recognized_handler(evt):
            pt_text = evt.result.text
            # PONTO CRÍTICO 1: Azure retornou a transcrição final?
            print(f"✅ Azure (FINAL): '{pt_text}'")
            
            translations = translate_text(pt_text)
            
            # PONTO CRÍTICO 2: A mensagem está sendo enviada para o grupo?
            print(f"📤 AudioConsumer: Enviando para o grupo 'transcription_group'")
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
        print("🎤 AudioConsumer: Reconhecimento da Azure iniciado.")

    async def disconnect(self, close_code):
        print(f"❌ AudioConsumer: WebSocket de ÁUDIO desconectado: {close_code}")
        if self.speech_recognizer:
            await sync_to_async(self.speech_recognizer.stop_continuous_recognition)()

    async def receive(self, text_data=None, bytes_data=None):
        print("➡️ AudioConsumer: Pacote de áudio recebido do frontend.")
        data = json.loads(text_data)
        audio_b64 = data.get("audio")
        if audio_b64:
            header, encoded = audio_b64.split(",", 1)
            audio_bytes = base64.b64decode(encoded)
            self.audio_stream.write(audio_bytes)


# --- Classe do Consumer da Tela de Leitura ---
class TranscriptConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        print("\n--- TranscriptConsumer ---")
        self.room_group_name = 'transcription_group'
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()
        print("👍 TranscriptConsumer: Conectado e aguardando mensagens no grupo.")

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
        print("🗑️ TranscriptConsumer: Desconectado e removido do grupo.")

    async def receive(self, text_data):
        # Este consumer não recebe dados, apenas envia
        pass

    async def send_transcription(self, event):
        # PONTO CRÍTICO 3: A mensagem chegou neste consumer?
        print("🎉 TranscriptConsumer: MENSAGEM RECEBIDA DO GRUPO!")
        message_pt = event.get('message_pt', '')
        
        # PONTO CRÍTICO 4: A mensagem está sendo enviada para o frontend de leitura?
        print(f"↪️ TranscriptConsumer: Enviando para o frontend: '{message_pt}'")
        await self.send(text_data=json.dumps({
            'pt': message_pt,
            'translations': event.get('translations', {})
        }))