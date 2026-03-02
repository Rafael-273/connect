from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Q
from django.utils import timezone

from ..models.escala import Escala
from ..models.ministry import Ministry
from ..models.member import Member


def is_admin(user):
    return user.is_authenticated and user.is_staff


@user_passes_test(is_admin)
def minhas_escalas_view(request):
    """View para mostrar as escalas dos ministérios do usuário"""
    
    # Buscar o membro associado ao usuário
    try:
        member = Member.objects.get(user=request.user)
    except Member.DoesNotExist:
        # Se não há membro associado, mostrar página vazia
        context = {
            'escalas': [],
            'ministries_with_escalas': [],
            'user_has_member': False
        }
        return render(request, 'admin_panel/escalas/minhas_escalas.html', context)
    
    # Buscar escalas dos ministérios do usuário
    user_ministries = member.ministry.all()
    escalas = Escala.objects.filter(
        ministry__in=user_ministries
    ).select_related('ministry').prefetch_related('assigned_members').order_by('date')
    
    # Agrupar escalas por ministério
    ministries_with_escalas = {}
    for ministry in user_ministries:
        ministry_escalas = escalas.filter(ministry=ministry)
        if ministry_escalas.exists():
            ministries_with_escalas[ministry] = ministry_escalas
    
    # Separar escalas futuras e passadas
    today = timezone.now().date()
    future_escalas = escalas.filter(date__gte=today)
    past_escalas = escalas.filter(date__lt=today)
    
    context = {
        'escalas': escalas,
        'future_escalas': future_escalas,
        'past_escalas': past_escalas,
        'ministries_with_escalas': ministries_with_escalas,
        'user_has_member': True,
        'member': member
    }
    
    return render(request, 'admin_panel/escalas/minhas_escalas.html', context)


@user_passes_test(is_admin)
def ministry_escalas_view(request, ministry_id):
    """View para mostrar todas as escalas de um ministério específico"""
    
    ministry = get_object_or_404(Ministry, id=ministry_id)
    
    # Verificar se o usuário pertence ao ministério
    try:
        member = Member.objects.get(user=request.user)
        if ministry not in member.ministry.all():
            # Usuário não pertence ao ministério
            from django.contrib import messages
            from django.shortcuts import redirect
            messages.error(request, 'Você não tem permissão para ver as escalas deste ministério.')
            return redirect('admin_dashboard')
    except Member.DoesNotExist:
        from django.contrib import messages
        from django.shortcuts import redirect
        messages.error(request, 'Você precisa estar cadastrado como membro para ver escalas.')
        return redirect('admin_dashboard')
    
    # Buscar todas as escalas do ministério
    escalas = Escala.objects.filter(
        ministry=ministry
    ).prefetch_related('assigned_members').order_by('date')
    
    # Separar escalas futuras e passadas
    today = timezone.now().date()
    future_escalas = escalas.filter(date__gte=today)
    past_escalas = escalas.filter(date__lt=today)
    
    context = {
        'ministry': ministry,
        'escalas': escalas,
        'future_escalas': future_escalas,
        'past_escalas': past_escalas,
        'member': member
    }
    
    return render(request, 'admin_panel/escalas/ministry_escalas.html', context)