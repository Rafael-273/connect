from django.views import View
from django.shortcuts import render


class AudioRecorderView(View):
    template_name = 'admin/create/audio_recorder.html'

    def get(self, request):
        return render(request, self.template_name)