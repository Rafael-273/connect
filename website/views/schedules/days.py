from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views import View

from ..mixins import StaffRequiredMixin
from ...models import MonthlySchedule, ScheduleDay


class ScheduleDayCreateView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Adiciona um dia à escala (POST-only, retorna JSON)."""

    def post(self, request, schedule_id):
        from safedelete.models import HARD_DELETE

        schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)

        date_str = request.POST.get('date')
        date = datetime.strptime(date_str, '%Y-%m-%d').date()

        team_id = request.POST.get('team')
        member_ids = request.POST.getlist('members')
        description = request.POST.get('description') or None
        notes = request.POST.get('notes') or None

        override_conflict = request.POST.get('override_conflict') == 'true'

        # Validação de conflito de escala no servidor
        if member_ids and not override_conflict:
            int_member_ids = [int(mid) for mid in member_ids if mid]
            if int_member_ids:
                conflicting_days = ScheduleDay.objects.filter(
                    date=date,
                    members__id__in=int_member_ids,
                    is_cancelled=False,
                    deleted__isnull=True,
                    schedule__deleted__isnull=True,
                ).exclude(schedule_id=schedule.id).select_related('schedule', 'schedule__ministry')

                if conflicting_days.exists():
                    conflicts = []
                    seen = set()
                    for cd in conflicting_days:
                        for member in cd.members.filter(id__in=int_member_ids):
                            key = (member.id, cd.schedule.id)
                            if key not in seen:
                                seen.add(key)
                                conflicts.append(
                                    f'{member.name} → {cd.schedule.ministry.name} - {cd.schedule.title}'
                                )
                    if conflicts:
                        return JsonResponse({
                            'success': False,
                            'conflict': True,
                            'error': 'Conflito de escala detectado',
                            'conflict_details': conflicts,
                        }, status=409)

        # Verifica se já existe um dia ATIVO para esta data nesta escala
        existing_active_day = ScheduleDay.objects.filter(
            schedule=schedule,
            date=date,
        ).first()

        if existing_active_day:
            return JsonResponse({
                'success': False,
                'error': (
                    f'Já existe um dia escalado para {date.strftime("%d/%m/%Y")} '
                    f'nesta escala. Edite o dia existente ao invés de criar um novo.'
                ),
            }, status=400)

        # Clear any soft-deleted day for this date to avoid constraint conflicts
        soft_deleted_day = ScheduleDay.all_objects.filter(
            schedule=schedule, date=date, deleted__isnull=False,
        ).first()
        if soft_deleted_day:
            soft_deleted_day.delete(force_policy=HARD_DELETE)

        has_shifts = request.POST.get('has_shifts') == 'true'
        morning_ids = request.POST.getlist('members_morning')
        evening_ids = request.POST.getlist('members_evening')

        try:
            day = ScheduleDay.objects.create(
                schedule=schedule,
                date=date,
                team_id=team_id if team_id else None,
                description=description,
                notes=notes,
                has_shifts=has_shifts,
            )

            if member_ids:
                day.members.set(member_ids)
            if morning_ids:
                day.members_morning.set(morning_ids)
            if evening_ids:
                day.members_evening.set(evening_ids)

            return JsonResponse({
                'success': True,
                'day_id': day.id,
                'date': day.date.strftime('%d/%m/%Y'),
            })
        except IntegrityError as e:
            return JsonResponse({
                'success': False,
                'error': (
                    f'Já existe um dia escalado para {date.strftime("%d/%m/%Y")} '
                    f'nesta escala. Erro: {str(e)}'
                ),
            }, status=400)

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


class ScheduleDayEditView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Edita um dia da escala (POST-only, retorna JSON)."""

    def post(self, request, day_id):
        day = get_object_or_404(ScheduleDay, id=day_id, deleted__isnull=True)

        team_id = request.POST.get('team')
        member_ids = request.POST.getlist('members')
        description = request.POST.get('description') or None
        notes = request.POST.get('notes') or None

        override_conflict = request.POST.get('override_conflict') == 'true'

        # Validação de conflito de escala no servidor
        if member_ids and not override_conflict:
            int_member_ids = [int(mid) for mid in member_ids if mid]
            if int_member_ids:
                conflicting_days = ScheduleDay.objects.filter(
                    date=day.date,
                    members__id__in=int_member_ids,
                    is_cancelled=False,
                    deleted__isnull=True,
                    schedule__deleted__isnull=True,
                ).exclude(schedule_id=day.schedule_id).select_related('schedule', 'schedule__ministry')

                if conflicting_days.exists():
                    conflicts = []
                    seen = set()
                    for cd in conflicting_days:
                        for member in cd.members.filter(id__in=int_member_ids):
                            key = (member.id, cd.schedule.id)
                            if key not in seen:
                                seen.add(key)
                                conflicts.append(
                                    f'{member.name} → {cd.schedule.ministry.name} - {cd.schedule.title}'
                                )
                    if conflicts:
                        return JsonResponse({
                            'success': False,
                            'conflict': True,
                            'error': 'Conflito de escala detectado',
                            'conflict_details': conflicts,
                        }, status=409)

        has_shifts = request.POST.get('has_shifts') == 'true'
        morning_ids = request.POST.getlist('members_morning')
        evening_ids = request.POST.getlist('members_evening')

        day.team_id = team_id if team_id else None
        day.description = description
        day.notes = notes
        day.has_shifts = has_shifts
        day.save()

        day.members.set(member_ids)
        day.members_morning.set(morning_ids)
        day.members_evening.set(evening_ids)
        return JsonResponse({'success': True})

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


