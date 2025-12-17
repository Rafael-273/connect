from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from datetime import datetime, timedelta
from website.models import WordOfKnowledge, Healing, Member
from website.forms.word_of_knowledge import WordOfKnowledgeForm, HealingForm


@login_required
def word_of_knowledge_list(request):
    """Lista palavras de conhecimento e curas do membro logado"""
    try:
        member = request.user.member
    except Member.DoesNotExist:
        messages.error(request, 'Você precisa ter um perfil de membro para acessar esta página.')
        return redirect('member_dashboard')
    
    # Buscar todas as palavras do membro (sem filtro de aprovação ainda)
    all_words = WordOfKnowledge.objects.filter(member=member).select_related('member', 'approved_by')
    
    # Buscar curas do membro
    healings = Healing.objects.filter(member=member).select_related('member', 'word_of_knowledge')
    
    # Adicionar atributo de tipo aos objetos para facilitar identificação no template
    for word in all_words:
        word.item_type = 'word'
    for healing in healings:
        healing.item_type = 'healing'
    
    # Combinar palavras e curas em uma única lista, ordenada por data
    from itertools import chain
    combined_items = sorted(
        chain(all_words, healings),
        key=lambda x: x.recorded_at if hasattr(x, 'recorded_at') else x.healing_date,
        reverse=True
    )
    
    # Estatísticas
    total_words = all_words.count()
    total_healings = healings.count()
    words_with_healing = all_words.filter(resulted_in_healing=True).count()
    
    # Para compatibilidade, manter a variável words
    words = all_words
    
    # Verifica se o membro está no ministério de ministração (sistema antigo e novo)
    is_ministration_old = member.ministry.filter(name__icontains='ministração').exists()
    from ..models.ministry_membership import MinistryMembership
    is_ministration_new = MinistryMembership.objects.filter(
        member=member,
        ministry__name__icontains='ministração',
        is_active=True
    ).exists()
    is_ministration_member = is_ministration_old or is_ministration_new
    
    context = {
        'member': member,
        'words': words,  # Mantém para compatibilidade
        'healings': healings,
        'combined_items': combined_items,  # Nova lista combinada
        'total_words': total_words,
        'total_healings': total_healings,
        'words_with_healing': words_with_healing,
        'is_approver': member.is_approver,
        'can_consolidate': member.is_available_to_consolidate,
        'is_ministration_member': is_ministration_member,
    }
    
    return render(request, 'words/my_words.html', context)


@login_required
def word_of_knowledge_create(request):
    """Criar nova palavra de conhecimento"""
    try:
        member = request.user.member
    except Member.DoesNotExist:
        messages.error(request, 'Você precisa ter um perfil de membro para acessar esta página.')
        return redirect('member_dashboard')
    
    if request.method == 'POST':
        form = WordOfKnowledgeForm(request.POST)
        if form.is_valid():
            word = form.save(commit=False)
            word.member = member
            word.save()  # O tipo de culto e data serão calculados automaticamente no save()
            messages.success(request, f'Palavra registrada para o culto de {word.get_service_type_display()} ({word.service_date.strftime("%d/%m/%Y")})!')
            return redirect('word_of_knowledge_list')
    else:
        form = WordOfKnowledgeForm()
    
    # Calcular qual será o próximo culto para mostrar ao usuário
    from datetime import datetime, timedelta
    now = datetime.now()
    weekday = now.weekday()
    
    if weekday in [6, 0, 1, 2]:  # Domingo até Quarta
        next_service = 'Quarta-feira'
        days_until = (2 - weekday) % 7
        if days_until == 0 and now.hour >= 20:
            days_until = 7
    else:  # Quinta até Sábado
        next_service = 'Domingo'
        days_until = (6 - weekday) % 7
        if days_until == 0 and now.hour >= 19:
            days_until = 7
    
    next_service_date = (now + timedelta(days=days_until)).date()
    
    context = {
        'member': member,
        'form': form,
        'next_service': next_service,
        'next_service_date': next_service_date,
    }
    
    return render(request, 'words/create_word.html', context)


@login_required
def healing_create(request):
    """Criar registro de cura"""
    try:
        member = request.user.member
    except Member.DoesNotExist:
        messages.error(request, 'Você precisa ter um perfil de membro para acessar esta página.')
        return redirect('member_dashboard')
    
    # Verificar se há uma palavra vinculada via URL
    word_id = request.GET.get('word_id')
    preselected_word = None
    if word_id:
        try:
            preselected_word = WordOfKnowledge.objects.get(id=word_id, member=member, is_approved=True)
        except WordOfKnowledge.DoesNotExist:
            pass
    
    if request.method == 'POST':
        form = HealingForm(request.POST)
        if form.is_valid():
            healing = form.save(commit=False)
            healing.member = member
            healing.healing_date = datetime.now().date()
            
            # Se veio word_of_knowledge no POST (campo oculto), vincular
            word_id_post = request.POST.get('word_of_knowledge')
            if word_id_post:
                try:
                    healing.word_of_knowledge = WordOfKnowledge.objects.get(id=word_id_post, member=member, is_approved=True)
                except WordOfKnowledge.DoesNotExist:
                    pass
            
            healing.save()
            
            messages.success(request, 'Cura registrada com sucesso!')
            return redirect('word_of_knowledge_list')
    else:
        form = HealingForm()
    
    context = {
        'member': member,
        'form': form,
        'preselected_word': preselected_word,
    }
    
    return render(request, 'words/create_healing.html', context)


