from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.utils import timezone
from datetime import datetime

from website.models import WordOfKnowledge, Member


@login_required
def pending_words_list(request):
    """Lista palavras de conhecimento aguardando aprovação"""
    # Verificar se o usuário tem um membro associado e é aprovador
    try:
        member = request.user.member
        if not member.is_approver and not request.user.is_staff:
            messages.error(request, 'Você não tem permissão para aprovar palavras.')
            return redirect('admin_dashboard')
    except:
        messages.error(request, 'Você não tem permissão para acessar esta página.')
        return redirect('admin_dashboard')
    
    # Buscar palavras não aprovadas, ordenadas por data do culto
    pending_words = WordOfKnowledge.objects.filter(
        is_approved=False
    ).select_related('member').order_by('service_date', 'recorded_at')
    
    # Buscar palavras já aprovadas (últimas 20)
    approved_words = WordOfKnowledge.objects.filter(
        is_approved=True
    ).select_related('member', 'approved_by').order_by('-approved_at')[:20]
    
    context = {
        'title': 'Aprovação de Palavras de Conhecimento',
        'pending_words': pending_words,
        'approved_words': approved_words,
        'pending_count': pending_words.count(),
    }
    
    return render(request, 'admin_panel/ministration/pending_words.html', context)


@login_required
def approve_word(request, word_id):
    """Aprova uma palavra de conhecimento"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)
    
    # Verificar se o usuário tem permissão
    try:
        member = request.user.member
        if not member.is_approver and not request.user.is_staff:
            return JsonResponse({'success': False, 'error': 'Sem permissão'}, status=403)
    except:
        return JsonResponse({'success': False, 'error': 'Sem permissão'}, status=403)
    
    word = get_object_or_404(WordOfKnowledge, id=word_id)
    
    # Aprovar a palavra
    word.is_approved = True
    word.approved_by = member
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


@login_required
def reject_word(request, word_id):
    """Rejeita/deleta uma palavra de conhecimento"""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)
    
    # Verificar se o usuário tem permissão
    try:
        member = request.user.member
        if not member.is_approver and not request.user.is_staff:
            return JsonResponse({'success': False, 'error': 'Sem permissão'}, status=403)
    except:
        return JsonResponse({'success': False, 'error': 'Sem permissão'}, status=403)
    
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
