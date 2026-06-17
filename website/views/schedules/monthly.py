from collections import OrderedDict
from datetime import datetime, timedelta, date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, JsonResponse
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from ..mixins import StaffRequiredMixin
from ...models import Ministry, MonthlySchedule, ScheduleDay, Team
from ...models.member import Member
from ...models.ministry_membership import MinistryMembership
from ...models.schedule import DivisionMember, ScaleDivision

WEEKDAYS_PT = [
    'Segunda-feira',
    'Terça-feira',
    'Quarta-feira',
    'Quinta-feira',
    'Sexta-feira',
    'Sábado',
    'Domingo',
]


# ---------------------------------------------------------------------------
# Helper: gera ScheduleDay para escalas semanais
# ---------------------------------------------------------------------------

def _generate_weekly_days(schedule):
    """Garante que todos os ScheduleDay da escala semanal existam no banco.

    Percorre o intervalo [start_date, end_date] (ou até hoje+90 dias se sem
    end_date) e cria ScheduleDay para cada ocorrência de days_of_week.
    Não recria dias já existentes.
    """
    if not schedule.is_weekly:
        return

    day_list = schedule.get_days_of_week_list()
    if not day_list:
        return

    start = schedule.start_date
    end = schedule.end_date or (date.today() + timedelta(days=90))

    existing_dates = set(
        ScheduleDay.objects
        .filter(schedule=schedule, deleted__isnull=True)
        .values_list('date', flat=True)
    )

    current = start
    to_create = []
    while current <= end:
        if current.weekday() in day_list and current not in existing_dates:
            to_create.append(ScheduleDay(
                schedule=schedule,
                date=current,
                day_of_week=current.weekday(),
            ))
        current += timedelta(days=1)

    if to_create:
        ScheduleDay.objects.bulk_create(to_create)


# ---------------------------------------------------------------------------
# LIST
# ---------------------------------------------------------------------------