@login_required
def service_words_view(request):
    """
    Visualizar palavras de conhecimento para o próximo culto
    - Culto de Quarta: mostra palavras de domingo até quarta
    - Culto de Domingo: mostra palavras de quinta até domingo
    """
    try:
        member = request.user.member
    except Member.DoesNotExist:
        messages.error(request, 'Você precisa ter um perfil de membro para acessar esta página.')
        return redirect('member_dashboard')
    
    # Determinar tipo de culto baseado no dia da semana atual
    today = datetime.now().date()
    weekday = today.weekday()  # 0 = Monday, 6 = Sunday
    
    # Filtros de data
    if request.GET.get('service_type'):
        service_type = request.GET.get('service_type')
        # Quando filtrado manualmente, calcular data de início
        if service_type == 'sunday':
            # Quinta (3) até Domingo (6) - voltar para quinta
            if weekday >= 3:  # Qui, Sex, Sab, Dom
                start_date = today - timedelta(days=(weekday - 3))
            else:  # Seg, Ter, Qua - voltar para quinta da semana passada
                start_date = today - timedelta(days=(weekday + 4))
        else:  # wednesday
            # Segunda (0) até Quarta (2) - voltar para segunda
            if weekday <= 2:  # Seg, Ter, Qua
                start_date = today - timedelta(days=weekday)
            else:  # Qui, Sex, Sab, Dom - voltar para segunda desta semana
                start_date = today - timedelta(days=(weekday - 0))
    else:
        # Quarta = dia 2, Domingo = dia 6
        if weekday in [3, 4, 5, 6]:  # Qui, Sex, Sab, Dom -> próximo culto é Domingo
            service_type = 'sunday'
            # Quinta até domingo - voltar para quinta
            start_date = today - timedelta(days=(weekday - 3))
        else:  # Seg, Ter, Qua -> próximo culto é Quarta
            service_type = 'wednesday'
            # Segunda até quarta - voltar para segunda
            start_date = today - timedelta(days=weekday)
    
    # Buscar palavras do intervalo - PELO RECORDED_AT, não service_date
    # Filtrar apenas palavras aprovadas
    words = WordOfKnowledge.objects.filter(
        service_type=service_type,
        recorded_at__date__gte=start_date,
        recorded_at__date__lte=today,
        is_approved=True  # Apenas palavras aprovadas
    ).select_related('member').order_by('recorded_at')
    
    # Calcular próximas datas de culto
    # Próxima quarta (dia 2)
    days_until_wednesday = (2 - weekday) % 7
    if days_until_wednesday == 0 and datetime.now().hour >= 20:
        days_until_wednesday = 7
    next_wednesday = today + timedelta(days=days_until_wednesday if days_until_wednesday > 0 else 7)
    
    # Próximo domingo (dia 6)
    days_until_sunday = (6 - weekday) % 7
    if days_until_sunday == 0 and datetime.now().hour >= 19:
        days_until_sunday = 7
    next_sunday = today + timedelta(days=days_until_sunday if days_until_sunday > 0 else 7)
    
    context = {
        'member': member,
        'words': words,
        'service_type': service_type,
        'service_type_display': 'Domingo' if service_type == 'sunday' else 'Quarta-feira',
        'start_date': start_date,
        'today': today,
        'next_wednesday': next_wednesday,
        'next_sunday': next_sunday,
    }
    
    return render(request, 'words/service_words.html', context)


@login_required
def mark_word_as_healed(request, word_id):
    """Marcar palavra de conhecimento como resultou em cura"""
    try:
        member = request.user.member
    except Member.DoesNotExist:
        messages.error(request, 'Você precisa ter um perfil de membro.')
        return redirect('member_dashboard')
    
    word = get_object_or_404(WordOfKnowledge, id=word_id, member=member)
    
    if request.method == 'POST':
        word.resulted_in_healing = not word.resulted_in_healing
        word.save()
        
        status = 'marcada como resultou em cura' if word.resulted_in_healing else 'desmarcada'
        messages.success(request, f'Palavra {status}!')
    
    return redirect('word_of_knowledge_list')
