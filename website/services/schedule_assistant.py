"""Transactional planning operations on the canonical MonthlySchedule."""
import datetime
import hashlib
import json
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from safedelete.models import SOFT_DELETE

from website.models import (
    Ministry, Event, MonthlySchedule, ScheduleDay, MediaSubTeam, MediaTeamService,
    ScheduleAssistantState, ScheduleOccurrence, ScheduleOccurrenceTeam,
)
from .schedule_agenda import MONTHS, agenda_occurrences, team_color


class PlanningConflict(Exception):
    pass


def integer(value, label, minimum=1, maximum=32767):
    if isinstance(value, bool):
        raise ValidationError(f'{label}: valor inválido.')
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        raise ValidationError(f'{label}: informe um número inteiro.')
    if not minimum <= parsed <= maximum:
        raise ValidationError(f'{label}: valor fora do intervalo permitido.')
    return parsed


def parse_identifier(value, label):
    return integer(value, label, maximum=9223372036854775807)


def text(value, label, maximum, required=False):
    if not isinstance(value, str) or len(value) > maximum:
        raise ValidationError(f'{label}: informe até {maximum} caracteres.')
    value = value.strip()
    if required and not value:
        raise ValidationError(f'Informe {label.lower()}.')
    return value


def period_from_input(year, month):
    return datetime.date(integer(year, 'Ano', 2020, 2100), integer(month, 'Mês', 1, 12), 1)


def _day(schedule, date):
    existing = ScheduleDay.all_objects.filter(schedule=schedule, date=date).first()
    if existing:
        if existing.deleted:
            existing.undelete(force_policy=SOFT_DELETE)
        return existing
    return ScheduleDay.objects.create(schedule=schedule, date=date)


def _defaults(ministry):
    result = {}
    services = MediaTeamService.objects.filter(
        sub_team__ministry=ministry, sub_team__deleted__isnull=True,
        sub_team__is_active=True,
    ).select_related('sub_team', 'event', 'event_date').prefetch_related('activity_links__activity')
    for service in services:
        if service.regular_service:
            result.setdefault(('regular', service.regular_service), []).append(service)
            continue
        if not service.event_id or service.event.deleted:
            continue
        if service.event_date_id and service.event_date.deleted:
            continue
        key = ('date', service.event_date_id) if service.event_date_id else ('event', service.event_id)
        result.setdefault(key, []).append(service)
        # Legacy recurring agenda defaults also seed the corresponding fixed
        # regular-service slot, preserving existing team configurations.
        if service.event.is_recurring and not service.event_date_id:
            slot = _regular_slot_for(service.event.event_date, service.event.event_time)
            if slot:
                result.setdefault(('regular', slot), []).append(service)
    return result


def _regular_slot_for(date, time):
    if not date or not time:
        return None
    for slot, (weekday, slot_time) in MediaTeamService.REGULAR_SERVICE_DETAILS.items():
        if date.weekday() == weekday and time == slot_time:
            return slot
    return None


def _is_standard_regular_agenda_row(row):
    if not row['recurring_event']:
        return False
    return bool(_regular_slot_for(
        datetime.date.fromisoformat(row['date']), datetime.time.fromisoformat(row['time']),
    ))


def _import(state, row, defaults, default_key=None):
    existing = ScheduleOccurrence.all_objects.filter(
        schedule_day__schedule=state.schedule, source_key=row['key'],
    ).first()
    if existing:
        if existing.deleted:
            existing.undelete()
        return existing
    occurrence = ScheduleOccurrence.objects.create(
        schedule_day=_day(state.schedule, datetime.date.fromisoformat(row['date'])),
        source_event_id=row['event_id'], source_event_date_id=row['event_date_id'],
        source_key=row['key'], source_label=row['label'], name=row['name'], start_time=row['time'],
    )
    configured = {
        service.sub_team_id: service
        for service in defaults.get(default_key or ('event', row['event_id']), [])
        if service.is_active
    }
    if not default_key and row['event_date_id']:
        for service in defaults.get(('date', row['event_date_id']), []):
            if service.is_active:
                configured[service.sub_team_id] = service
            else:
                configured.pop(service.sub_team_id, None)
    for service in configured.values():
        activities = [
            {'id': link.activity_id, 'name': link.activity.name, 'order': link.activity.sort_order}
            for link in service.activity_links.all()
            if link.activity.is_active and not link.activity.deleted
        ]
        ScheduleOccurrenceTeam.objects.create(
            occurrence=occurrence, sub_team=service.sub_team,
            team_name=service.sub_team.name, color=team_color(service.sub_team_id),
            member_count=service.member_count,
            activities=sorted(activities, key=lambda activity: (activity['order'], activity['name'])),
        )
    return occurrence