class ScheduleDayGetView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Retorna os dados de um dia da escala (GET-only, retorna JSON)."""

    def get(self, request, day_id):
        day = get_object_or_404(ScheduleDay, id=day_id, deleted__isnull=True)
        return JsonResponse({
            'success': True,
            'date': day.date.strftime('%Y-%m-%d'),
            'description': day.description or '',
            'notes': day.notes or '',
            'team_id': day.team_id,
            'team_name': day.team.name if day.team else '',
            'member_ids': list(day.members.values_list('id', flat=True)),
            'member_names': list(day.members.values_list('name', flat=True)),
            'has_shifts': day.has_shifts,
            'morning_member_ids': list(day.members_morning.values_list('id', flat=True)),
            'morning_member_names': list(day.members_morning.values_list('name', flat=True)),
            'evening_member_ids': list(day.members_evening.values_list('id', flat=True)),
            'evening_member_names': list(day.members_evening.values_list('name', flat=True)),
        })

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


class ScheduleDayDeleteView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Deleta um dia da escala (POST-only, retorna JSON)."""

    def post(self, request, day_id):
        day = get_object_or_404(ScheduleDay, id=day_id, deleted__isnull=True)
        day.delete()
        return JsonResponse({'success': True})

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


class ScheduleDayToggleCancelView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Cancela/descancela um dia da escala (POST-only, retorna JSON)."""

    def post(self, request, day_id):
        day = get_object_or_404(ScheduleDay, id=day_id, deleted__isnull=True)

        action = request.POST.get('action')
        if action == 'cancel':
            day.is_cancelled = True
            day.cancellation_reason = request.POST.get('reason')
        elif action == 'uncancel':
            day.is_cancelled = False
            day.cancellation_reason = None

        day.save()
        return JsonResponse({'success': True, 'is_cancelled': day.is_cancelled})

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


@login_required
def check_schedule_conflict_view(request):
    """Verifica se membros já estão escalados em outra escala na mesma data."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)

    date_str = request.POST.get('date')
    member_ids = request.POST.getlist('members')
    schedule_id = request.POST.get('schedule_id')

    if not date_str or not member_ids:
        return JsonResponse({'has_conflict': False, 'conflicts': []})

    try:
        date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except (ValueError, TypeError):
        return JsonResponse({'has_conflict': False, 'conflicts': []})

    member_ids = [int(mid) for mid in member_ids if mid]
    if not member_ids:
        return JsonResponse({'has_conflict': False, 'conflicts': []})

    conflicting_days = ScheduleDay.objects.filter(
        date=date,
        members__id__in=member_ids,
        is_cancelled=False,
        deleted__isnull=True,
        schedule__deleted__isnull=True,
    ).select_related('schedule', 'schedule__ministry')

    if schedule_id:
        conflicting_days = conflicting_days.exclude(schedule_id=int(schedule_id))

    conflicts = []
    seen = set()
    for day in conflicting_days:
        for member in day.members.filter(id__in=member_ids):
            key = (member.id, day.schedule.id)
            if key not in seen:
                seen.add(key)
                conflicts.append({
                    'member_id': member.id,
                    'member_name': member.name,
                    'schedule_title': day.schedule.title,
                    'ministry_name': day.schedule.ministry.name,
                    'date': day.date.strftime('%d/%m/%Y'),
                })

    return JsonResponse({
        'has_conflict': len(conflicts) > 0,
        'conflicts': conflicts,
    })
