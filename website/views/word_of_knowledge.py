from itertools import chain

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.views import View
from django.views.generic import ListView
from datetime import datetime, timedelta

from website.models import WordOfKnowledge, Healing, Member
from website.forms.word_of_knowledge import WordOfKnowledgeForm, HealingForm
from .mixins import MemberRequiredMixin, MinistrationContextMixin


class WordOfKnowledgeListView(MemberRequiredMixin, MinistrationContextMixin, ListView):
    """Lista palavras de conhecimento e curas do membro logado"""
    template_name = 'words/my_words.html'
    context_object_name = 'words'

    def get_queryset(self):
        return WordOfKnowledge.objects.filter(
            member=self.member
        ).select_related('member', 'approved_by')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        all_words = context['words']
        healings = Healing.objects.filter(
            member=self.member
        ).select_related('member', 'word_of_knowledge')

        for word in all_words:
            word.item_type = 'word'
        for healing in healings:
            healing.item_type = 'healing'

        context['combined_items'] = sorted(
            chain(all_words, healings),
            key=lambda x: x.recorded_at if hasattr(x, 'recorded_at') else x.healing_date,
            reverse=True
        )

        context['member'] = self.member
        context['healings'] = healings
        context['total_words'] = all_words.count()
        context['total_healings'] = healings.count()
        context['words_with_healing'] = all_words.filter(resulted_in_healing=True).count()
        context['is_approver'] = self.member.is_approver
        context['can_consolidate'] = self.member.is_available_to_consolidate
        context['is_ministration_member'] = self.get_ministration_status(self.member)
        return context


class WordOfKnowledgeCreateView(MemberRequiredMixin, View):
    """Criar nova palavra de conhecimento"""
    template_name = 'words/create_word.html'

    def get(self, request):
        form = WordOfKnowledgeForm()
        context = self._build_context(form)
        return render(request, self.template_name, context)

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
        context = self._build_context(form)
        return render(request, self.template_name, context)

    def _build_context(self, form):
        now = datetime.now()
        weekday = now.weekday()

        if weekday in [6, 0, 1, 2]:
            next_service = 'Quarta-feira'
            days_until = (2 - weekday) % 7
            if days_until == 0 and now.hour >= 20:
                days_until = 7
        else:
            next_service = 'Domingo'
            days_until = (6 - weekday) % 7
            if days_until == 0 and now.hour >= 19:
                days_until = 7

        next_service_date = (now + timedelta(days=days_until)).date()

        return {
            'member': self.member,
            'form': form,
            'next_service': next_service,
            'next_service_date': next_service_date,
        }


class HealingCreateView(MemberRequiredMixin, View):
    """Criar registro de cura"""
    template_name = 'words/create_healing.html'

    def get(self, request):
        form = HealingForm()
        preselected_word = self._get_preselected_word(request)
        context = {
            'member': self.member,
            'form': form,
            'preselected_word': preselected_word,
        }
        return render(request, self.template_name, context)

    def post(self, request):
        form = HealingForm(request.POST)
        if form.is_valid():
            healing = form.save(commit=False)
            healing.member = self.member
            healing.healing_date = datetime.now().date()

            word_id_post = request.POST.get('word_of_knowledge')
            if word_id_post:
                try:
                    healing.word_of_knowledge = WordOfKnowledge.objects.get(
                        id=word_id_post, member=self.member, is_approved=True
                    )
                except WordOfKnowledge.DoesNotExist:
                    pass

            healing.save()
            messages.success(request, 'Cura registrada com sucesso!')
            return redirect('word_of_knowledge_list')

        preselected_word = self._get_preselected_word(request)
        context = {
            'member': self.member,
            'form': form,
            'preselected_word': preselected_word,
        }
        return render(request, self.template_name, context)

    def _get_preselected_word(self, request):
        word_id = request.GET.get('word_id')
        if word_id:
            try:
                return WordOfKnowledge.objects.get(
                    id=word_id, member=self.member, is_approved=True
                )
            except WordOfKnowledge.DoesNotExist:
                pass
        return None


class ServiceWordsView(MemberRequiredMixin, ListView):
    """Visualizar palavras de conhecimento para o próximo culto"""
    template_name = 'words/service_words.html'
    context_object_name = 'words'

    def get_queryset(self):
        today = datetime.now().date()
        weekday = today.weekday()

        service_type = self.request.GET.get('service_type')
        if service_type:
            if service_type == 'sunday':
                if weekday >= 3:
                    start_date = today - timedelta(days=(weekday - 3))
                else:
                    start_date = today - timedelta(days=(weekday + 4))
            else:
                if weekday <= 2:
                    start_date = today - timedelta(days=weekday)
                else:
                    start_date = today - timedelta(days=(weekday - 0))
        else:
            if weekday in [3, 4, 5, 6]:
                service_type = 'sunday'
                start_date = today - timedelta(days=(weekday - 3))
            else:
                service_type = 'wednesday'
                start_date = today - timedelta(days=weekday)

        self._service_type = service_type
        self._start_date = start_date
        self._today = today

        return WordOfKnowledge.objects.filter(
            service_type=service_type,
            recorded_at__date__gte=start_date,
            recorded_at__date__lte=today,
            is_approved=True
        ).select_related('member').order_by('recorded_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = self._today
        weekday = today.weekday()

        days_until_wednesday = (2 - weekday) % 7
        if days_until_wednesday == 0 and datetime.now().hour >= 20:
            days_until_wednesday = 7
        next_wednesday = today + timedelta(days=days_until_wednesday if days_until_wednesday > 0 else 7)

        days_until_sunday = (6 - weekday) % 7
        if days_until_sunday == 0 and datetime.now().hour >= 19:
            days_until_sunday = 7
        next_sunday = today + timedelta(days=days_until_sunday if days_until_sunday > 0 else 7)

        context['member'] = self.member
        context['service_type'] = self._service_type
        context['service_type_display'] = 'Domingo' if self._service_type == 'sunday' else 'Quarta-feira'
        context['start_date'] = self._start_date
        context['today'] = today
        context['next_wednesday'] = next_wednesday
        context['next_sunday'] = next_sunday
        return context


class MarkWordAsHealedView(MemberRequiredMixin, View):
    """Marcar palavra de conhecimento como resultou em cura"""

    def post(self, request, word_id):
        word = get_object_or_404(WordOfKnowledge, id=word_id, member=self.member)

        word.resulted_in_healing = not word.resulted_in_healing
        word.save()

        status = 'marcada como resultou em cura' if word.resulted_in_healing else 'desmarcada'
        messages.success(request, f'Palavra {status}!')

        return redirect('word_of_knowledge_list')
