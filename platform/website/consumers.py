import base64
import json
from channels.generic.websocket import AsyncWebsocketConsumer
import azure.cognitiveservices.speech as speechsdk
from googletrans import Translator
from asgiref.sync import async_to_sync, sync_to_async
import asyncio

translator = Translator()

def translate_text(text):
    translator = Translator()
    targets = ['en', 'nl']
    results = {}
    for lang in targets:
        try:
            results[lang] = translator.translate(text, src='pt', dest=lang).text
        except Exception as e:
            print(f"Translation to {lang} failed:", e)
            results[lang] = "⚠️ Error"
    return results

class AudioConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        print("WebSocket conectado")
        await self.accept()

        self.speech_key = "4b2W4Yk4J0eVg1niTlo7jxDcp2oTypGTbzA2qV41G5mfnfkJXGsXJQQJ99BFACZoyfiXJ3w3AAAYACOGnoS2"
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

        def recognizing_handler(evt):
            pt_text = evt.result.text
            async_to_sync(self.channel_layer.group_send)(
                'transcription_group',
                {
                    'type': 'send_transcription',
                    'message_pt': pt_text,
                    'translations': {},
                    'message_type': 'partial'
                }
            )

        def recognized_handler(evt):
            pt_text = evt.result.text
            translations = translate_text(pt_text)

            async_to_sync(self.channel_layer.group_send)(
                'transcription_group',
                {
                    'type': 'send_transcription',
                    'message_pt': pt_text,
                    'translations': translations,
                    'message_type': 'final'
                }
            )

        self.speech_recognizer.recognizing.connect(recognizing_handler)
        self.speech_recognizer.recognized.connect(recognized_handler)
        self.speech_recognizer.start_continuous_recognition()

        self.audio_buffer = b""
        self.buffer_size = 15000
        self.send_interval = 0.5

        # Cria tarefa para enviar buffer periodicamente
        self._sending_task = asyncio.create_task(self._send_buffer_periodically())

    async def disconnect(self, close_code):
        print("WebSocket desconectado:", close_code)
        if self.speech_recognizer:
            await sync_to_async(self.speech_recognizer.stop_continuous_recognition)()
            self.speech_recognizer = None
        self._sending_task.cancel()
        try:
            await self._sending_task
        except asyncio.CancelledError:
            pass

    async def receive(self, text_data=None, bytes_data=None):
        data = json.loads(text_data)
        audio_b64 = data.get("audio")

        if audio_b64:
            header, encoded = audio_b64.split(",", 1)
            audio_bytes = base64.b64decode(encoded)

            self.audio_buffer += audio_bytes

            if len(self.audio_buffer) >= self.buffer_size:
                self.audio_stream.write(self.audio_buffer)
                self.audio_buffer = b""

    async def _send_buffer_periodically(self):
        try:
            while True:
                await asyncio.sleep(self.send_interval)
                if self.audio_buffer:
                    self.audio_stream.write(self.audio_buffer)
                    self.audio_buffer = b""
        except asyncio.CancelledError:
            pass

    async def send_json(self, content):
        await self.send(text_data=json.dumps(content))


class TranscriptConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.room_group_name = 'transcription_group'
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()
        print("👀 Tela de leitura conectada")

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )

    async def receive(self, text_data):
        pass

    async def send_transcription(self, event):
        await self.send(text_data=json.dumps({
            'pt': event.get('message_pt', ''),
            'translations': event.get('translations', {})
        }))