def _regular_row(slot, date):
    _weekday, time = MediaTeamService.REGULAR_SERVICE_DETAILS[slot]
    names = {
        'sunday_09': 'Culto de Domingo',
        'sunday_19': 'Culto de Domingo',
        'wednesday_20': 'Culto de Quarta-feira',
    }
    name = names[slot]
    return {
        'key': f'regular:{slot}@{date.isoformat()}', 'event_id': None, 'event_date_id': None,
        'date': date.isoformat(), 'time': time.strftime('%H:%M'), 'name': name,
        'label': f'{name} · {date:%d/%m/%Y} · {time:%H:%M}', 'recurring': True, 'recurring_event': True,
    }


def _ensure_regular_occurrences(state, defaults):
    """Backfill only missing fixed services on an already-open draft.

    A draft is a snapshot, so its existing occurrences and local team edits are
    never recalculated here. This narrowly handles drafts created before the
    three standard worship services existed. A soft-deleted occurrence is also
    considered present: removing it from the planning must remain intentional.
    """
    existing_keys = set(ScheduleOccurrence.all_objects.filter(
        schedule_day__schedule=state.schedule,
        source_key__startswith='regular:',
    ).values_list('source_key', flat=True))
    for slot, _label in MediaTeamService.REGULAR_SERVICE_CHOICES:
        key = ('regular', slot)
        for date in MediaTeamService.regular_dates(slot, state.period):
            row = _regular_row(slot, date)
            if row['key'] not in existing_keys:
                _import(state, row, defaults, key)


@transaction.atomic
def ensure_regular_occurrences(state):
    """Upgrade a legacy draft when it is opened directly by URL or API."""
    state = ScheduleAssistantState.objects.select_for_update().select_related('schedule').get(pk=state.pk)
    _ensure_regular_occurrences(state, _defaults(state.ministry))
    from .schedule_availability import reconcile_review
    reconcile_review(state)
    return state


@transaction.atomic
def start_draft(ministry, period, user):
    Ministry.objects.select_for_update().get(pk=ministry.pk)
    existing = ScheduleAssistantState.objects.filter(
        ministry=ministry, period=period, status='draft', schedule__deleted__isnull=True,
    ).first()
    if existing:
        # Drafts created before the fixed regular-service model was introduced
        # need the missing occurrences, but must retain every existing choice.
        _ensure_regular_occurrences(existing, _defaults(ministry))
        return existing, False
    schedule = MonthlySchedule.objects.create(
        ministry=ministry, schedule_type='monthly', month=period.month, year=period.year,
        title=f'Mídia — {MONTHS[period.month]} de {period.year}', is_published=False,
    )
    state = ScheduleAssistantState.objects.create(
        schedule=schedule, ministry=ministry, period=period, created_by=user, updated_by=user,
    )
    sources, _ = agenda_occurrences(period)
    defaults = _defaults(ministry)
    # The three regular worship services are always present. Teams configured for
    # the matching slot are automatically preselected on each occurrence.
    _ensure_regular_occurrences(state, defaults)
    # Other events from the agenda remain visible for one-off team planning.
    for row in sources:
        if _is_standard_regular_agenda_row(row):
            continue
        _import(state, row, defaults)
    from .schedule_availability import reconcile_review
    reconcile_review(state)
    return state, True


