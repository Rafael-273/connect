"""Declarations and coverage diagnostics. This module never assigns a volunteer."""
from collections import defaultdict

from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone

from website.models import (
    MediaSubTeam, MediaSubTeamMembership, ScheduleAvailability, ScheduleAvailabilityOccurrence,
    ScheduleAvailabilityCoverage, ScheduleOccurrence, ScheduleOccurrenceTeam,
)

AVAILABLE, UNAVAILABLE, UNKNOWN = 'available', 'unavailable', 'unknown'


def active_memberships(state):
    return MediaSubTeamMembership.objects.filter(
        is_active=True, sub_team__ministry=state.ministry, sub_team__is_active=True,
        sub_team__deleted__isnull=True, sub_team__ministry__is_active=True, member__is_active=True, member__deleted__isnull=True,
        member__ministry_memberships__ministry=state.ministry,
        member__ministry_memberships__is_active=True,
        member__ministry_memberships__deleted__isnull=True,
    ).select_related('member', 'sub_team').distinct().order_by('member__name', 'pk')


def planned_teams(state):
    return ScheduleOccurrenceTeam.objects.filter(
        occurrence__schedule_day__schedule=state.schedule,
        occurrence__deleted__isnull=True, occurrence__schedule_day__deleted__isnull=True,
        occurrence__schedule_day__is_cancelled=False,
    ).select_related('occurrence__schedule_day', 'sub_team')


def _declared_available(mode, selected_ids, occurrence_id):
    return mode == 'all' or (mode == 'only' and occurrence_id in selected_ids) or (mode == 'except' and occurrence_id not in selected_ids)


def confirmation_status(confirmation, plan, declaration=None):
    if confirmation is None:
        return UNKNOWN
    occurrence = plan.occurrence
    declaration = declaration or confirmation.availability
    if (declaration.deleted or declaration.schedule_id != occurrence.schedule_day.schedule_id or
            declaration.sub_team_id != plan.sub_team_id or declaration.mode not in dict(ScheduleAvailability.MODE_CHOICES)):
        return UNKNOWN
    if confirmation.is_available != _declared_available(declaration.mode, declaration.selected_occurrence_ids, occurrence.pk):
        return UNKNOWN
    if (confirmation.team_plan_id != plan.pk or
            confirmation.occurrence_date != occurrence.schedule_day.date or
            confirmation.start_time != occurrence.start_time):
        return UNKNOWN
    return AVAILABLE if confirmation.is_available else UNAVAILABLE


def validate_daily_service_limit(occurrence, assigned_occurrences):
    """Reusable assignment guard: multiple teams in one cult are not two cults."""
    for assigned in assigned_occurrences:
        if assigned.pk != occurrence.pk and assigned.schedule_day.date == occurrence.schedule_day.date:
            raise ValidationError('Uma pessoa não pode servir em dois cultos no mesmo dia, mesmo em equipes diferentes.')


def available_candidates(state, plan, assigned_occurrences_by_member=None):
    """Contract for the future generator: unknown is never eligible; enforce daily limit."""
    if plan.occurrence.schedule_day.schedule_id != state.schedule_id:
        raise ValidationError('A participação não pertence a esta escala.')
    if (plan.deleted or plan.occurrence.deleted or plan.occurrence.schedule_day.deleted or
            plan.occurrence.schedule_day.is_cancelled):
        return []
    memberships = list(active_memberships(state).filter(sub_team_id=plan.sub_team_id))
    confirmations = {
        row.availability.member_id: row
        for row in ScheduleAvailabilityOccurrence.objects.filter(
            availability__schedule=state.schedule, availability__sub_team_id=plan.sub_team_id,
            availability__deleted__isnull=True, occurrence_id=plan.occurrence_id,
        ).select_related('availability')
    }
    candidates = []
    for membership in memberships:
        if confirmation_status(confirmations.get(membership.member_id), plan) != AVAILABLE:
            continue
        try:
            validate_daily_service_limit(plan.occurrence, (assigned_occurrences_by_member or {}).get(membership.member_id, []))
        except ValidationError:
            continue
        candidates.append(membership.member)
    return candidates


def _context(state):
    memberships = list(active_memberships(state))
    plans = list(planned_teams(state))
    declarations = list(ScheduleAvailability.objects.filter(schedule=state.schedule).prefetch_related('confirmations'))
    records = {(row.sub_team_id, row.member_id): row for row in declarations}
    confirmations = {
        (row.sub_team_id, row.member_id): {item.occurrence_id: item for item in row.confirmations.all()}
        for row in declarations
    }
    return memberships, plans, records, confirmations