class ScheduleListView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Lista todas as escalas (mensais e semanais)."""

    def get(self, request):
        ministry_id = request.GET.get('ministry')
        search = request.GET.get('search', '')
        schedule_type = request.GET.get('type', '')
        year = request.GET.get('year')
        month = request.GET.get('month')
        current_year = datetime.now().year

        qs = MonthlySchedule.objects.select_related('ministry').filter(deleted__isnull=True)

        if ministry_id:
            qs = qs.filter(ministry_id=ministry_id)
        if schedule_type:
            qs = qs.filter(schedule_type=schedule_type)
        if year:
            qs = qs.filter(year=year)
        if month:
            qs = qs.filter(month=month)
        if search:
            qs = qs.filter(
                Q(title__icontains=search) | Q(ministry__name__icontains=search)
            )

        qs = qs.order_by('-year', '-month', '-start_date', 'ministry__name')

        context = {
            'schedules': qs,
            'ministries': Ministry.objects.filter(deleted__isnull=True).order_by('name'),
            'years': range(current_year - 1, current_year + 2),
            'months': MonthlySchedule.MONTH_CHOICES,
            'selected_ministry': ministry_id,
            'selected_year': year,
            'selected_month': month,
            'selected_type': schedule_type,
            'search': search,
        }
        return render(request, 'admin_panel/schedules/monthly/list.html', context)


# ---------------------------------------------------------------------------
# CREATE / EDIT
# ---------------------------------------------------------------------------

class ScheduleCreateView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Cria uma nova escala (mensal ou semanal)."""

    def get(self, request):
        current_year = datetime.now().year
        context = {
            'ministries': Ministry.objects.filter(deleted__isnull=True).order_by('name'),
            'years': range(current_year, current_year + 2),
            'months': MonthlySchedule.MONTH_CHOICES,
            'current_month': datetime.now().month,
            'current_year': current_year,
            'week_days_choices': MonthlySchedule.WEEK_DAYS_CHOICES,
            'initial_type': request.GET.get('type', 'monthly'),
        }
        return render(request, 'admin_panel/schedules/monthly/form.html', context)

    def post(self, request):
        schedule_type = request.POST.get('schedule_type', MonthlySchedule.TYPE_MONTHLY)
        ministry_id = request.POST.get('ministry')
        title = request.POST.get('title', '').strip()
        use_team_rotation = request.POST.get('use_team_rotation') == 'true'
        guidelines = request.POST.get('guidelines') or None

        ministry = get_object_or_404(Ministry, id=ministry_id)

        if schedule_type == MonthlySchedule.TYPE_WEEKLY:
            start_date_str = request.POST.get('start_date', '').strip()
            end_date_str = request.POST.get('end_date', '').strip()
            days_of_week = request.POST.getlist('days_of_week')

            schedule = MonthlySchedule.objects.create(
                schedule_type=MonthlySchedule.TYPE_WEEKLY,
                ministry=ministry,
                title=title,
                start_date=datetime.strptime(start_date_str, '%Y-%m-%d').date(),
                end_date=datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else None,
                use_team_rotation=use_team_rotation,
                guidelines=guidelines,
            )
            schedule.set_days_of_week([int(d) for d in days_of_week])
            schedule.save()
            _generate_weekly_days(schedule)
            division_names = [n.strip() for n in request.POST.getlist('divisions') if n.strip()]
            for i, name in enumerate(division_names):
                ScaleDivision.objects.create(schedule=schedule, name=name, order=i)
        else:
            month = request.POST.get('month')
            year = request.POST.get('year')

            schedule = MonthlySchedule.objects.create(
                schedule_type=MonthlySchedule.TYPE_MONTHLY,
                ministry=ministry,
                title=title,
                month=int(month),
                year=int(year),
                use_team_rotation=use_team_rotation,
                guidelines=guidelines,
            )

            division_names = [n.strip() for n in request.POST.getlist('divisions') if n.strip()]
            for i, name in enumerate(division_names):
                ScaleDivision.objects.create(schedule=schedule, name=name, order=i)

        return redirect('schedule_detail', schedule_id=schedule.id)


