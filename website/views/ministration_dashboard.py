from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count, Q
from django.db.models.functions import TruncMonth
from datetime import datetime, timedelta, date
from collections import defaultdict

from website.models import WordOfKnowledge, Healing, Member, Ministry
from website.models.ministry_membership import MinistryMembership
from website.models.schedule import MonthlySchedule, ScheduleDay


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
        # Membros do sistema antigo
        members_old = set(Member.objects.filter(ministry=ministry).values_list('id', flat=True))
        
        # Membros do sistema novo
        members_new = set(MinistryMembership.objects.filter(
            ministry=ministry,
            is_active=True
        ).values_list('member_id', flat=True))
        
        # Total único
        all_member_ids = members_old | members_new
        total_ministry_members = len(all_member_ids)
        
        # Ativos
        active_member_ids = Member.objects.filter(
            id__in=all_member_ids,
            is_active=True
        ).values_list('id', flat=True)
        active_ministry_members = len(active_member_ids)
    else:
        total_ministry_members = 0
        active_ministry_members = 0
    
    # Escala atual (semana atual)
    today = date.today()
    week_start = today - timedelta(days=today.weekday())  # Segunda-feira da semana atual
    week_end = week_start + timedelta(days=6)  # Domingo da semana atual
    
    # Buscar dias da escala na semana atual do ministério de ministração
    current_schedule_days = None
    if ministry:
        current_schedule_days = ScheduleDay.objects.filter(
            schedule__ministry=ministry,
            date__gte=week_start,
            date__lte=week_end,
            is_cancelled=False
        ).select_related('schedule', 'team').prefetch_related('members', 'team__members').order_by('date')
    
    # Próximas escalas (próximas 3 semanas com escalas)
    upcoming_schedule_weeks = []
    if ministry:
        # Buscar próximos dias de escala após esta semana
        next_days = ScheduleDay.objects.filter(
            schedule__ministry=ministry,
            date__gt=week_end,
            is_cancelled=False
        ).select_related('schedule', 'team').prefetch_related('members', 'team__members').order_by('date')[:21]  # 3 semanas * 7 dias
        
        # Agrupar por semana
        weeks_dict = {}
        for day in next_days:
            day_week_start = day.date - timedelta(days=day.date.weekday())
            if day_week_start not in weeks_dict:
                weeks_dict[day_week_start] = []
            weeks_dict[day_week_start].append(day)
        
        # Pegar as 3 primeiras semanas
        upcoming_schedule_weeks = [
            {'week_start': week_start, 'days': days}
            for week_start, days in sorted(weeks_dict.items())[:3]
        ]
    
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
        'current_schedule_days': current_schedule_days,
        'upcoming_schedule_weeks': upcoming_schedule_weeks,
        'recent_words': recent_words,
        'recent_healings': recent_healings,
        'monthly_stats': monthly_stats,
        'current_year': current_year,
    }
    
    return render(request, 'admin_panel/ministration/dashboard.html', context)