def serialize_availability(state):
    memberships, plans, records, confirmations = _context(state)
    plans_by_team, memberships_by_team, teams_by_member = defaultdict(list), defaultdict(list), defaultdict(list)
    for plan in plans:
        plans_by_team[plan.sub_team_id].append(plan)
    for membership in memberships:
        memberships_by_team[membership.sub_team_id].append(membership)
        teams_by_member[membership.member_id].append({'id': membership.sub_team_id, 'name': membership.sub_team.name})
    roster = []
    complete_members, pending_members, recorded_members, relevant_members = set(), set(), set(), set()
    review_ids = set()
    for membership in memberships:
        key = (membership.sub_team_id, membership.member_id)
        declaration = records.get(key)
        known = confirmations.get(key, {})
        cells = []
        for plan in plans_by_team[membership.sub_team_id]:
            occurrence = plan.occurrence
            status = confirmation_status(known.get(occurrence.pk), plan, declaration)
            cells.append({'occurrence_id': occurrence.pk, 'status': status})
            if declaration and status == UNKNOWN:
                review_ids.add(occurrence.pk)
        if cells:
            relevant_members.add(membership.member_id)
            if declaration:
                recorded_members.add(membership.member_id)
            if any(cell['status'] == UNKNOWN for cell in cells):
                pending_members.add(membership.member_id)
        current_ids = {cell['occurrence_id'] for cell in cells}
        removed = []
        if declaration:
            for identifier in declaration.selected_occurrence_ids:
                confirmation = known.get(identifier)
                if identifier not in current_ids and confirmation:
                    removed.append({'id': identifier, 'date': confirmation.occurrence_date.isoformat(),
                                    'time': confirmation.start_time.strftime('%H:%M'), 'name': confirmation.occurrence_name})
        roster.append({
            'membership_id': membership.pk, 'member_id': membership.member_id, 'name': membership.member.name,
            'team_id': membership.sub_team_id, 'experience_level': membership.experience_level,
            'experience_label': membership.get_experience_level_display(),
            'other_teams': [team for team in teams_by_member[membership.member_id] if team['id'] != membership.sub_team_id],
            'record': None if not declaration else {
                'id': declaration.pk, 'mode': declaration.mode,
                'selected_occurrence_ids': declaration.selected_occurrence_ids,
                'notes': declaration.notes, 'recorded_at': declaration.recorded_at.isoformat(),
            },
            'occurrences': cells, 'removed_selections': removed,
            'needs_review': bool(declaration and any(cell['status'] == UNKNOWN for cell in cells)),
        })
    complete_members = relevant_members - pending_members
    rows, warnings = [], []
    daily = defaultdict(lambda: {'occurrences': {}, 'available': set(), 'mentors': set(), 'opportunities': defaultdict(set)})
    for plan in plans:
        occurrence = plan.occurrence
        counts = {AVAILABLE: 0, UNAVAILABLE: 0, UNKNOWN: 0}
        available, mentors, unclassified = set(), set(), set()
        for membership in memberships_by_team[plan.sub_team_id]:
            key = (plan.sub_team_id, membership.member_id)
            status = confirmation_status(confirmations.get(key, {}).get(occurrence.pk), plan, records.get(key))
            counts[status] += 1
            if status == AVAILABLE:
                available.add(membership.member_id)
                if membership.experience_level == 'mentor':
                    mentors.add(membership.member_id)
                elif membership.experience_level == 'pending':
                    unclassified.add(membership.member_id)
        shortage = max(0, plan.member_count - len(available))
        mentor_shortage = not mentors
        date = occurrence.schedule_day.date.isoformat()
        row = {'team_plan_id': plan.pk, 'occurrence_id': occurrence.pk, 'name': occurrence.name,
               'date': date, 'time': occurrence.start_time.strftime('%H:%M'),
               'team_id': plan.sub_team_id, 'team_name': plan.team_name, 'required': plan.member_count,
               'available': counts[AVAILABLE], 'unavailable': counts[UNAVAILABLE], 'unknown': counts[UNKNOWN],
               'mentors_available': len(mentors), 'pending_experience_available': len(unclassified),
               'shortage': shortage, 'mentor_shortage': mentor_shortage}
        rows.append(row)
        if shortage:
            warnings.append(f'{date[8:10]}/{date[5:7]} · {row["time"]} · {plan.team_name}: faltam {shortage} integrantes com disponibilidade confirmada.')
        if mentor_shortage:
            warnings.append(f'{date[8:10]}/{date[5:7]} · {row["time"]} · {plan.team_name}: nenhuma pessoa apta a acompanhar com disponibilidade confirmada.')
        if unclassified:
            warnings.append(f'{date[8:10]}/{date[5:7]} · {row["time"]} · {plan.team_name}: {len(unclassified)} pessoas disponíveis ainda precisam de classificação de experiência.')
        capacity = daily[date]
        capacity['occurrences'][occurrence.pk] = max(plan.member_count, capacity['occurrences'].get(occurrence.pk, 0))
        capacity['available'].update(available)
        capacity['mentors'].update(mentors)
        for member_id in available:
            capacity['opportunities'][member_id].add(occurrence.pk)
    daily_rows = []
    for date, capacity in sorted(daily.items()):
        # Necessary lower bounds, not a promise of feasibility or a provisional allocation.
        minimum = sum(capacity['occurrences'].values())
        minimum_mentors = len(capacity['occurrences'])
        shortage = max(0, minimum - len(capacity['available']))
        mentor_shortage = max(0, minimum_mentors - len(capacity['mentors']))
        overlap = sum(len(opportunities) > 1 for opportunities in capacity['opportunities'].values())
        daily_rows.append({'date': date, 'minimum_unique_people': minimum,
                           'unique_available': len(capacity['available']), 'shortage': shortage,
                           'minimum_unique_mentors': minimum_mentors, 'unique_mentors': len(capacity['mentors']),
                           'mentor_shortage': mentor_shortage, 'members_with_multiple_cults': overlap})
        if len(capacity['occurrences']) > 1 and (shortage or mentor_shortage):
            warnings.append(f'{date[8:10]}/{date[5:7]}: capacidade diária insuficiente considerando no máximo um culto por pessoa ({len(capacity["available"])} pessoas e {len(capacity["mentors"])} aptas a acompanhar).')
    return {
        'roster': roster,
        'summary': {'total_members': len(relevant_members), 'recorded_members': len(recorded_members),
                    'informed_members': len(complete_members), 'pending_members': len(pending_members),
                    'rows': sorted(rows, key=lambda row: (row['date'], row['time'], row['team_name'])),
                    'daily': daily_rows, 'warnings': warnings},
        'review_occurrence_ids': sorted(review_ids),
    }


