"""Published, concrete schedule occurrences assigned to a member."""

from zoneinfo import ZoneInfo

from django.db.models import Exists, OuterRef, Prefetch, Q, F
from django.utils import timezone

from website.models.ministry_membership import MinistryMembership
from website.models.schedule import DivisionMember, ScheduleDay, Team


WEEKDAYS = ('Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira',
            'Sexta-feira', 'Sábado', 'Domingo')
MONTHS = ('jan', 'fev', 'mar', 'abr', 'mai', 'jun',
          'jul', 'ago', 'set', 'out', 'nov', 'dez')


def upcoming_member_schedules(member, *, today=None, limit=4):
    # Church calendar dates follow Brasilia, even when the server runs in UTC.
    today = today or timezone.localdate(timezone=ZoneInfo('America/Sao_Paulo'))
    ministry_ids = MinistryMembership.objects.filter(
        member=member, is_active=True, ministry__is_active=True,
    ).values('ministry_id')
    divisions = DivisionMember.objects.filter(
        schedule_day_id=OuterRef('pk'), member=member,
        division__is_active=True, division__deleted__isnull=True,
        division__schedule_id=OuterRef('schedule_id'),
    )
    days = (
        ScheduleDay.objects.filter(
            schedule__ministry_id__in=ministry_ids,
            schedule__deleted__isnull=True,
            schedule__is_published=True,
            is_cancelled=False, date__gte=today,
        )
        .filter(
            Q(schedule__schedule_type='monthly') |
            (Q(schedule__schedule_type='weekly') &
             (Q(schedule__start_date__isnull=True) | Q(date__gte=F('schedule__start_date'))) &
             (Q(schedule__end_date__isnull=True) | Q(date__lte=F('schedule__end_date'))))
        )
        .annotate(
            assigned_directly=Exists(ScheduleDay.members.through.objects.filter(
                scheduleday_id=OuterRef('pk'), member_id=member.pk,
            )),
            assigned_morning=Exists(ScheduleDay.members_morning.through.objects.filter(
                scheduleday_id=OuterRef('pk'), member_id=member.pk,
            )),
            assigned_evening=Exists(ScheduleDay.members_evening.through.objects.filter(
                scheduleday_id=OuterRef('pk'), member_id=member.pk,
            )),
            assigned_team=Exists(Team.objects.filter(
                pk=OuterRef('team_id'), members=member, is_active=True,
            )),
            assigned_division=Exists(divisions),
        )
        .filter(
            Q(assigned_division=True) |
            (Q(has_shifts=True) & (Q(assigned_morning=True) | Q(assigned_evening=True))) |
            (Q(has_shifts=False) & (Q(assigned_directly=True) | Q(assigned_team=True)))
        )
        .select_related('schedule__ministry', 'team')
        .prefetch_related(Prefetch(
            'division_assignments',
            queryset=DivisionMember.objects.filter(
                member=member, division__is_active=True, division__deleted__isnull=True,
            ).select_related('division'),
            to_attr='personal_divisions',
        ))
        .order_by('date', 'schedule__ministry__name', 'schedule__title', 'pk')[:limit]
    )
    entries = []
    for day in days:
        shifts = set()
        if day.has_shifts:
            if day.assigned_morning:
                shifts.add('morning')
            if day.assigned_evening:
                shifts.add('evening')
            shifts.update(assignment.shift for assignment in day.personal_divisions)
        delta = (day.date - today).days
        entries.append({
            'day': day,
            'schedule': day.schedule,
            'weekday': WEEKDAYS[day.date.weekday()],
            'month': MONTHS[day.date.month - 1],
            'relative_date': 'Hoje' if delta == 0 else 'Amanhã' if delta == 1 else f'Em {delta} dias',
            'shift': ' e '.join(label for key, label in (
                ('morning', 'Manhã'), ('evening', 'Noite'),
            ) if key in shifts),
            'divisions': ', '.join(dict.fromkeys(
                assignment.division.name for assignment in day.personal_divisions
            )),
        })
    return entries
