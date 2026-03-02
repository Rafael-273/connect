from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse, HttpResponse
from django.db.models import Q, Count
from django.db import IntegrityError
from django.template.loader import get_template
from datetime import datetime
from calendar import monthrange
from django.conf import settings
import os
from ..models import MonthlySchedule, ScheduleDay, Team, Ministry, Member
from ..models.ministry_membership import MinistryMembership
from xhtml2pdf import pisa


WEEKDAYS_PT = [
    'Segunda-feira',
    'Terça-feira',
    'Quarta-feira',
    'Quinta-feira',
    'Sexta-feira',
    'Sábado',
    'Domingo'
]

def is_admin(user):
    """Verifica se o usuário é admin"""
    return user.is_authenticated and user.is_staff


# ==================== TEAM VIEWS ====================

@user_passes_test(is_admin)
def team_list_view(request):
    """Lista todas as equipes"""
    teams = Team.objects.select_related('ministry', 'leader').prefetch_related('members').filter(deleted__isnull=True)
    
    # Filtros
    ministry_id = request.GET.get('ministry')
    search = request.GET.get('search', '')
    
    if ministry_id:
        teams = teams.filter(ministry_id=ministry_id)
    
    if search:
        teams = teams.filter(
            Q(name__icontains=search) |
            Q(ministry__name__icontains=search)
        )
    
    # Adicionar contagem de membros
    teams = teams.annotate(member_count=Count('members'))
    
    ministries = Ministry.objects.filter(deleted__isnull=True).order_by('name')
    
    context = {
        'teams': teams.order_by('ministry__name', 'name'),
        'ministries': ministries,
        'selected_ministry': ministry_id,
        'search': search
    }
    
    return render(request, 'admin_panel/schedules/teams/list.html', context)


@user_passes_test(is_admin)
def team_create_view(request):
    """Cria uma nova equipe"""
    if request.method == 'POST':
        name = request.POST.get('name')
        ministry_id = request.POST.get('ministry')
        leader_id = request.POST.get('leader')
        color = request.POST.get('color', '#3B82F6')
        member_ids = request.POST.getlist('members')
        
        ministry = get_object_or_404(Ministry, id=ministry_id)
        
        team = Team.objects.create(
            name=name,
            ministry=ministry,
            leader_id=leader_id if leader_id else None,
            color=color
        )
        
        if member_ids:
            team.members.set(member_ids)
        
        return redirect('team_list')
    
    ministries = Ministry.objects.filter(deleted__isnull=True).order_by('name')
    
    # Buscar todos os membros ativos
    all_members = Member.objects.filter(
        is_active=True,
        deleted__isnull=True
    ).prefetch_related('ministry').order_by('name')
    
    # Criar mapeamento de membros para ministérios (sistema híbrido)
    members_with_ministries = []
    for member in all_members:
        # Ministérios do sistema antigo
        ministry_ids_old = set(member.ministry.values_list('id', flat=True))
        
        # Ministérios do sistema novo
        ministry_ids_new = set(
            MinistryMembership.objects.filter(
                member=member,
                is_active=True
            ).values_list('ministry_id', flat=True)
        )
        
        # Combinar ambos
        all_ministry_ids = ministry_ids_old | ministry_ids_new
        
        if all_ministry_ids:  # Só incluir membros que estão em algum ministério
            members_with_ministries.append({
                'id': member.id,
                'name': member.name,
                'ministry_ids': list(all_ministry_ids)
            })
    
    context = {
        'ministries': ministries,
        'members': all_members,
        'members_with_ministries': members_with_ministries
    }
    
    return render(request, 'admin_panel/schedules/teams/form.html', context)


@user_passes_test(is_admin)
def team_edit_view(request, team_id):
    """Edita uma equipe"""
    team = get_object_or_404(Team, id=team_id, deleted__isnull=True)
    
    if request.method == 'POST':
        team.name = request.POST.get('name')
        team.ministry_id = request.POST.get('ministry')
        leader_id = request.POST.get('leader')
        team.leader_id = leader_id if leader_id else None
        team.color = request.POST.get('color', '#3B82F6')
        
        member_ids = request.POST.getlist('members')
        team.members.set(member_ids)
        
        team.save()
        
        return redirect('team_list')
    
    ministries = Ministry.objects.filter(deleted__isnull=True).order_by('name')
    
    # Buscar todos os membros ativos
    all_members = Member.objects.filter(
        is_active=True,
        deleted__isnull=True
    ).prefetch_related('ministry').order_by('name')
    
    # Criar mapeamento de membros para ministérios (sistema híbrido)
    members_with_ministries = []
    for member in all_members:
        # Ministérios do sistema antigo
        ministry_ids_old = set(member.ministry.values_list('id', flat=True))
        
        # Ministérios do sistema novo
        ministry_ids_new = set(
            MinistryMembership.objects.filter(
                member=member,
                is_active=True
            ).values_list('ministry_id', flat=True)
        )
        
        # Combinar ambos
        all_ministry_ids = ministry_ids_old | ministry_ids_new
        
        if all_ministry_ids:  # Só incluir membros que estão em algum ministério
            members_with_ministries.append({
                'id': member.id,
                'name': member.name,
                'ministry_ids': list(all_ministry_ids)
            })
    
    context = {
        'team': team,
        'ministries': ministries,
        'members': all_members,
        'members_with_ministries': members_with_ministries
    }
    
    return render(request, 'admin_panel/schedules/teams/form.html', context)