def save_availability(state, user, payload):
    """Called inside the assistant's canonical schedule/state locks and transaction."""
    from .schedule_assistant import parse_identifier, text
    team_id = parse_identifier(payload.get('team_id'), 'Equipe')
    member_id = parse_identifier(payload.get('member_id'), 'Integrante')
    get_object_or_404(active_memberships(state), sub_team_id=team_id, member_id=member_id)
    mode = payload.get('mode')
    if mode not in dict(ScheduleAvailability.MODE_CHOICES):
        raise ValidationError('Selecione um modo de disponibilidade.')
    selection = payload.get('selected_occurrence_ids')
    if not isinstance(selection, list) or len(selection) > 1000:
        raise ValidationError('Selecione uma lista válida de ocorrências.')
    selection = list(dict.fromkeys(parse_identifier(value, 'Ocorrência') for value in selection))
    if mode == 'all' and selection:
        raise ValidationError('O modo todas as datas não deve conter exceções.')
    plans = list(planned_teams(state).filter(sub_team_id=team_id))
    if not plans:
        raise ValidationError('Inclua esta equipe em pelo menos uma ocorrência do planejamento antes de registrar disponibilidades.')
    previous = ScheduleAvailability.all_objects.filter(schedule=state.schedule, sub_team_id=team_id, member_id=member_id)
    record = previous.filter(deleted__isnull=True).first() or previous.order_by('-pk').first()
    valid_ids = {plan.occurrence_id for plan in plans}
    if record and not record.deleted and record.mode == mode:
        # Retain explicit exceptions to removed occurrences; never accept arbitrary foreign IDs.
        valid_ids.update(record.confirmations.filter(occurrence_id__in=record.selected_occurrence_ids).values_list('occurrence_id', flat=True))
    if any(identifier not in valid_ids for identifier in selection):
        raise ValidationError('As ocorrências selecionadas devem pertencer à equipe neste planejamento mensal.')
    if record and not record.deleted:
        confirmations = {row.occurrence_id: row for row in record.confirmations.all()}
        if any(confirmation_status(confirmations.get(plan.occurrence_id), plan, record) == UNKNOWN for plan in plans):
            if payload.get('confirm_current_plan') is not True:
                raise ValidationError('O planejamento mudou. Confirme que revisou as ocorrências e os horários atuais antes de salvar.')
    notes = text(payload.get('notes', ''), 'Observações', 5000)
    if record is None:
        record = ScheduleAvailability(schedule=state.schedule, sub_team_id=team_id, member_id=member_id)
    record.deleted = None
    record.deleted_by_cascade = False
    record.mode, record.selected_occurrence_ids, record.notes = mode, selection, notes
    record.recorded_by, record.recorded_at = user, timezone.now()
    record.save()
    for plan in plans:
        occurrence = plan.occurrence
        confirmation = ScheduleAvailabilityOccurrence.all_objects.filter(availability=record, occurrence=occurrence).first()
        if confirmation is None:
            confirmation = ScheduleAvailabilityOccurrence(availability=record, occurrence=occurrence)
        confirmation.deleted = None
        confirmation.deleted_by_cascade = False
        confirmation.team_plan_id = plan.pk
        confirmation.occurrence_date = occurrence.schedule_day.date
        confirmation.start_time, confirmation.occurrence_name = occurrence.start_time, occurrence.name
        confirmation.is_available = _declared_available(mode, selection, occurrence.pk)
        confirmation.save()
    state.availability_revision = state.revision + 1


