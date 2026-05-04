import json
from datetime import datetime, timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from ..mixins import StaffRequiredMixin
from ...models import Ministry, Member, MonthlySchedule
from ...models.ministry_membership import MinistryMembership
from ...models.schedule import Team, ScaleDivision
from ...models import WeeklySchedule, ScheduleWeekDay


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_week_days(schedule):
    """Gera os ScheduleWeekDay dentro do intervalo da escala sem recriar existentes."""
    day_indices = schedule.get_days_of_week_list()
    start_date = schedule.start_date
    end_date = schedule.end_date
    limit = end_date or (start_date + timedelta(days=365))

    days = []
    current_date = start_date
    while current_date <= limit:
        for day_idx in day_indices:
            days_until = (day_idx - current_date.weekday()) % 7
            target_date = current_date + timedelta(days=days_until)

            if target_date < start_date:
                continue
            if end_date and target_date > end_date:
                continue

            week_day, _ = ScheduleWeekDay.objects.get_or_create(
                weekly_schedule=schedule,
                date=target_date,
                day_of_week=day_idx,
                defaults={'description': ''},
            )
            days.append(week_day)
        current_date += timedelta(days=7)

    return sorted(set(days), key=lambda d: d.date)


def _generate_and_save_week_days(schedule):
    """Gera dias no intervalo e remove os que ficaram fora após edição da escala."""
    outside = ScheduleWeekDay.objects.filter(
        weekly_schedule=schedule, deleted__isnull=True
    )
    if schedule.end_date:
        outside.filter(date__gt=schedule.end_date).delete()

    ScheduleWeekDay.objects.filter(
        weekly_schedule=schedule,
        deleted__isnull=True,
        date__lt=schedule.start_date,
    ).delete()

    return _generate_week_days(schedule)


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------

