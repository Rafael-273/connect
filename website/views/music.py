from django.urls import reverse_lazy
from django.views.generic import ListView, DetailView, CreateView
from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth.mixins import LoginRequiredMixin
from website.models import Music
from django.http import JsonResponse
from django.views import View
from django.views.generic import UpdateView
from django.contrib import messages


class MusicListView(LoginRequiredMixin, ListView):
    model = Music
    template_name = 'admin_panel/music/list.html'
    context_object_name = 'musics'
    ordering = ['name']

class MusicDetailView(LoginRequiredMixin, DetailView):
    model = Music
    template_name = 'admin_panel/music/detail.html'
    context_object_name = 'music'

class MusicCreateView(LoginRequiredMixin, CreateView):
    model = Music
    template_name = 'admin_panel/music/form.html'
    fields = ['name', 'singer', 'chord_sheet']
    success_url = reverse_lazy('admin_music_list')

class MusicDeleteView(LoginRequiredMixin, View):

    def post(self, request, pk):
        music = get_object_or_404(Music, pk=pk)
        music_name = music.name
        music.delete()

        return JsonResponse({
            'success': True,
            'message': f'Música "{music_name}" excluída com sucesso!'
        })

class MusicUpdateView(LoginRequiredMixin, UpdateView):
    model = Music
    template_name = 'admin_panel/music/form.html'
    fields = ['name', 'singer', 'chord_sheet']
    success_url = reverse_lazy('admin_music_list')

    def form_valid(self, form):
        messages.success(self.request, 'Música atualizada com sucesso!')
        return super().form_valid(form)

    
    