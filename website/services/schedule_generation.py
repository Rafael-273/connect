"""Generation adapter: canonical assignments, confirmed availability and live guards."""
import calendar
import datetime
import hashlib
import json
from collections import defaultdict
from itertools import combinations

from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from django.utils import timezone
from safedelete.models import SOFT_DELETE

from website.models import DivisionMember, MonthlySchedule, ScheduleDay, ScaleDivision
from .schedule_availability import _context, confirmation_status, AVAILABLE
from .schedule_optimizer import Duty, Problem, solve
from .schedule_visibility import member_visible_schedules

EXPERIENCE = {'mentor': 'Apto a acompanhar', 'trainee': 'Em treinamento', 'pending': 'Classificação pendente'}


def assignments(state):
    return list(DivisionMember.objects.filter(schedule_day__schedule=state.schedule).select_related(
        'member', 'occurrence_team__occurrence__schedule_day', 'division'))


def _history(state):
    """Legacy schedules are already member-visible; conservatively reserve their date.

    Include all four legacy membership channels and canonical division assignments.
    Never interpret an unpublished assistant draft as a commitment.
    """
    # A bounded window prevents old history from dominating the month while still
    # carrying the immediately preceding month across the month boundary.
    first = (state.period - datetime.timedelta(days=1)).replace(day=1)
    for _ in range(2):
        first = (first - datetime.timedelta(days=1)).replace(day=1)
    last = state.period.replace(day=calendar.monthrange(state.period.year, state.period.month)[1])
    schedules = member_visible_schedules(MonthlySchedule.objects.all()).exclude(pk=state.schedule_id)
    days = ScheduleDay.objects.filter(schedule__in=schedules, date__range=(first, last), is_cancelled=False,
                                      schedule__deleted__isnull=True).select_related('schedule', 'team').prefetch_related(
        'members', 'members_morning', 'members_evening', 'team__members',
        Prefetch('division_assignments', queryset=DivisionMember.objects.select_related('division', 'occurrence_team__occurrence')))
    rows = set()
    team_names = {m.sub_team.name: m.sub_team_id for m in _context_memberships(state)}
    for day in days:
        media = day.schedule.ministry_id == state.ministry_id
        members = set()
        for relation in (day.members, day.members_morning, day.members_evening):
            members.update(m.pk for m in relation.all())
        if day.team and not day.team.deleted:
            members.update(m.pk for m in day.team.members.all())
        assigned_members = set()
        for assignment in day.division_assignments.all():
            plan = assignment.occurrence_team
            if plan and (plan.deleted or plan.occurrence.deleted):
                continue
            team = plan.sub_team_id if plan else team_names.get(assignment.division.name) if media else None
            token = f'occ:{plan.occurrence_id}' if plan else f'day:{day.pk}'
            time = plan.occurrence.start_time.isoformat() if plan else ''
            rows.add((assignment.member_id, day.date.isoformat(), team if media else None, token, time, media))
            assigned_members.add(assignment.member_id)
        for member in members - assigned_members:
            team = team_names.get(day.team.name) if media and day.team else None
            rows.add((member, day.date.isoformat(), team, f'day:{day.pk}', '', media))
    return sorted(rows, key=lambda row: (row[1], row[4], row[3], row[0], row[2] or 0))


def _context_memberships(state):
    from .schedule_availability import active_memberships
    return active_memberships(state)


def context(state):
    memberships, plans, records, confirmations = _context(state)
    plans.sort(key=lambda p: (p.occurrence.schedule_day.date, p.occurrence.start_time, p.sub_team_id or 0, p.pk))
    history = _history(state)
    levels = {(m.sub_team_id, m.member_id): m.experience_level for m in memberships}
    names = {m.member_id: m.member.name for m in memberships}
    commitments = {(member, date) for member, date, *_ in history if date >= state.period.isoformat()}
    eligible, declared, duties = {}, {}, []
    for plan in plans:
        confirmed = {
            m.member_id: m.experience_level for m in memberships if m.sub_team_id == plan.sub_team_id
            and confirmation_status(confirmations.get((m.sub_team_id, m.member_id), {}).get(plan.occurrence_id), plan,
                                    records.get((m.sub_team_id, m.member_id))) == AVAILABLE
        }
        declared[plan.pk] = confirmed
        eligible[plan.pk] = {m: level for m, level in confirmed.items() if level in ('mentor', 'trainee')
                            and (m, plan.occurrence.schedule_day.date.isoformat()) not in commitments}
        minimum = 1 if plan.member_count == 2 else plan.minimum_mentors
        duties.append(Duty(plan.pk, plan.occurrence_id, plan.occurrence.schedule_day.date.isoformat(),
                           plan.occurrence.start_time.isoformat(), plan.sub_team_id or 0, plan.member_count, minimum, eligible[plan.pk]))
    # Fingerprint *effective* eligibility, membership/experience and all planning context/history.
    raw = [[d.__dict__ for d in duties], history,
           [(p.pk, p.activities, p.notes, p.team_name, p.occurrence.name, p.occurrence.notes) for p in plans],
           sorted((team, member, level) for (team, member), level in levels.items())]
    signature = hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()
    return {'plans': plans, 'duties': duties, 'eligible': eligible, 'declared': declared, 'levels': levels,
            'names': names, 'history': history, 'signature': signature}