@user_passes_test(is_admin)
def team_delete_view(request, team_id):
    """Deleta uma equipe"""
    if request.method == 'POST':
        team = get_object_or_404(Team, id=team_id, deleted__isnull=True)
        team.delete()
        
        return JsonResponse({'success': True})
    
    return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


# ==================== MONTHLY SCHEDULE VIEWS ====================

@user_passes_test(is_admin)
def schedule_list_view(request):
    """Lista todas as escalas mensais"""
    schedules = MonthlySchedule.objects.select_related('ministry').filter(deleted__isnull=True)
    
    # Filtros
    ministry_id = request.GET.get('ministry')
    year = request.GET.get('year')
    month = request.GET.get('month')
    search = request.GET.get('search', '')
    
    if ministry_id:
        schedules = schedules.filter(ministry_id=ministry_id)
    
    if year:
        schedules = schedules.filter(year=year)
    
    if month:
        schedules = schedules.filter(month=month)
    
    if search:
        schedules = schedules.filter(
            Q(title__icontains=search) |
            Q(ministry__name__icontains=search)
        )
    
    # Anos disponíveis
    current_year = datetime.now().year
    years = range(current_year - 1, current_year + 2)
    
    ministries = Ministry.objects.filter(deleted__isnull=True).order_by('name')
    
    context = {
        'schedules': schedules.order_by('-year', '-month', 'ministry__name'),
        'ministries': ministries,
        'years': years,
        'months': MonthlySchedule.MONTH_CHOICES,
        'selected_ministry': ministry_id,
        'selected_year': year,
        'selected_month': month,
        'search': search
    }
    
    return render(request, 'admin_panel/schedules/monthly/list.html', context)


@user_passes_test(is_admin)
def schedule_create_view(request):
    """Cria uma nova escala mensal"""
    if request.method == 'POST':
        ministry_id = request.POST.get('ministry')
        title = request.POST.get('title')
        month = request.POST.get('month')
        year = request.POST.get('year')
        # removed optional name/color fields
        use_team_rotation = request.POST.get('use_team_rotation') == 'true'
        guidelines = request.POST.get('guidelines') or None
        
        ministry = get_object_or_404(Ministry, id=ministry_id)
        
        schedule = MonthlySchedule.objects.create(
            ministry=ministry,
            title=title,
            month=int(month),
            year=int(year),
            use_team_rotation=use_team_rotation,
            guidelines=guidelines
        )
        
        return redirect('schedule_detail', schedule_id=schedule.id)
    
    ministries = Ministry.objects.filter(deleted__isnull=True).order_by('name')
    current_year = datetime.now().year
    years = range(current_year, current_year + 2)
    
    context = {
        'ministries': ministries,
        'years': years,
        'months': MonthlySchedule.MONTH_CHOICES,
        'current_month': datetime.now().month,
        'current_year': current_year
    }
    
    return render(request, 'admin_panel/schedules/monthly/form.html', context)


@user_passes_test(is_admin)
def schedule_edit_view(request, schedule_id):
    """Edita uma escala mensal"""
    schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)
    
    if request.method == 'POST':
        schedule.ministry_id = request.POST.get('ministry')
        schedule.title = request.POST.get('title')
        schedule.month = int(request.POST.get('month'))
        schedule.year = int(request.POST.get('year'))
        # removed optional name/color assignments
        schedule.use_team_rotation = request.POST.get('use_team_rotation') == 'true'
        schedule.guidelines = request.POST.get('guidelines') or None
        
        schedule.save()
        
        return redirect('schedule_detail', schedule_id=schedule.id)
    
    ministries = Ministry.objects.filter(deleted__isnull=True).order_by('name')
    current_year = datetime.now().year
    years = range(current_year - 1, current_year + 2)
    
    context = {
        'schedule': schedule,
        'ministries': ministries,
        'years': years,
        'months': MonthlySchedule.MONTH_CHOICES
    }
    
    return render(request, 'admin_panel/schedules/monthly/form.html', context)