def clear_availability(state, payload):
    from .schedule_assistant import parse_identifier
    team_id = parse_identifier(payload.get('team_id'), 'Equipe')
    member_id = parse_identifier(payload.get('member_id'), 'Integrante')
    get_object_or_404(active_memberships(state), sub_team_id=team_id, member_id=member_id)
    record = ScheduleAvailability.objects.filter(schedule=state.schedule, sub_team_id=team_id, member_id=member_id).first()
    if record:
        record.delete()
    state.availability_revision = state.revision + 1


def reconcile_review(state):
    if not ScheduleAvailability.all_objects.filter(schedule=state.schedule).exists():
        # Preserve legacy workflow markers created before this stage existed,
        # while still recording the current unknown coverage baseline.
        persist_coverage_metrics(state)
        return
    data = serialize_availability(state)
    ids = set(data['review_occurrence_ids'])
    occurrences = list(ScheduleOccurrence.objects.filter(schedule_day__schedule=state.schedule))
    state.availability_review = []
    for occurrence in occurrences:
        required = occurrence.pk in ids
        if occurrence.availability_review_required != required:
            occurrence.availability_review_required = required
            occurrence.save(update_fields=['availability_review_required', 'update_at'])
        if required:
            state.availability_review.append({'occurrence_id': occurrence.pk, 'name': occurrence.name,
                                              'reason': 'Há disponibilidades a confirmar para a data, o horário ou a participação atual da equipe.'})
    persist_coverage_metrics(state, data)


def persist_coverage_metrics(state, data=None):
    """Store current coverage facts for future generation and balancing advice.

    These diagnostics intentionally do not assign anyone and are not exposed in
    the availability screen yet.
    """
    data = data or serialize_availability(state)
    plans = {
        plan.pk: plan
        for plan in planned_teams(state).select_related('occurrence__schedule_day')
    }
    rows = {row['team_plan_id']: row for row in data['summary']['rows']}
    existing = {
        metric.occurrence_team_id: metric
        for metric in ScheduleAvailabilityCoverage.all_objects.filter(schedule=state.schedule)
    }
    live_ids = set()
    now = timezone.now()
    for plan_id, row in rows.items():
        plan = plans.get(plan_id)
        if plan is None:
            continue
        live_ids.add(plan_id)
        metric = existing.get(plan_id)
        if metric is None:
            metric = ScheduleAvailabilityCoverage(schedule=state.schedule, occurrence_team=plan)
        metric.deleted = None
        metric.deleted_by_cascade = False
        metric.occurrence_date = plan.occurrence.schedule_day.date
        metric.start_time = plan.occurrence.start_time
        metric.occurrence_name = plan.occurrence.name
        metric.team_name = plan.team_name
        metric.required_members = row['required']
        metric.available_members = row['available']
        metric.unavailable_members = row['unavailable']
        metric.unknown_members = row['unknown']
        metric.available_mentors = row['mentors_available']
        metric.available_pending_experience = row['pending_experience_available']
        metric.member_shortage = row['shortage']
        metric.mentor_shortage = row['mentor_shortage']
        metric.calculated_at = now
        metric.save()
    for plan_id, metric in existing.items():
        if plan_id not in live_ids and not metric.deleted:
            metric.delete()