def group_errors(ctx, plan_id, member_ids, other_assignments=()):
    duty = next((d for d in ctx['duties'] if d.id == plan_id), None)
    if duty is None:
        return ['A equipe ou ocorrência foi retirada do planejamento. Remova estas designações.'] if member_ids else []
    errors = []
    if member_ids and not duty.minimum_mentors:
        errors.append('Defina a quantidade mínima de integrantes aptos a acompanhar para esta quantidade de vagas.')
    if len(member_ids) > duty.count:
        errors.append('A quantidade de integrantes excede as vagas planejadas.')
    if len(set(member_ids)) != len(member_ids):
        errors.append('Um integrante não pode ocupar duas vagas na mesma equipe e culto.')
    for member in member_ids:
        if member not in duty.candidates:
            errors.append(f'{ctx["names"].get(member, "Integrante")}: vínculo, classificação, disponibilidade confirmada ou compromisso existente impede esta designação.')
        for assignment in other_assignments:
            plan = assignment.occurrence_team
            if assignment.member_id != member or not plan:
                continue
            occurrence = plan.occurrence
            if occurrence.pk != duty.occurrence and occurrence.schedule_day.date.isoformat() == duty.date:
                errors.append(f'{assignment.member.name} já está em outro culto no mesmo dia.')
                break
    if member_ids and sum(duty.candidates.get(m) == 'mentor' for m in member_ids) < (duty.minimum_mentors or 1):
        errors.append('A composição precisa de pelo menos um integrante apto a acompanhar (ou do mínimo configurado).')
    return list(dict.fromkeys(errors))


def _division(state, plan):
    if plan.division_id and not plan.division.deleted:
        return plan.division
    sibling = type(plan).objects.filter(occurrence__schedule_day__schedule=state.schedule, sub_team_id=plan.sub_team_id,
                                        division__deleted__isnull=True, division__isnull=False).select_related('division').first()
    division = sibling.division if sibling else ScaleDivision.objects.create(schedule=state.schedule, name=plan.team_name)
    plan.division = division
    plan.save(update_fields=['division', 'update_at'])
    return division


def _write_group(state, ctx, plan, member_ids, locked_ids, source):
    """Update in place, including soft-deleted uniqueness rows; keep locked IDs stable."""
    current = {row.member_id: row for row in DivisionMember.objects.filter(occurrence_team=plan)}
    division = _division(state, plan)
    for member, row in current.items():
        if member not in member_ids:
            row.delete(force_policy=SOFT_DELETE)
    for member in member_ids:
        row = current.get(member)
        if row is None:
            # Legacy unique key is intentionally preserved for backwards compatibility.
            row = DivisionMember.all_objects.filter(division=division, schedule_day=plan.occurrence.schedule_day, member_id=member, shift='none').first()
            if row and not row.deleted and row.occurrence_team_id != plan.pk:
                raise ValidationError('Designação existente incompatível nesta divisão. Revise o planejamento.')
            if row and row.deleted:
                row.undelete(force_policy=SOFT_DELETE)
            if row is None:
                row = DivisionMember(division=division, schedule_day=plan.occurrence.schedule_day, member_id=member)
        if row.is_locked and source == 'automatic':
            continue
        row.occurrence_team = plan
        row.schedule_day = plan.occurrence.schedule_day
        row.is_locked = member in locked_ids
        row.assignment_source = source
        row.experience_snapshot = ctx['levels'][plan.sub_team_id, member]
        row.save()


