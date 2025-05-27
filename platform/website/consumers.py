import base64
import json
from channels.generic.websocket import AsyncWebsocketConsumer
import azure.cognitiveservices.speech as speechsdk
from asgiref.sync import async_to_sync, sync_to_async


class AudioConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        print("WebSocket conectado")
        await self.accept()

        self.speech_key = "9wPTLq5cidkGaXT1zqww8q5iU6TgZWnXW2zOL51oHZmkG1dmtbSEJQQJ99BEACZoyfiXJ3w3AAAYACOG4i99"
        self.service_region = "brazilsouth"

        speech_config = speechsdk.SpeechConfig(subscription=self.speech_key, region=self.service_region)
        speech_config.speech_recognition_language = "pt-BR"
        self.audio_stream = speechsdk.audio.PushAudioInputStream()
        audio_config = speechsdk.audio.AudioConfig(stream=self.audio_stream)
        self.speech_recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)

        def recognized_handler(evt):
            print('Reconhecendo parcialmente:', evt.result.text)
            async_to_sync(self.send_json)({
                "message": evt.result.text
            })

        self.speech_recognizer.recognized.connect(recognized_handler)
        self.speech_recognizer.start_continuous_recognition()

        self.audio_buffer = b""

    async def disconnect(self, close_code):
        print("WebSocket desconectado:", close_code)
        if self.speech_recognizer:
            await sync_to_async(self.speech_recognizer.stop_continuous_recognition)()
            self.speech_recognizer = None

    async def receive(self, text_data=None, bytes_data=None):
        data = json.loads(text_data)
        audio_b64 = data.get("audio")

        if audio_b64:
            header, encoded = audio_b64.split(",", 1)
            audio_bytes = base64.b64decode(encoded)

            self.audio_buffer += audio_bytes

            if len(self.audio_buffer) > 32000:
                self.audio_stream.write(self.audio_buffer)
                self.audio_buffer = b""

    async def send_json(self, content):
        await self.send(text_data=json.dumps(content))
