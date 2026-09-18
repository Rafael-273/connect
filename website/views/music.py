from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView
from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.mixins import LoginRequiredMixin
from website.models import Music, ChordSheet
from django.http import JsonResponse
from django.views import View
from django.views.generic import UpdateView
from django.contrib import messages
from django.db.models import Q
from .mixins import MinistrationContextMixin
from website.forms.music import MusicForm, ChordSheetFormSet, ChordSheetFormSetEdit


class MusicListView(LoginRequiredMixin, ListView):
    model = Music
    template_name = 'admin_panel/music/list.html'
    context_object_name = 'musics'
    ordering = ['name']
    paginate_by = 20

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.GET.get('search', '').strip()
        tempo = self.request.GET.get('tempo', '').strip()

        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(singer__icontains=search)
            )

        if tempo:
            queryset = queryset.filter(tempo=tempo)

        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['search'] = self.request.GET.get('search', '')
        context['tempo_filter'] = self.request.GET.get('tempo', '')
        return context


class MusicCreateView(LoginRequiredMixin, CreateView):
    model = Music
    form_class = MusicForm
    template_name = 'admin_panel/music/form.html'
    success_url = reverse_lazy('admin_music_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['action'] = 'create'
        if 'formset' not in context:
            if self.request.POST:
                context['formset'] = ChordSheetFormSet(self.request.POST, self.request.FILES, instance=self.object, prefix='chordsheets')
            else:
                context['formset'] = ChordSheetFormSet(instance=self.object, prefix='chordsheets')
        return context

    def form_valid(self, form):
        self.object = form.save()
        formset = ChordSheetFormSet(self.request.POST, self.request.FILES, instance=self.object, prefix='chordsheets')

        if formset.is_valid():
            formset.save()
            messages.success(self.request, 'Música cadastrada com sucesso!')
            return redirect(self.success_url)
        else:
            self.object.delete()
            return self.render_to_response(self.get_context_data(form=form, formset=formset))


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
    form_class = MusicForm
    template_name = 'admin_panel/music/form.html'
    success_url = reverse_lazy('admin_music_list')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['action'] = 'edit'
        if 'formset' not in context:
            if self.request.POST:
                context['formset'] = ChordSheetFormSetEdit(self.request.POST, self.request.FILES, instance=self.object, prefix='chordsheets')
            else:
                context['formset'] = ChordSheetFormSetEdit(instance=self.object, prefix='chordsheets')
        return context

    def form_valid(self, form):
        self.object = form.save()
        formset = ChordSheetFormSet(self.request.POST, self.request.FILES, instance=self.object, prefix='chordsheets')

        if formset.is_valid():
            formset.save()
            messages.success(self.request, 'Música atualizada com sucesso!')
            return redirect(self.success_url)
        else:
            return self.render_to_response(self.get_context_data(form=form, formset=formset))
    

class MusicUserListView(LoginRequiredMixin, MinistrationContextMixin, ListView):
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        member = self.request.user.member
        flags = self._get_ministry_flags(member)
        context['can_consolidate'] = flags['can_consolidate']
        context['is_ministration_member'] = flags['is_ministration']
        context['is_media_member'] = flags['is_media']
        return context

    def _get_ministry_flags(self, member):
        from website.models import MinistryMembership
        return {
            'can_consolidate': member.is_available_to_consolidate,
            'is_ministration': self.get_ministration_status(member),
            'is_media': self.get_media_status(member),
        }


    
    