@user_passes_test(is_admin)
def schedule_detail_view(request, schedule_id):
    """Visualiza detalhes de uma escala mensal"""
    schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)
    
    # Buscar todos os dias da escala
    days = ScheduleDay.objects.filter(
        schedule=schedule,
        deleted__isnull=True
    ).select_related('team').prefetch_related('members').order_by('date')
    
    # Buscar equipes do ministério
    teams = Team.objects.filter(
        ministry=schedule.ministry,
        is_active=True,
        deleted__isnull=True
    ).prefetch_related('members')
    
    # Buscar membros do ministério (sistema antigo e novo - híbrido)
    # Membros do sistema antigo
    members_old = Member.objects.filter(
        ministry=schedule.ministry,
        is_active=True,
        deleted__isnull=True
    )
    
    # Membros do sistema novo
    memberships_new = MinistryMembership.objects.filter(
        ministry=schedule.ministry,
        is_active=True
    ).select_related('member')
    
    # Combinar ambos os sistemas sem duplicatas
    member_ids_old = set(members_old.values_list('id', flat=True))
    member_ids_new = set(memberships_new.values_list('member_id', flat=True))
    all_member_ids = member_ids_old | member_ids_new
    
    # Buscar todos os membros únicos
    members = Member.objects.filter(
        id__in=all_member_ids,
        is_active=True,
        deleted__isnull=True
    ).order_by('name')
    
    # Organizar dias por semana
    weeks = {}
    for day in days:
        # attach Portuguese weekday name for template use
        try:
            day.pt_weekday = WEEKDAYS_PT[day.date.weekday()]
        except Exception:
            day.pt_weekday = ''
        week_num = day.get_week_number()
        if week_num not in weeks:
            weeks[week_num] = []
        weeks[week_num].append(day)
    
    context = {
        'schedule': schedule,
        'days': days,
        'weeks': weeks,
        'teams': teams,
        'members': members
    }
    
    return render(request, 'admin_panel/schedules/monthly/detail.html', context)


@user_passes_test(is_admin)
def schedule_delete_view(request, schedule_id):
    """Deleta uma escala mensal"""
    if request.method == 'POST':
        schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)
        schedule.delete()
        
        return JsonResponse({'success': True})
    
    return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


# Publishing feature removed — schedules are considered active by creation month


# ==================== SCHEDULE DAY VIEWS ====================

@user_passes_test(is_admin)
def schedule_day_create_view(request, schedule_id):
    """Adiciona um dia à escala"""
    if request.method == 'POST':
        from safedelete.models import HARD_DELETE
        
        schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)
        
        date_str = request.POST.get('date')
        date = datetime.strptime(date_str, '%Y-%m-%d').date()
        
        team_id = request.POST.get('team')
        member_ids = request.POST.getlist('members')
        description = request.POST.get('description') or None
        notes = request.POST.get('notes') or None
        
        # Verifica se já existe um dia ATIVO para esta data nesta escala
        existing_active_day = ScheduleDay.objects.filter(
            schedule=schedule,
            date=date
        ).first()
        
        if existing_active_day:
            # Se já existe um dia ativo, retorna erro
            return JsonResponse({
                'success': False,
                'error': f'Já existe um dia escalado para {date.strftime("%d/%m/%Y")} nesta escala. Edite o dia existente ao invés de criar um novo.'
            }, status=400)
        
        # Verifica se existe um dia SOFT-DELETED para esta data
        soft_deleted_day = ScheduleDay.all_objects.filter(
            schedule=schedule,
            date=date,
            deleted__isnull=False
        ).first()
        
        if soft_deleted_day:
            # Remove permanentemente o registro soft-deleted para evitar conflito de constraint
            soft_deleted_day.delete(force_policy=HARD_DELETE)
        
        try:
            # Cria o novo dia
            day = ScheduleDay.objects.create(
                schedule=schedule,
                date=date,
                team_id=team_id if team_id else None,
                description=description,
                notes=notes
            )
            
            if member_ids:
                day.members.set(member_ids)
            
            return JsonResponse({
                'success': True,
                'day_id': day.id,
                'date': day.date.strftime('%d/%m/%Y')
            })
        except IntegrityError as e:
            # Captura erro de duplicação (caso ainda ocorra por race condition)
            return JsonResponse({
                'success': False,
                'error': f'Já existe um dia escalado para {date.strftime("%d/%m/%Y")} nesta escala. Erro: {str(e)}'
            }, status=400)
    
    return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