class ScheduleEditView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Edita uma escala existente (mensal ou semanal)."""

    def get(self, request, schedule_id):
        schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)
        current_year = datetime.now().year
        existing_divisions = list(
            ScaleDivision.objects.filter(schedule=schedule, deleted__isnull=True)
            .order_by('order', 'name')
            .values_list('name', flat=True)
        )
        context = {
            'schedule': schedule,
            'ministries': Ministry.objects.filter(deleted__isnull=True).order_by('name'),
            'years': range(current_year - 1, current_year + 2),
            'months': MonthlySchedule.MONTH_CHOICES,
            'week_days_choices': MonthlySchedule.WEEK_DAYS_CHOICES,
            'existing_divisions': existing_divisions,
        }
        return render(request, 'admin_panel/schedules/monthly/form.html', context)

    def post(self, request, schedule_id):
        schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)

        schedule.ministry_id = request.POST.get('ministry')
        schedule.title = request.POST.get('title', '').strip()
        schedule.use_team_rotation = request.POST.get('use_team_rotation') == 'true'
        schedule.guidelines = request.POST.get('guidelines') or None

        if schedule.is_weekly:
            start_date_str = request.POST.get('start_date', '').strip()
            end_date_str = request.POST.get('end_date', '').strip()
            days_of_week = request.POST.getlist('days_of_week')
            schedule.start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            schedule.end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else None
            schedule.set_days_of_week([int(d) for d in days_of_week])
            schedule.save()
            _generate_weekly_days(schedule)
            ScaleDivision.objects.filter(schedule=schedule, deleted__isnull=True).delete()
            division_names = [n.strip() for n in request.POST.getlist('divisions') if n.strip()]
            for i, name in enumerate(division_names):
                ScaleDivision.objects.create(schedule=schedule, name=name, order=i)
        else:
            schedule.month = int(request.POST.get('month'))
            schedule.year = int(request.POST.get('year'))
            schedule.save()

            ScaleDivision.objects.filter(schedule=schedule, deleted__isnull=True).delete()
            division_names = [n.strip() for n in request.POST.getlist('divisions') if n.strip()]
            for i, name in enumerate(division_names):
                ScaleDivision.objects.create(schedule=schedule, name=name, order=i)

        return redirect('schedule_detail', schedule_id=schedule.id)


# ---------------------------------------------------------------------------
# DETAIL
# ---------------------------------------------------------------------------

class ScheduleDetailView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Visualiza detalhes de uma escala (mensal ou semanal)."""

    def get(self, request, schedule_id):
        schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)

        if schedule.is_weekly:
            _generate_weekly_days(schedule)

        days_qs = (
            ScheduleDay.objects
            .filter(schedule=schedule, deleted__isnull=True)
            .select_related('team')
            .prefetch_related(
                'members',
                'members_morning',
                'members_evening',
                Prefetch(
                    'division_assignments',
                    queryset=DivisionMember.objects.filter(
                        deleted__isnull=True
                    ).select_related('division', 'member').order_by(
                        'division__order', 'division__name', 'member__name'
                    ),
                ),
            )
            .order_by('date')
        )

        if schedule.is_weekly and schedule.end_date:
            days_qs = days_qs.filter(date__lte=schedule.end_date)

        days = list(days_qs)

        teams = (
            Team.objects
            .filter(ministry=schedule.ministry, is_active=True, deleted__isnull=True)
            .prefetch_related('members')
        )

        member_ids = set(
            MinistryMembership.objects
            .filter(ministry=schedule.ministry, is_active=True)
            .values_list('member_id', flat=True)
        )
        members = (
            Member.objects
            .filter(id__in=member_ids, is_active=True, deleted__isnull=True)
            .order_by('name')
        )

        weeks = {}
        for day in days:
            try:
                day.pt_weekday = WEEKDAYS_PT[day.date.weekday()]
            except Exception:
                day.pt_weekday = ''

            if schedule.is_monthly:
                week_num = day.get_week_number()
            else:
                week_num = day.date.isocalendar()[1]

            weeks.setdefault(week_num, []).append(day)

            groups, grouped = {}, []
            groups_morning, grouped_morning = {}, []
            groups_evening, grouped_evening = {}, []
            for a in day.division_assignments.all():
                if a.shift == 'morning':
                    if a.division_id not in groups_morning:
                        groups_morning[a.division_id] = {'division': a.division, 'members': []}
                        grouped_morning.append(groups_morning[a.division_id])
                    groups_morning[a.division_id]['members'].append(a.member)
                elif a.shift == 'evening':
                    if a.division_id not in groups_evening:
                        groups_evening[a.division_id] = {'division': a.division, 'members': []}
                        grouped_evening.append(groups_evening[a.division_id])
                    groups_evening[a.division_id]['members'].append(a.member)
                else:
                    if a.division_id not in groups:
                        groups[a.division_id] = {'division': a.division, 'members': []}
                        grouped.append(groups[a.division_id])
                    groups[a.division_id]['members'].append(a.member)
            day.grouped_divisions = grouped
            day.morning_grouped_divisions = grouped_morning
            day.evening_grouped_divisions = grouped_evening

        divisions = ScaleDivision.objects.filter(
            schedule=schedule, deleted__isnull=True
        ).order_by('order', 'name')

        template = (
            'admin_panel/schedules/monthly/detail_weekly.html'
            if schedule.is_weekly
            else 'admin_panel/schedules/monthly/detail.html'
        )

        return render(request, template, {
            'schedule': schedule,
            'days': days,
            'weeks': weeks,
            'teams': teams,
            'members': members,
            'divisions': divisions,
        })


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------