class WeeklyScheduleListView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Lista todas as escalas semanais."""

    def get(self, request):
        if request.user.is_admin:
            schedules = (
                WeeklySchedule.objects
                .filter(deleted__isnull=True)
                .select_related('ministry')
            )
        else:
            schedules = (
                WeeklySchedule.objects
                .filter(
                    Q(deleted__isnull=True),
                    Q(is_published=True) | Q(ministry__coordinators=request.user),
                )
                .select_related('ministry')
                .distinct()
            )

        return render(request, 'admin_panel/schedules/weekly/list.html', {
            'schedules': schedules,
            'current_tab': 'weekly',
        })


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

class WeeklyScheduleDetailView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Visualiza uma escala semanal com seus dias."""

    def get(self, request, schedule_id):
        schedule = get_object_or_404(WeeklySchedule, id=schedule_id, deleted__isnull=True)
        _generate_and_save_week_days(schedule)

        qs = ScheduleWeekDay.objects.filter(
            weekly_schedule=schedule,
            deleted__isnull=True,
            date__gte=schedule.start_date,
        )
        if schedule.end_date:
            qs = qs.filter(date__lte=schedule.end_date)
        week_days = (
            qs.select_related('team')
            .prefetch_related('members', 'members_morning', 'members_evening')
            .order_by('date')
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
        teams = (
            Team.objects
            .filter(ministry=schedule.ministry, is_active=True, deleted__isnull=True)
            .prefetch_related('members')
        )
        divisions = (
            ScaleDivision.objects
            .filter(schedule__ministry=schedule.ministry, deleted__isnull=True, is_active=True)
            .select_related('schedule')
            .order_by('order', 'name')
        )

        return render(request, 'admin_panel/schedules/weekly/detail.html', {
            'schedule': schedule,
            'week_days': week_days,
            'total_days': week_days.count(),
            'members': members,
            'teams': teams,
            'divisions': divisions,
        })


# ---------------------------------------------------------------------------
# Create / Edit
# ---------------------------------------------------------------------------

class WeeklyScheduleCreateView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Cria uma nova escala semanal."""

    def get(self, request):
        current_year = datetime.now().year
        return render(request, 'admin_panel/schedules/monthly/form.html', {
            'initial_type': 'weekly',
            'ministries': Ministry.objects.filter(deleted__isnull=True).order_by('name'),
            'week_days_choices': WeeklySchedule.WEEK_DAYS_CHOICES,
            'months': MonthlySchedule.MONTH_CHOICES,
            'years': range(current_year - 1, current_year + 2),
        })

    def post(self, request):
        ministry_id = request.POST.get('ministry')
        title = request.POST.get('title', '').strip()
        days_of_week = request.POST.getlist('days_of_week')
        start_date_str = request.POST.get('start_date', '').strip()
        end_date_str = request.POST.get('end_date', '').strip()
        use_team_rotation = request.POST.get('use_team_rotation') == 'true'
        guidelines = request.POST.get('guidelines', '').strip()

        try:
            schedule = WeeklySchedule.objects.create(
                ministry_id=ministry_id,
                title=title,
                start_date=datetime.strptime(start_date_str, '%Y-%m-%d').date(),
                end_date=datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else None,
                use_team_rotation=use_team_rotation,
                guidelines=guidelines,
            )
        except IntegrityError:
            return self._error_response(
                request,
                'Já existe uma escala semanal com esse ministério, título e data de início.',
            )

        schedule.set_days_of_week([int(d) for d in days_of_week])
        schedule.save()
        _generate_and_save_week_days(schedule)
        return redirect(f'/admin-panel/schedules/weekly/{schedule.id}/')

    def _error_response(self, request, message):
        current_year = datetime.now().year
        return render(request, 'admin_panel/schedules/monthly/form.html', {
            'error': message,
            'initial_type': 'weekly',
            'ministries': Ministry.objects.filter(deleted__isnull=True).order_by('name'),
            'week_days_choices': WeeklySchedule.WEEK_DAYS_CHOICES,
            'months': MonthlySchedule.MONTH_CHOICES,
            'years': range(current_year, current_year + 2),
        })


class WeeklyScheduleEditView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Edita uma escala semanal existente."""

    def get(self, request, schedule_id):
        schedule = get_object_or_404(WeeklySchedule, id=schedule_id, deleted__isnull=True)
        current_year = datetime.now().year
        return render(request, 'admin_panel/schedules/monthly/form.html', {
            'weekly_schedule': schedule,
            'initial_type': 'weekly',
            'ministries': Ministry.objects.filter(deleted__isnull=True).order_by('name'),
            'week_days_choices': WeeklySchedule.WEEK_DAYS_CHOICES,
            'months': MonthlySchedule.MONTH_CHOICES,
            'years': range(current_year - 1, current_year + 2),
        })

    def post(self, request, schedule_id):
        schedule = get_object_or_404(WeeklySchedule, id=schedule_id, deleted__isnull=True)
        start_date_str = request.POST.get('start_date', '').strip()
        end_date_str = request.POST.get('end_date', '').strip()

        schedule.ministry_id = request.POST.get('ministry')
        schedule.title = request.POST.get('title', '').strip()
        schedule.start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        schedule.end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date() if end_date_str else None
        schedule.use_team_rotation = request.POST.get('use_team_rotation') == 'true'
        schedule.guidelines = request.POST.get('guidelines', '').strip()
        schedule.set_days_of_week([int(d) for d in request.POST.getlist('days_of_week')])
        schedule.save()
        _generate_and_save_week_days(schedule)
        return redirect(f'/admin-panel/schedules/weekly/{schedule.id}/')


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

class WeeklyScheduleDeleteView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Deleta uma escala semanal (POST-only, retorna JSON)."""

    def post(self, request, schedule_id):
        schedule = get_object_or_404(WeeklySchedule, id=schedule_id, deleted__isnull=True)
        schedule.delete()
        return JsonResponse({'success': True})

    def get(self, request, schedule_id):
        return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


# ---------------------------------------------------------------------------
# Day API
# ---------------------------------------------------------------------------

class WeeklyScheduleDayGetView(LoginRequiredMixin, StaffRequiredMixin, View):
    """API: Retorna dados de um dia de escala semanal."""

    def get(self, request, day_id):
        day = get_object_or_404(ScheduleWeekDay, id=day_id, deleted__isnull=True)
        return JsonResponse({
            'success': True,
            'id': day.id,
            'date': day.date.strftime('%Y-%m-%d'),
            'day_of_week': day.day_of_week,
            'description': day.description or '',
            'notes': day.notes or '',
            'team_id': day.team_id,
            'team_name': day.team.name if day.team else '',
            'has_shifts': day.has_shifts,
            'is_cancelled': day.is_cancelled,
            'cancellation_reason': day.cancellation_reason or '',
            'member_ids': list(day.members.values_list('id', flat=True)),
            'morning_member_ids': list(day.members_morning.values_list('id', flat=True)),
            'evening_member_ids': list(day.members_evening.values_list('id', flat=True)),
        })


class WeeklyScheduleDayEditView(LoginRequiredMixin, StaffRequiredMixin, View):
    """API: Edita um dia de escala semanal."""

    def post(self, request, day_id):
        day = get_object_or_404(ScheduleWeekDay, id=day_id, deleted__isnull=True)

        has_shifts = request.POST.get('has_shifts') == 'true'
        member_ids = request.POST.getlist('members')
        morning_ids = request.POST.getlist('members_morning')
        evening_ids = request.POST.getlist('members_evening')
        division_assignments_raw = request.POST.get('division_assignments')

        if division_assignments_raw:
            member_ids, morning_ids, evening_ids = self._resolve_division_members(
                division_assignments_raw, has_shifts, member_ids, morning_ids, evening_ids,
            )

        day.team_id = request.POST.get('team') or None
        day.description = request.POST.get('description') or None
        day.notes = request.POST.get('notes') or None
        day.has_shifts = has_shifts
        day.is_cancelled = request.POST.get('is_cancelled') == 'true'
        day.cancellation_reason = request.POST.get('cancellation_reason') or None
        day.save()

        day.members.set(member_ids)
        day.members_morning.set(morning_ids)
        day.members_evening.set(evening_ids)

        return JsonResponse({'success': True, 'day_id': day.id})

    def _resolve_division_members(self, raw, has_shifts, member_ids, morning_ids, evening_ids):
        """Extrai member IDs a partir de um payload de divisões, se o frontend não os enviou."""
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return member_ids, morning_ids, evening_ids

        if not isinstance(data, dict):
            return member_ids, morning_ids, evening_ids

        if has_shifts:
            if not morning_ids:
                morning_ids = self._collect_ids(data.get('morning', {}))
            if not evening_ids:
                evening_ids = self._collect_ids(data.get('evening', {}))
        else:
            if not member_ids:
                member_ids = self._collect_ids(data)

        return member_ids, morning_ids, evening_ids

    @staticmethod
    def _collect_ids(mapping):
        agg = set()
        for ids in mapping.values():
            if isinstance(ids, list):
                agg.update(str(i) for i in ids)
        return list(agg)


class WeeklyScheduleDayDeleteView(LoginRequiredMixin, StaffRequiredMixin, View):
    """API: Exclui um dia de escala semanal."""

    def post(self, request, day_id):
        day = get_object_or_404(ScheduleWeekDay, id=day_id, deleted__isnull=True)
        day.delete()
        return JsonResponse({'success': True})

    def get(self, request, day_id):
        return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)