@user_passes_test(is_admin)
def schedule_day_edit_view(request, day_id):
    """Edita um dia da escala"""
    if request.method == 'POST':
        day = get_object_or_404(ScheduleDay, id=day_id, deleted__isnull=True)
        
        team_id = request.POST.get('team')
        member_ids = request.POST.getlist('members')
        description = request.POST.get('description') or None
        notes = request.POST.get('notes') or None
        
        day.team_id = team_id if team_id else None
        day.description = description
        day.notes = notes
        day.save()
        
        day.members.set(member_ids)
        
        return JsonResponse({'success': True})
    
    return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


@user_passes_test(is_admin)
def schedule_day_delete_view(request, day_id):
    """Deleta um dia da escala"""
    if request.method == 'POST':
        day = get_object_or_404(ScheduleDay, id=day_id, deleted__isnull=True)
        day.delete()
        
        return JsonResponse({'success': True})
    
    return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


@user_passes_test(is_admin)
def schedule_day_toggle_cancel_view(request, day_id):
    """Cancela/descancela um dia da escala"""
    if request.method == 'POST':
        day = get_object_or_404(ScheduleDay, id=day_id, deleted__isnull=True)
        
        action = request.POST.get('action')
        if action == 'cancel':
            day.is_cancelled = True
            day.cancellation_reason = request.POST.get('reason')
        elif action == 'uncancel':
            day.is_cancelled = False
            day.cancellation_reason = None
        
        day.save()
        
        return JsonResponse({
            'success': True,
            'is_cancelled': day.is_cancelled
        })
    
    return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


@user_passes_test(is_admin)
def schedule_export_pdf_view(request, schedule_id):
    """Exporta a escala em PDF"""
    schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)
    days = ScheduleDay.objects.filter(
        schedule=schedule,
        deleted__isnull=True
    ).select_related('team').prefetch_related('members').order_by('date')
    
    # Preparar contexto para o template
    month_names = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
                   "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
    
    day_names = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']
    
    # Processar dias para incluir dia da semana
    processed_days = []
    for day in days:
        day_data = {
            'date': day.date,
            'date_str': day.date.strftime('%d/%m/%Y'),
            'day_name': day_names[day.date.weekday()],
            'description': day.description,
            'notes': day.notes,
            'is_cancelled': day.is_cancelled,
            'cancellation_reason': day.cancellation_reason,
        }
        
        if schedule.use_team_rotation:
            day_data['team'] = day.team
        else:
            day_data['members'] = list(day.members.all())
        
        processed_days.append(day_data)
    
    # Caminho absoluto para a logo
    logo_path = os.path.join(settings.BASE_DIR, 'static', 'assets', 'red_logo.png')
    
    context = {
        'schedule': schedule,
        'days': processed_days,
        'month_name': month_names[schedule.month - 1],
        'generated_at': datetime.now().strftime('%d/%m/%Y às %H:%M'),
        'use_team_rotation': schedule.use_team_rotation,
        'logo_path': logo_path
    }
    
    # Renderizar template HTML
    template = get_template('admin_panel/schedules/monthly/pdf_template.html')
    html = template.render(context)
    
    # Criar resposta PDF
    response = HttpResponse(content_type='application/pdf')
    filename = f"escala_{schedule.ministry.name.replace(' ', '_')}_{month_names[schedule.month - 1]}_{schedule.year}.pdf"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    # Converter HTML para PDF com encoding UTF-8
    pisa_status = pisa.CreatePDF(
        html.encode('utf-8'),
        dest=response,
        encoding='utf-8'
    )
    
    if pisa_status.err:
        return HttpResponse('Erro ao gerar PDF', status=500)
    
    return response


@user_passes_test(is_admin)
def schedule_print_view(request, schedule_id):
    """Exibe a escala em formato printável para impressão nativa do navegador"""
    schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)
    days = ScheduleDay.objects.filter(
        schedule=schedule,
        deleted__isnull=True
    ).select_related('team').prefetch_related('members').order_by('date')

    month_names = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
                   "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]

    day_names = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']

    processed_days = []
    for day in days:
        day_data = {
            'date': day.date,
            'date_str': day.date.strftime('%d/%m/%Y'),
            'day_name': day_names[day.date.weekday()],
            'description': day.description,
            'notes': day.notes,
            'is_cancelled': day.is_cancelled,
            'cancellation_reason': day.cancellation_reason,
        }

        if schedule.use_team_rotation:
            day_data['team'] = day.team
        else:
            day_data['members'] = list(day.members.all())

        processed_days.append(day_data)

    context = {
        'schedule': schedule,
        'days': processed_days,
        'month_name': month_names[schedule.month - 1],
        'generated_at': datetime.now().strftime('%d/%m/%Y às %H:%M'),
        'use_team_rotation': schedule.use_team_rotation,
    }

    return render(request, 'admin_panel/schedules/monthly/print.html', context)