class ScheduleDeleteView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Deleta uma escala (POST-only, retorna JSON)."""

    def post(self, request, schedule_id):
        schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)
        schedule.delete()
        return JsonResponse({'success': True})

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


# ---------------------------------------------------------------------------
# PRINT
# ---------------------------------------------------------------------------

@login_required
def schedule_print_view(request, schedule_id):
    """Exibe a escala em formato printável para impressão nativa do navegador."""
    try:
        schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)
    except Http404:
        messages.warning(request, 'A escala solicitada não foi encontrada. Use a lista de escalas para abrir a versão atual.')
        return redirect('schedule_list')

    days = ScheduleDay.objects.filter(
        schedule=schedule,
        deleted__isnull=True,
    ).select_related('team').prefetch_related(
        'members',
        'members_morning',
        'members_evening',
        'division_assignments',
        'division_assignments__division',
        'division_assignments__member',
    ).order_by('date')

    divisions = list(ScaleDivision.objects.filter(
        schedule=schedule,
        deleted__isnull=True,
        is_active=True,
    ).order_by('order', 'name'))

    month_names = ['Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
                   'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro']
    day_names = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']

    processed_days = []
    has_any_shifts = False
    has_notes_column = False
    for day in days:
        division_blocks = OrderedDict()
        has_division_shifts = False
        for assignment in day.division_assignments.all():
            block = division_blocks.setdefault(assignment.division_id, {
                'division': assignment.division,
                'members': [],
                'morning_members': [],
                'evening_members': [],
            })

            if assignment.shift == 'morning':
                block['morning_members'].append(assignment.member)
                has_division_shifts = True
            elif assignment.shift == 'evening':
                block['evening_members'].append(assignment.member)
                has_division_shifts = True
            else:
                block['members'].append(assignment.member)

        day_data = {
            'date': day.date,
            'date_str': day.date.strftime('%d/%m/%Y'),
            'day_name': day_names[day.date.weekday()],
            'description': day.description,
            'notes': day.notes,
            'is_cancelled': day.is_cancelled,
            'cancellation_reason': day.cancellation_reason,
            'team_name': day.team.name if day.team else '',
            'team_color': getattr(day.team, 'color', '') if day.team else '',
            'has_shifts': day.has_shifts,
            'has_division_shifts': has_division_shifts,
            'direct_members': list(day.members.all()),
            'morning_members': list(day.members_morning.all()) if day.has_shifts else [],
            'evening_members': list(day.members_evening.all()) if day.has_shifts else [],
            'division_blocks': list(division_blocks.values()),
            'division_blocks_map': division_blocks,
            'has_divisions': bool(division_blocks),
        }

        if day_data['has_shifts'] or day_data['has_division_shifts']:
            has_any_shifts = True
        if day_data['notes'] or day_data['cancellation_reason']:
            has_notes_column = True

        processed_days.append(day_data)

    if schedule.is_weekly:
        if schedule.start_date and schedule.end_date:
            period_label = f"{schedule.start_date.strftime('%d/%m/%Y')} a {schedule.end_date.strftime('%d/%m/%Y')}"
        elif schedule.start_date:
            period_label = f"A partir de {schedule.start_date.strftime('%d/%m/%Y')}"
        else:
            period_label = 'Escala semanal'
        month_name = 'Escala semanal'
    else:
        month_name = month_names[schedule.month - 1] if schedule.month else ''
        period_label = f"{month_name} de {schedule.year}" if month_name and schedule.year else schedule.title

    return render(request, 'admin_panel/schedules/monthly/print.html', {
        'schedule': schedule,
        'days': processed_days,
        'divisions': divisions,
        'month_name': month_name,
        'period_label': period_label,
        'schedule_type_label': 'Escala semanal' if schedule.is_weekly else 'Escala mensal',
        'assignment_mode_label': 'Rotação de equipes' if schedule.use_team_rotation else 'Membros diretos',
        'has_any_shifts': has_any_shifts,
        'has_notes_column': has_notes_column,
        'generated_at': datetime.now().strftime('%d/%m/%Y às %H:%M'),
        'use_team_rotation': schedule.use_team_rotation,
    })
