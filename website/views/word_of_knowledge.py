from itertools import chain

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.views import View
from django.views.generic import ListView
from datetime import datetime, timedelta

from website.models import WordOfKnowledge, Healing
from website.forms.word_of_knowledge import WordOfKnowledgeForm, HealingForm
from .mixins import MemberRequiredMixin, MinistrationContextMixin


class WordOfKnowledgeListView(MemberRequiredMixin, MinistrationContextMixin, View):
    template_name = 'words/my_words.html'

    def get(self, request):
        return render(request, self.template_name, self._build_context())

    def _build_context(self):
        words = WordOfKnowledge.objects.filter(
            member=self.member
        ).select_related('member', 'approved_by')
        healings = Healing.objects.filter(
            member=self.member
        ).select_related('member', 'word_of_knowledge')
        return {
            'words': words,
            'healings': healings,
        }

class WordOfKnowledgeCreateView(MemberRequiredMixin, View):
    """Criar nova palavra de conhecimento"""
    template_name = 'words/create_word.html'

    def get(self, request):
        return render(request, self.template_name, self._build_context(WordOfKnowledgeForm()))

    def post(self, request):
        form = WordOfKnowledgeForm(request.POST)
        if form.is_valid():
            word = form.save(commit=False)
            word.member = self.member
            word.save()
            messages.success(
                request,
                f'Palavra registrada para o culto de {word.get_service_type_display()} '
                f'({word.service_date.strftime("%d/%m/%Y")})!'
            )
            return redirect('word_of_knowledge_list')
        return render(request, self.template_name, self._build_context(form))

    def _build_context(self, form):
        next_service, next_service_date = self._get_next_service()
        return {
            'member': self.member,
            'form': form,
            'next_service': next_service,
            'next_service_date': next_service_date,
        }

    @staticmethod
    def _get_next_service():
        now = datetime.now()
        weekday = now.weekday()
        if weekday in [6, 0, 1, 2]:
            label = 'Quarta-feira'
            days_until = (2 - weekday) % 7 or 7
        else:
            label = 'Domingo'
            days_until = (6 - weekday) % 7 or 7
        return label, (now + timedelta(days=days_until)).date()


class HealingCreateView(MemberRequiredMixin, View):
    """Criar registro de cura"""
    template_name = 'words/create_healing.html'

    def get(self, request):
        context = {
            'member': self.member,
            'form': HealingForm(),
            'preselected_word': self._get_word(request.GET.get('word_id'), self.member),
        }
        return render(request, self.template_name, context)

    def post(self, request):
        form = HealingForm(request.POST)
        if form.is_valid():
            healing = form.save(commit=False)
            healing.member = self.member
            healing.healing_date = datetime.now().date()
            healing.word_of_knowledge = self._get_word(
                request.POST.get('word_of_knowledge'), self.member
            )
            healing.save()
            messages.success(request, 'Cura registrada com sucesso!')
            return redirect('word_of_knowledge_list')
        context = {
            'member': self.member,
            'form': form,
            'preselected_word': self._get_word(request.GET.get('word_id'), self.member),
        }
        return render(request, self.template_name, context)

    @staticmethod
    def _get_word(word_id, member):
        if not word_id:
            return None
        try:
            return WordOfKnowledge.objects.get(id=word_id, member=member, is_approved=True)
        except WordOfKnowledge.DoesNotExist:
            return None


class ServiceWordsView(MemberRequiredMixin, ListView):
    """Visualizar palavras de conhecimento para o próximo culto"""
    template_name = 'words/service_words.html'
    context_object_name = 'words'

    def get_queryset(self):
        self._today = datetime.now().date()
        self._service_type, self._start_date = self._resolve_service(
            self.request.GET.get('service_type'), self._today
        )
        return WordOfKnowledge.objects.filter(
            service_type=self._service_type,
            recorded_at__date__gte=self._start_date,
            recorded_at__date__lte=self._today,
            is_approved=True,
        ).select_related('member').order_by('recorded_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        next_wednesday, next_sunday = self._get_next_services(self._today)
        context.update({
            'member': self.member,
            'service_type': self._service_type,
            'service_type_display': 'Domingo' if self._service_type == 'sunday' else 'Quarta-feira',
            'start_date': self._start_date,
            'today': self._today,
            'next_wednesday': next_wednesday,
            'next_sunday': next_sunday,
        })
        return context

    @staticmethod
    def _resolve_service(service_type_param, today):
        weekday = today.weekday()
        if service_type_param:
            service_type = service_type_param
            if service_type == 'sunday':
                start_date = today - timedelta(days=(weekday - 3) if weekday >= 3 else (weekday + 4))
            else:
                start_date = today - timedelta(days=weekday if weekday <= 2 else weekday)
        else:
            if weekday in [3, 4, 5, 6]:
                service_type = 'sunday'
                start_date = today - timedelta(days=(weekday - 3))
            else:
                service_type = 'wednesday'
                start_date = today - timedelta(days=weekday)
        return service_type, start_date

    @staticmethod
    def _get_next_services(today):
        weekday = today.weekday()
        days_wed = (2 - weekday) % 7 or 7
        days_sun = (6 - weekday) % 7 or 7
        return today + timedelta(days=days_wed), today + timedelta(days=days_sun)


class MarkWordAsHealedView(MemberRequiredMixin, View):
    """Marcar palavra de conhecimento como resultou em cura"""

    def post(self, request, word_id):
        word = get_object_or_404(WordOfKnowledge, id=word_id, member=self.member)

        word.resulted_in_healing = not word.resulted_in_healing
        word.save()

        status = 'marcada como resultou em cura' if word.resulted_in_healing else 'desmarcada'
        messages.success(request, f'Palavra {status}!')

        return redirect('word_of_knowledge_list')
