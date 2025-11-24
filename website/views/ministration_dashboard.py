from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count, Q
from django.db.models.functions import TruncMonth
from datetime import datetime, timedelta, date
from collections import defaultdict

from website.models import WordOfKnowledge, Healing, Member, Ministry, MinistrationSchedule


@login_required
def ministration_dashboard(request):
    """Dashboard principal do Ministério de Ministração"""
    if not request.user.is_staff and request.user.user_type != 'admin':
        messages.error(request, 'Você não tem permissão para acessar esta página.')
        return redirect('admin_dashboard')
    
    # Buscar ministério de ministração
    ministry = Ministry.objects.filter(name__icontains='ministração').first()
    
    # Estatísticas
    total_words = WordOfKnowledge.objects.count()
    total_healings = Healing.objects.count()
    
    # Membros do ministério
    if ministry:
        total_ministry_members = Member.objects.filter(ministry=ministry).count()
        active_ministry_members = Member.objects.filter(ministry=ministry, is_active=True).count()
    else:
        total_ministry_members = 0
        active_ministry_members = 0
    
    # Escala atual
    today = date.today()
    week_start = today - timedelta(days=today.weekday())  # Segunda-feira da semana atual
    current_schedule = MinistrationSchedule.objects.filter(
        week_start_date=week_start,
        is_active=True
    ).prefetch_related('members').first()
    
    # Próximas escalas
    upcoming_schedules = MinistrationSchedule.objects.filter(
        week_start_date__gt=week_start,
        is_active=True
    ).order_by('week_start_date')[:3]
    
    # Últimas palavras registradas
    recent_words = WordOfKnowledge.objects.select_related('member').order_by('-recorded_at')[:5]
    
    # Últimas curas registradas
    recent_healings = Healing.objects.select_related('member').order_by('-recorded_at')[:5]
    
    # Estatísticas de curas por mês (ano atual)
    current_year = datetime.now().year
    healings_by_month = Healing.objects.filter(
        healing_date__year=current_year
    ).extra(
        select={'month': 'EXTRACT(month FROM healing_date)'}
    ).values('month').annotate(
        count=Count('id')
    ).order_by('month')
    
    # Criar dicionário com todos os meses (1-12)
    monthly_healings = {i: 0 for i in range(1, 13)}
    for item in healings_by_month:
        monthly_healings[int(item['month'])] = item['count']
    
    # Calcular porcentagens baseadas no valor máximo
    max_healings = max(monthly_healings.values()) if monthly_healings.values() else 1
    monthly_stats = []
    month_names = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho', 
                   'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
    
    for month_num in range(1, 13):
        count = monthly_healings[month_num]
        percentage = (count / max_healings * 100) if max_healings > 0 else 0
        monthly_stats.append({
            'month_name': month_names[month_num - 1],
            'count': count,
            'percentage': percentage
        })
    
    context = {
        'title': 'Dashboard - Ministração',
        'ministry': ministry,
        'total_words': total_words,
        'total_healings': total_healings,
        'total_ministry_members': total_ministry_members,
        'active_ministry_members': active_ministry_members,
        'current_schedule': current_schedule,
        'upcoming_schedules': upcoming_schedules,
        'recent_words': recent_words,
        'recent_healings': recent_healings,
        'monthly_stats': monthly_stats,
        'current_year': current_year,
    }
    
    return render(request, 'admin_panel/ministration/dashboard.html', context)