def version(state):
    # Include administrative edits to canonical metadata/days, not just wizard writes.
    schedule = state.schedule
    days = list(ScheduleDay.all_objects.filter(schedule=schedule).order_by('pk').values_list(
        'pk', 'date', 'update_at', 'deleted',
    ))
    from website.models import DivisionMember
    designations = list(DivisionMember.all_objects.filter(schedule_day__schedule=schedule).order_by('pk').values_list(
        'pk', 'member_id', 'occurrence_team_id', 'is_locked', 'update_at', 'deleted'))
    value = json.dumps([designations, state.revision, str(state.update_at), str(schedule.update_at), schedule.ministry_id,
                        schedule.month, schedule.year, str(schedule.deleted), days], default=str)
    return hashlib.sha256(value.encode()).hexdigest()


def catalog(state):
    return [
        {'id': team.pk, 'name': team.name, 'color': team_color(team.pk),
         'activities': [{'id': activity.pk, 'name': activity.name, 'order': activity.sort_order}
                        for activity in team.activities.all() if activity.is_active and not activity.deleted]}
        for team in MediaSubTeam.objects.filter(ministry=state.ministry, is_active=True).prefetch_related('activities')
    ]


def serialize(state):
    from .schedule_availability import serialize_availability
    availability = serialize_availability(state)
    from .schedule_generation import serialize_generation
    generation = serialize_generation(state)
    review_ids = set(availability['review_occurrence_ids'])
    sources, warnings = agenda_occurrences(state.period)
    sources = [row for row in sources if not _is_standard_regular_agenda_row(row)]
    occurrences = ScheduleOccurrence.objects.filter(schedule_day__schedule=state.schedule).select_related(
        'schedule_day',
    ).prefetch_related('team_plans')
    return {
        'id': state.pk, 'schedule_id': state.schedule_id, 'year': state.period.year,
        'month': state.period.month, 'period_label': f'{MONTHS[state.period.month]} de {state.period.year}',
        'current_step': state.current_step, 'revision': state.revision, 'version': version(state),
        'updated_at': timezone.localtime(state.update_at).isoformat(),
        'availability_review': state.availability_review, 'availability': availability, 'generation': generation,
        'sources': sources, 'warnings': warnings, 'teams': catalog(state),
        'events': [{'id': event.pk, 'label': event.title} for event in Event.objects.order_by('title', 'pk')],
        'occurrences': [
            {'id': occurrence.pk, 'date': occurrence.schedule_day.date.isoformat(),
             'time': occurrence.start_time.strftime('%H:%M'), 'name': occurrence.name,
             'notes': occurrence.notes, 'source_key': occurrence.source_key,
             'source_label': occurrence.source_label,
             'availability_review_required': occurrence.availability_review_required or occurrence.pk in review_ids,
             'teams': [{'id': team.pk, 'team_id': team.sub_team_id, 'name': team.team_name,
                        'color': team.color, 'member_count': team.member_count, 'minimum_mentors': team.minimum_mentors,
                        'activities': team.activities, 'notes': team.notes}
                       for team in occurrence.team_plans.all()]}
            for occurrence in occurrences
        ],
    }


def _date_time(state, data):
    try:
        date = datetime.date.fromisoformat(data.get('date', ''))
        time = datetime.time.fromisoformat(data.get('time', ''))
    except (ValueError, TypeError):
        raise ValidationError('Informe data e horário válidos.')
    if (date.year, date.month) != (state.period.year, state.period.month):
        raise ValidationError('A ocorrência deve pertencer ao mês do planejamento.')
    if time.tzinfo or time.second or time.microsecond:
        raise ValidationError('Informe o horário local com horas e minutos.')
    return date, time


def _occurrence(state, identifier):
    return get_object_or_404(ScheduleOccurrence, pk=parse_identifier(identifier, 'Ocorrência'),
                            schedule_day__schedule=state.schedule)


