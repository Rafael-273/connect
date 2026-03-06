from django.shortcuts import redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.utils import timezone
from django.views import View
from django.views.generic import ListView

from website.models import WordOfKnowledge
from .mixins import ApproverRequiredMixin


class PendingWordsListView(ApproverRequiredMixin, ListView):
    """Lista palavras de conhecimento aguardando aprovação"""
    model = WordOfKnowledge
    template_name = 'admin_panel/ministration/pending_words.html'
    context_object_name = 'pending_words'

    def get_queryset(self):
        return WordOfKnowledge.objects.filter(
            is_approved=False
        ).select_related('member').order_by('service_date', 'recorded_at')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['title'] = 'Aprovação de Palavras de Conhecimento'
        context['approved_words'] = WordOfKnowledge.objects.filter(
            is_approved=True
        ).select_related('member', 'approved_by').order_by('-approved_at')[:20]
        context['pending_count'] = self.get_queryset().count()
        return context


class ApproveWordView(ApproverRequiredMixin, View):
    """Aprova uma palavra de conhecimento"""

    def post(self, request, word_id):
        word = get_object_or_404(WordOfKnowledge, id=word_id)

        word.is_approved = True
        word.approved_by = self.member
        word.approved_at = timezone.now()
        word.save()

        messages.success(request, f'Palavra de {word.member.name} aprovada com sucesso!')

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'message': 'Palavra aprovada com sucesso!',
                'approved_by': word.approved_by.name,
                'approved_at': word.approved_at.strftime('%d/%m/%Y %H:%M')
            })

        return redirect('pending_words_list')


class RejectWordView(ApproverRequiredMixin, View):
    """Rejeita/deleta uma palavra de conhecimento"""

    def post(self, request, word_id):
        word = get_object_or_404(WordOfKnowledge, id=word_id)
        member_name = word.member.name
        word.delete()

        messages.warning(request, f'Palavra de {member_name} foi rejeitada e removida.')

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'success': True,
                'message': 'Palavra rejeitada e removida'
            })

        return redirect('pending_words_list')