def generate(state, payload):
    if payload.get('confirmed') is not True:
        raise ValidationError('Confirme a geração. Designações desbloqueadas serão recalculadas.')
    ctx = context(state)
    current = assignments(state)
    if any(row.occurrence_team_id is None for row in current):
        raise ValidationError('Há designações administrativas sem ocorrência. Revise a escala antes de gerar.')
    locked = [row for row in current if row.is_locked]
    plan_ids = {p.pk for p in ctx['plans']}
    if any(row.occurrence_team_id not in plan_ids for row in locked):
        raise ValidationError('Há bloqueios em equipes ou cultos removidos. Remova essas designações na revisão.')
    if any(r.schedule_day_id != r.occurrence_team.occurrence.schedule_day_id for r in locked):
        raise ValidationError('A data de uma designação bloqueada mudou. Revise a composição antes de regenerar.')
    for duty in ctx['duties']:
        ids = [row.member_id for row in locked if row.occurrence_team_id == duty.id]
        # A locked trainee may acquire a mentor during generation; validate final experience in solver.
        errors = group_errors(ctx, duty.id, ids, [r for r in locked if r.occurrence_team_id != duty.id])
        errors = [e for e in errors if not e.startswith('A composição precisa')]
        if errors:
            raise ValidationError(errors)
    from dataclasses import replace
    duties = [replace(d, locked=tuple(r.member_id for r in locked if r.occurrence_team_id == d.id)) for d in ctx['duties']]
    load, mentor_load, last_team, pairs = defaultdict(set), defaultdict(set), {}, {}
    grouped_history = defaultdict(set)
    for member, date, team, token, time, media in ctx['history']:
        if media:
            load[member].add((date, token))
            if ctx['levels'].get((team, member)) == 'mentor':
                mentor_load[member].add((date, token))
            if date < state.period.isoformat() and team:
                grouped_history[team, date, time, token].add(member)
    for (team, date, time, token), people in sorted(grouped_history.items(), key=lambda x: x[0][1:]):
        last_team[team] = tuple(sorted(people))
        pairs[team] = tuple(combinations(sorted(people), 2)) if len(people) == 2 else ()
    if len(duties) > 500 or sum(len(d.candidates) for d in duties) > 15000:
        raise ValidationError('O planejamento excede o limite de 500 atuações ou 15.000 candidaturas por geração. Divida a operação antes de continuar.')
    try:
        result, phases = solve(Problem(duties, {m: len(v) for m, v in load.items()},
                                      {m: len(v) for m, v in mentor_load.items()}, last_team, pairs))
    except ValueError as exc:
        raise ValidationError(str(exc))
    # Re-read external commitments, roles and availability before persisting a long solve.
    if context(state)['signature'] != ctx['signature']:
        from .schedule_assistant import PlanningConflict
        raise PlanningConflict('Os dados de entrada mudaram durante a geração. Recarregue e gere novamente.')
    for row in current:
        if row.occurrence_team_id not in plan_ids and not row.is_locked:
            row.delete(force_policy=SOFT_DELETE)
    for plan in ctx['plans']:
        ids = result[plan.pk]
        _write_group(state, ctx, plan, ids, {r.member_id for r in locked if r.occurrence_team_id == plan.pk}, 'automatic')
    state.generation_signature = ctx['signature']
    state.generation_report = {'generated_at': timezone.now().isoformat(), 'phases': phases,
                               'solver': 'OR-Tools CP-SAT 9.14; 1 worker; seed 0; 1 deterministic second/objective',
                               'fully_optimized': len(phases) == 7 and all(p['status'] == 'OPTIMAL' for p in phases)}
    state.current_step = 3


def edit_assignments(state, payload):
    from .schedule_assistant import parse_identifier
    ctx = context(state)
    plan_id = parse_identifier(payload.get('plan_id'), 'Atuação')
    plan = next((p for p in ctx['plans'] if p.pk == plan_id), None)
    if plan is None:
        raise ValidationError('Selecione uma atuação ativa deste planejamento.')
    raw = payload.get('member_ids')
    locks = payload.get('locked_member_ids', raw)
    if not isinstance(raw, list) or not isinstance(locks, list) or len(raw) > 1000 or len(locks) > 1000:
        raise ValidationError('Informe listas válidas de integrantes e bloqueios.')
    ids = [parse_identifier(m, 'Integrante') for m in raw]
    locked_ids = {parse_identifier(m, 'Integrante bloqueado') for m in locks}
    if not locked_ids.issubset(ids):
        raise ValidationError('Somente integrantes designados podem ser bloqueados.')
    others = [row for row in assignments(state) if row.occurrence_team_id != plan_id]
    errors = group_errors(ctx, plan_id, ids, others)
    if errors:
        raise ValidationError(errors)
    _write_group(state, ctx, plan, ids, locked_ids, 'manual')


def remove_obsolete(state, payload):
    from .schedule_assistant import parse_identifier
    row = get_object_or_404(DivisionMember, pk=parse_identifier(payload.get('assignment_id'), 'Designação'), schedule_day__schedule=state.schedule)
    live_ids = {p.pk for p in context(state)['plans']}
    if row.occurrence_team_id in live_ids:
        raise ValidationError('Edite a composição da equipe para remover esta designação.')
    row.delete(force_policy=SOFT_DELETE)


