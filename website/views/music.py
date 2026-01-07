from django.views.generic import ListView
from django.shortcuts import redirect
from django.contrib.auth.mixins import LoginRequiredMixin

from website.models import Music


class MusicListView(LoginRequiredMixin, ListView):
    model = Music
    template_name = 'admin_panel/music/list.html'
    context_object_name = 'musics'
    ordering = ['name']

    def post(self, request, *args, **kwargs):
        name = request.POST.get('name')
        singer = request.POST.get('singer')
        chord_sheet = request.FILES.get('chord_sheet')

        if name and singer:
            Music.objects.create(
                name=name,
                singer=singer,
                chord_sheet=chord_sheet
            )

        return redirect('admin_music_list')
