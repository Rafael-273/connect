from django.shortcuts import get_object_or_404, render

from ..models.schedule import MonthlySchedule, ScheduleDay
from .mixins import MemberRequiredMixin
from django.views import View
from django.utils import timezone
from zoneinfo import ZoneInfo

WEEKDAYS_PT = [
    'Segunda-feira',
    'Terça-feira',
    'Quarta-feira',
    'Quinta-feira',
    'Sexta-feira',
    'Sábado',
    'Domingo',
]


def _member_in_day(member, day):
    """Returns True if the member is assigned to a schedule day."""
    if day.division_assignments.filter(member=member, division__is_active=True).exists():
        return True
    if day.has_shifts:
        if day.members_morning.filter(id=member.id).exists():
            return True
        if day.members_evening.filter(id=member.id).exists():
            return True
        return False
    if day.members.filter(id=member.id).exists():
        return True
    if day.team and day.team.members.filter(id=member.id).exists():
        return True
    if day.division_assignments.filter(member=member).exists():
        return True
    return False


class MemberScheduleDetailView(MemberRequiredMixin, View):
    def get(self, request, schedule_id):
        member = self.member
        schedule = get_object_or_404(
            MonthlySchedule,
            id=schedule_id,
        )

        days = (
            ScheduleDay.objects
            .filter(schedule=schedule)
            .select_related('team')
            .prefetch_related(
                'members',
                'members_morning',
                'members_evening',
                'team__members',
                'division_assignments',
                'division_assignments__member',
                'division_assignments__division',
            )
            .order_by('date')
        )
        if schedule.is_weekly:
            days = days.filter(date__gte=timezone.localdate(timezone=ZoneInfo('America/Sao_Paulo')))
            if schedule.start_date:
                days = days.filter(date__gte=schedule.start_date)
            if schedule.end_date:
                days = days.filter(date__lte=schedule.end_date)
        else:
            days = days.filter(date__month=schedule.month, date__year=schedule.year)

        MONTH_NAMES = [
            'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
            'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro',
        ]
        MONTH_ABBR = [
            'Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun',
            'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez',
        ]

        processed_days = []
        member_day_count = 0
        for day in days:
            assigned = _member_in_day(member, day)
            if assigned:
                member_day_count += 1
            # Build member list for display
            day_members = list(day.members.all())
            if day.team:
                team_members = list(day.team.members.filter(is_active=True))
                seen = {m.id for m in day_members}
                for m in team_members:
                    if m.id not in seen:
                        day_members.append(m)
                        seen.add(m.id)
            # Division assignments keyed by division name
            div_map = {}
            for assignment in day.division_assignments.all():
                div_name = assignment.division.name
                div_map.setdefault(div_name, []).append(assignment.member)

            processed_days.append({
                'day': day,
                'pt_weekday': WEEKDAYS_PT[day.date.weekday()],
                'pt_month_abbr': MONTH_ABBR[day.date.month - 1],
                'is_member_assigned': assigned,
                'members': day_members,
                'div_map': div_map,
                'members_morning': list(day.members_morning.all()) if day.has_shifts else [],
                'members_evening': list(day.members_evening.all()) if day.has_shifts else [],
            })

        return render(request, 'member/schedule_detail.html', {
            'member': member,
            'schedule': schedule,
            'days': processed_days,
            'member_day_count': member_day_count,
            'month_name': MONTH_NAMES[schedule.month - 1] if schedule.month else '',
        })
