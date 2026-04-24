from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView
from django.shortcuts import get_object_or_404, redirect
from django.contrib.auth.mixins import LoginRequiredMixin
from website.models import Music
from django.http import JsonResponse
from django.views import View
from django.views.generic import UpdateView
from django.contrib import messages
from django.db.models import Q


class MusicListView(LoginRequiredMixin, ListView):
    model = Music
    template_name = 'admin_panel/music/list.html'
    context_object_name = 'musics'
    ordering = ['name']

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.GET.get('search', '').strip()

        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(singer__icontains=search)
            )

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('search', '')
        return context


class MusicCreateView(LoginRequiredMixin, CreateView):
    model = Music
    template_name = 'admin_panel/music/form.html'
    fields = ['name', 'singer', 'chord_sheet']
    success_url = reverse_lazy('admin_music_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['action'] = 'create'
        return context


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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['action'] = 'edit'
        return context

    def form_valid(self, form):
        messages.success(self.request, 'Música atualizada com sucesso!')
        return super().form_valid(form)
    

class MusicUserListView(LoginRequiredMixin, ListView):
    model = Music
    template_name = 'list/music_list.html'
    context_object_name = 'musics'
    ordering = ['name']
    paginate_by = 20

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.GET.get('search')
        tempo = self.request.GET.get('tempo')

        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(singer__icontains=search)
            )

        if tempo:
            queryset = queryset.filter(tempo=tempo)

        return queryset


    
    