def serialize_generation(state):
    ctx, current = context(state), assignments(state)
    rows, per_member, consecutive = [], {}, []
    previous_by_team = {}
    for plan in ctx['plans']:
        chosen = [row for row in current if row.occurrence_team_id == plan.pk]
        others = [row for row in current if row.occurrence_team_id != plan.pk]
        errors = group_errors(ctx, plan.pk, [r.member_id for r in chosen], others)
        if any(r.schedule_day_id != plan.occurrence.schedule_day_id for r in chosen):
            errors.append('A data da ocorrência mudou. Revise os bloqueios e regenere a escala.')
        valid_count = len(chosen) if not errors else 0
        pending = max(0, plan.member_count - valid_count)
        reasons = list(errors)
        eligible = ctx['eligible'][plan.pk]
        if plan.member_count != 2 and not plan.minimum_mentors:
            reasons.append('Quantidade diferente de dois: configure o mínimo de integrantes aptos a acompanhar antes de gerar.')
        elif not any(level == 'mentor' for level in eligible.values()):
            reasons.append('Não há integrante apto a acompanhar com disponibilidade confirmada e sem compromisso existente neste culto.')
        elif pending and len(eligible) < plan.member_count:
            reasons.append(f'Esta ocorrência possui apenas {len(eligible)} integrante(s) elegível(is) para {plan.member_count} vagas.')
        elif pending:
            reasons.append('Vagas pendentes: há disputa por integrantes entre cultos do mesmo dia ou necessidade de acompanhamento. Revise a composição e as disponibilidades.')
        people = []
        prior = previous_by_team.get(plan.sub_team_id)
        for row in chosen:
            level = ctx['levels'].get((plan.sub_team_id, row.member_id), 'pending')
            people.append({'assignment_id': row.pk, 'member_id': row.member_id, 'name': row.member.name,
                           'experience': level, 'experience_label': EXPERIENCE.get(level, level), 'locked': row.is_locked,
                           'source': row.assignment_source, 'eligible': row.member_id in eligible})
            if not errors:
                item = per_member.setdefault(row.member_id, {'member_id': row.member_id, 'name': row.member.name, 'services': set(), 'team_assignments': 0, 'mentor_assignments': 0})
                item['services'].add(plan.occurrence_id)
                item['team_assignments'] += 1
                item['mentor_assignments'] += level == 'mentor'
            if prior and row.member_id in prior[1]:
                consecutive.append({'member': row.member.name, 'team': plan.team_name,
                                    'previous_date': prior[0], 'date': plan.occurrence.schedule_day.date.isoformat()})
        previous_by_team[plan.sub_team_id] = (plan.occurrence.schedule_day.date.isoformat(), {r.member_id for r in chosen})
        rows.append({'plan_id': plan.pk, 'team_id': plan.sub_team_id, 'team': plan.team_name, 'color': plan.color,
                     'occurrence_id': plan.occurrence_id, 'name': plan.occurrence.name,
                     'date': plan.occurrence.schedule_day.date.isoformat(), 'time': plan.occurrence.start_time.strftime('%H:%M'),
                     'activities': plan.activities, 'notes': plan.notes, 'required': plan.member_count,
                     'minimum_mentors': 1 if plan.member_count == 2 else plan.minimum_mentors,
                     'people': people, 'filled': valid_count, 'pending': pending, 'errors': errors, 'reasons': list(dict.fromkeys(reasons)),
                     'candidates': [{'id': m, 'name': ctx['names'][m], 'experience': level, 'experience_label': EXPERIENCE[level]} for m, level in sorted(eligible.items(), key=lambda x: (ctx['names'][x[0]], x[0]))]})
    for member, name in ctx['names'].items():
        per_member.setdefault(member, {'member_id': member, 'name': name, 'services': set(), 'team_assignments': 0, 'mentor_assignments': 0})
    metrics = [{**row, 'services': len(row['services'])} for row in per_member.values()]
    live_ids = {p.pk for p in ctx['plans']}
    obsolete = [{'id': r.pk, 'name': r.member.name, 'locked': r.is_locked,
                 'reason': 'Designação sem equipe/ocorrência ativa no planejamento.'} for r in current if r.occurrence_team_id not in live_ids]
    return {'generated': bool(state.generation_signature),
            'stale': bool(state.generation_signature and state.generation_signature != ctx['signature']),
            'report': state.generation_report, 'rows': rows, 'obsolete': obsolete,
            'summary': {'filled': sum(r['filled'] for r in rows), 'pending': sum(r['pending'] for r in rows),
                        'required': sum(r['required'] for r in rows), 'locked': sum(r.is_locked for r in current),
                        'people': sorted(metrics, key=lambda r: (r['name'], r['member_id'])), 'consecutive': consecutive}}
