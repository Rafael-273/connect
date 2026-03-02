from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from datetime import timedelta
from ..models.schedule import Schedule
from ..models.ministry import Ministry
from ..models.member import Member


@login_required
def user_schedules_view(request):
    """Lista as escalas dos ministérios dos quais o usuário pertence"""
    # Busca o membro associado ao usuário logado
    try:
        member = Member.objects.get(user=request.user)
    except Member.DoesNotExist:
        # Se o usuário não tem member associado, mostra mensagem
        context = {
            'schedules': [],
            'user_ministries': [],
            'no_member': True
        }
        return render(request, 'admin_panel/schedules/user_schedules.html', context)
    
    # Busca os ministérios do usuário
    user_ministries = member.ministry.all()
    
    # Se não pertence a nenhum ministério
    if not user_ministries.exists():
        context = {
            'schedules': [],
            'user_ministries': [],
            'no_ministries': True
        }
        return render(request, 'admin_panel/schedules/user_schedules.html', context)
    
    # Busca escalas futuras dos ministérios do usuário
    future_date = timezone.now().date()
    schedules = Schedule.objects.filter(
        ministry__in=user_ministries,
        date__gte=future_date,
        status='active'
    ).select_related('member', 'ministry').order_by('date')
    
    # Separa as escalas do usuário das demais para destacar
    user_schedules = schedules.filter(member=member)
    other_schedules = schedules.exclude(member=member)
    
    context = {
        'schedules': schedules,
        'user_schedules': user_schedules,
        'other_schedules': other_schedules,
        'user_ministries': user_ministries,
        'member': member,
    }
    
    return render(request, 'admin_panel/schedules/user_schedules.html', context)


@login_required
def ministry_schedules_view(request, ministry_id):
    """Lista todas as escalas de um ministério específico"""
    ministry = get_object_or_404(Ministry, id=ministry_id)
    
    # Verifica se o usuário pertence ao ministério ou é admin
    try:
        member = Member.objects.get(user=request.user)
        has_access = member.ministry.filter(id=ministry_id).exists() or request.user.is_staff
    except Member.DoesNotExist:
        has_access = request.user.is_staff
    
    if not has_access:
        context = {
            'ministry': ministry,
            'schedules': [],
            'no_access': True
        }
        return render(request, 'admin_panel/schedules/ministry_schedules.html', context)
    
    # Busca todas as escalas do ministério (passadas e futuras)
    schedules = Schedule.objects.filter(
        ministry=ministry,
        status='active'
    ).select_related('member').order_by('-date')
    
    # Separar escalas futuras e passadas
    today = timezone.now().date()
    future_schedules = schedules.filter(date__gte=today)
    past_schedules = schedules.filter(date__lt=today)
    
    context = {
        'ministry': ministry,
        'schedules': schedules,
        'future_schedules': future_schedules,
        'past_schedules': past_schedules,
    }
    
    return render(request, 'admin_panel/schedules/ministry_schedules.html', context)