def _review(state, occurrence, reason):
    if state.availability_revision is None:
        return
    occurrence.availability_review_required = True
    occurrence.save(update_fields=['availability_review_required', 'update_at'])
    change = {'occurrence_id': occurrence.pk, 'name': occurrence.name, 'reason': reason}
    if change not in state.availability_review:
        state.availability_review = [*state.availability_review, change]


def _set_teams(state, occurrence, items, available):
    if not isinstance(items, list) or len(items) > 100:
        raise ValidationError('Informe uma lista válida de equipes.')
    current = {team.sub_team_id: team for team in occurrence.team_plans.all()}
    normalized, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            raise ValidationError('Equipe inválida.')
        team_id = parse_identifier(item.get('team_id'), 'Equipe')
        if team_id in seen:
            raise ValidationError('Uma equipe só pode aparecer uma vez em cada culto.')
        seen.add(team_id)
        prior = current.get(team_id)
        entry = available.get(team_id)
        if not entry and not prior:
            raise ValidationError('Selecione uma equipe ativa da Mídia.')
        activities = {activity['id']: activity for activity in (entry or {}).get('activities', [])}
        if prior:
            activities.update({activity['id']: activity for activity in prior.activities})
        ids = item.get('activity_ids', [])
        if not isinstance(ids, list) or len(ids) > 100:
            raise ValidationError('Atividades inválidas.')
        ids = list(dict.fromkeys(parse_identifier(value, 'Atividade') for value in ids))
        if any(value not in activities for value in ids):
            raise ValidationError('Selecione atividades pertencentes à equipe.')
        count = integer(item.get('member_count'), 'Quantidade de integrantes')
        minimum = item.get('minimum_mentors', prior.minimum_mentors if prior else None)
        minimum = integer(minimum, 'Mínimo de integrantes aptos a acompanhar', 1, count) if minimum not in (None, '') else None
        if count == 2:
            minimum = None  # A pair always requires one mentor, independently of configuration.
        normalized.append((team_id, prior, {
            'team_name': prior.team_name if prior else entry['name'],
            'color': prior.color if prior else entry['color'],
            'member_count': count, 'minimum_mentors': minimum,
            'activities': sorted([activities[value] for value in ids], key=lambda activity: (activity['order'], activity['name'])),
            'notes': text(item.get('notes', ''), 'Observações da equipe', 5000),
        }))
    changed = False
    for team_id, prior in current.items():
        if team_id not in seen:
            prior.delete()
            changed = True
    for team_id, prior, values in normalized:
        if prior:
            if any(getattr(prior, key) != value for key, value in values.items()):
                for key, value in values.items():
                    setattr(prior, key, value)
                prior.save()
                changed = True
        else:
            ScheduleOccurrenceTeam.objects.create(occurrence=occurrence, sub_team_id=team_id, **values)
            changed = True
    return changed


@transaction.atomic
def mutate(state_id, ministry, user, payload):
    # Always lock the canonical schedule first, then its workflow state.
    unlocked = get_object_or_404(ScheduleAssistantState, pk=state_id, ministry=ministry,
                                schedule__ministry=ministry, status='draft')
    schedule = get_object_or_404(MonthlySchedule.objects.select_for_update(), pk=unlocked.schedule_id)
    state = get_object_or_404(ScheduleAssistantState.objects.select_for_update(), pk=state_id, status='draft')
    state.schedule = schedule
    if payload.get('version') != version(state):
        raise PlanningConflict('Este rascunho foi alterado em outra sessão. Recarregue para revisar antes de salvar.')
    if (schedule.year, schedule.month) != (state.period.year, state.period.month) or schedule.ministry_id != ministry.pk:
        raise ValidationError('O período da escala foi alterado na Administração. Revise a escala antes de continuar.')
    action = payload.get('action')
    if action == 'discard':
        if payload.get('confirmed') is not True:
            raise ValidationError('Confirme o descarte do rascunho.')
        state.status = 'discarded'
        state.updated_by = user
        state.save()
        schedule.delete()
        return None
    if action == 'generate':
        from .schedule_generation import generate
        generate(state, payload)
    elif action == 'set_assignments':
        from .schedule_generation import edit_assignments
        edit_assignments(state, payload)
    elif action == 'remove_obsolete_assignment':
        from .schedule_generation import remove_obsolete
        remove_obsolete(state, payload)
    elif action == 'save_availability':
        from .schedule_availability import save_availability
        save_availability(state, user, payload)
    elif action == 'clear_availability':
        from .schedule_availability import clear_availability
        clear_availability(state, payload)
    elif action == 'navigate':
        state.current_step = integer(payload.get('step'), 'Etapa', 1, 3)
    elif action == 'add_source':
        sources, _ = agenda_occurrences(state.period)
        row = next((item for item in sources if item['key'] == payload.get('source_key')), None)
        if not row:
            raise ValidationError('Esta ocorrência não está disponível na agenda deste mês.')
        exists = ScheduleOccurrence.objects.filter(schedule_day__schedule=schedule, source_key=row['key']).exists()
        occurrence = _import(state, row, _defaults(ministry))
        if not exists:
            _review(state, occurrence, 'Ocorrência adicionada ou restaurada.')
    elif action == 'add_manual':
        date, time = _date_time(state, payload)
        name = text(payload.get('name', ''), 'Nome', 255, True)
        notes = text(payload.get('notes', ''), 'Observações', 5000)
        occurrence = ScheduleOccurrence.objects.filter(
            schedule_day__schedule=schedule, schedule_day__date=date, start_time=time, name=name,
        ).first()
        if occurrence is None:
            event_id = payload.get('event_id')
            if event_id:
                event = get_object_or_404(Event, pk=parse_identifier(event_id, 'Evento'))
                occurrence = _import(state, {
                    'key': f'manual:{uuid.uuid4()}', 'event_id': event.pk, 'event_date_id': None,
                    'date': date.isoformat(), 'time': time.isoformat(), 'name': name,
                    'label': f'{event.title} · Ocorrência adicional',
                }, _defaults(ministry))
                occurrence.notes = notes
                occurrence.save(update_fields=['notes', 'update_at'])
            else:
                occurrence = ScheduleOccurrence.objects.create(
                    schedule_day=_day(schedule, date), source_key=f'manual:{uuid.uuid4()}',
                    name=name, start_time=time, notes=notes,
                )
            _review(state, occurrence, 'Ocorrência adicionada.')
    elif action == 'remove_occurrence':
        occurrence = _occurrence(state, payload.get('occurrence_id'))
        _review(state, occurrence, 'Ocorrência retirada do planejamento.')
        occurrence.delete()
    elif action == 'save_occurrence':
        occurrence = _occurrence(state, payload.get('occurrence_id'))
        date, time = _date_time(state, payload)
        name = text(payload.get('name', ''), 'Nome', 255, True)
        notes = text(payload.get('notes', ''), 'Observações', 5000)
        timing_changed = (occurrence.schedule_day.date != date or occurrence.start_time != time
                          or occurrence.name != name or occurrence.notes != notes)
        occurrence.schedule_day = _day(schedule, date)
        occurrence.start_time, occurrence.name, occurrence.notes = time, name, notes
        occurrence.save()
        extra_ids = payload.get('apply_to', [])
        if not isinstance(extra_ids, list) or len(extra_ids) > 100:
            raise ValidationError('Selecione até 100 ocorrências para edição em conjunto.')
        targets = [occurrence]
        for identifier in dict.fromkeys(parse_identifier(value, 'Ocorrência') for value in extra_ids):
            if identifier != occurrence.pk:
                targets.append(_occurrence(state, identifier))
        available = {team['id']: team for team in catalog(state)}
        for target in targets:
            changed = _set_teams(state, target, payload.get('teams'), available)
            if changed or (target.pk == occurrence.pk and timing_changed):
                _review(state, target, 'Dados da ocorrência ou composição da equipe alterados.')
    else:
        raise ValidationError('Ação de planejamento inválida.')
    from .schedule_availability import reconcile_review
    reconcile_review(state)
    state.revision += 1
    state.updated_by = user
    state.save()
    return serialize(state)
