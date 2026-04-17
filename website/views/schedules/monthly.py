from datetime import datetime

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import ListView

from ..mixins import StaffRequiredMixin
from ...models import Ministry, MonthlySchedule, ScheduleDay, Team
from ...models.member import Member
from ...models.ministry_membership import MinistryMembership
from ...models.schedule import ScaleDivision

WEEKDAYS_PT = [
    'Segunda-feira',
    'Terça-feira',
    'Quarta-feira',
    'Quinta-feira',
    'Sexta-feira',
    'Sábado',
    'Domingo',
]


class ScheduleListView(LoginRequiredMixin, StaffRequiredMixin, ListView):
    """Lista todas as escalas mensais."""

    model = MonthlySchedule
    template_name = 'admin_panel/schedules/monthly/list.html'
    context_object_name = 'schedules'

    def get_queryset(self):
        queryset = MonthlySchedule.objects.select_related('ministry').filter(deleted__isnull=True)

        ministry_id = self.request.GET.get('ministry')
        year = self.request.GET.get('year')
        month = self.request.GET.get('month')
        search = self.request.GET.get('search', '')

        if ministry_id:
            queryset = queryset.filter(ministry_id=ministry_id)
        if year:
            queryset = queryset.filter(year=year)
        if month:
            queryset = queryset.filter(month=month)
        if search:
            queryset = queryset.filter(
                Q(title__icontains=search) | Q(ministry__name__icontains=search)
            )

        return queryset.order_by('-year', '-month', 'ministry__name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        current_year = datetime.now().year
        context['ministries'] = Ministry.objects.filter(deleted__isnull=True).order_by('name')
        context['years'] = range(current_year - 1, current_year + 2)
        context['months'] = MonthlySchedule.MONTH_CHOICES
        context['selected_ministry'] = self.request.GET.get('ministry')
        context['selected_year'] = self.request.GET.get('year')
        context['selected_month'] = self.request.GET.get('month')
        context['search'] = self.request.GET.get('search', '')
        return context


class ScheduleCreateView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Cria uma nova escala mensal."""

    def get(self, request):
        current_year = datetime.now().year
        context = {
            'ministries': Ministry.objects.filter(deleted__isnull=True).order_by('name'),
            'years': range(current_year, current_year + 2),
            'months': MonthlySchedule.MONTH_CHOICES,
            'current_month': datetime.now().month,
            'current_year': current_year,
        }
        return render(request, 'admin_panel/schedules/monthly/form.html', context)

    def post(self, request):
        ministry_id = request.POST.get('ministry')
        title = request.POST.get('title')
        month = request.POST.get('month')
        year = request.POST.get('year')
        use_team_rotation = request.POST.get('use_team_rotation') == 'true'
        guidelines = request.POST.get('guidelines') or None

        ministry = get_object_or_404(Ministry, id=ministry_id)

        schedule = MonthlySchedule.objects.create(
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
    """Edita uma escala mensal existente."""

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
            'existing_divisions': existing_divisions,
        }
        return render(request, 'admin_panel/schedules/monthly/form.html', context)

    def post(self, request, schedule_id):
        schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)

        schedule.ministry_id = request.POST.get('ministry')
        schedule.title = request.POST.get('title')
        schedule.month = int(request.POST.get('month'))
        schedule.year = int(request.POST.get('year'))
        schedule.use_team_rotation = request.POST.get('use_team_rotation') == 'true'
        schedule.guidelines = request.POST.get('guidelines') or None
        schedule.save()

        ScaleDivision.objects.filter(schedule=schedule, deleted__isnull=True).delete()
        division_names = [n.strip() for n in request.POST.getlist('divisions') if n.strip()]
        for i, name in enumerate(division_names):
            ScaleDivision.objects.create(schedule=schedule, name=name, order=i)

        return redirect('schedule_detail', schedule_id=schedule.id)


class ScheduleDetailView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Visualiza detalhes de uma escala mensal."""

    def get(self, request, schedule_id):
        schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)

        days = (
            ScheduleDay.objects
            .filter(schedule=schedule, deleted__isnull=True)
            .select_related('team')
            .prefetch_related('members')
            .order_by('date')
        )

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
            week_num = day.get_week_number()
            weeks.setdefault(week_num, []).append(day)

        divisions = ScaleDivision.objects.filter(
            schedule=schedule, deleted__isnull=True
        ).order_by('order', 'name')

        return render(request, 'admin_panel/schedules/monthly/detail.html', {
            'schedule': schedule,
            'days': days,
            'weeks': weeks,
            'teams': teams,
            'members': members,
            'divisions': divisions,
        })


class ScheduleDeleteView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Deleta uma escala mensal (POST-only, retorna JSON)."""

    def post(self, request, schedule_id):
        schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)
        schedule.delete()
        return JsonResponse({'success': True})

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


@login_required
def schedule_print_view(request, schedule_id):
    """Exibe a escala em formato printável para impressão nativa do navegador"""
    schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)
    days = ScheduleDay.objects.filter(
        schedule=schedule,
        deleted__isnull=True,
    ).select_related('team').prefetch_related(
        'members',
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
    for day in days:
        div_members_map = {}
        for assignment in day.division_assignments.all():
            did = assignment.division_id
            if did not in div_members_map:
                div_members_map[did] = []
            div_members_map[did].append(assignment.member)

        day_data = {
            'date': day.date,
            'date_str': day.date.strftime('%d/%m/%Y'),
            'day_name': day_names[day.date.weekday()],
            'description': day.description,
            'notes': day.notes,
            'is_cancelled': day.is_cancelled,
            'cancellation_reason': day.cancellation_reason,
            'div_members_map': div_members_map,
        }

        if schedule.use_team_rotation:
            day_data['team'] = day.team
        else:
            day_data['members'] = list(day.members.all())

        processed_days.append(day_data)

    return render(request, 'admin_panel/schedules/monthly/print.html', {
        'schedule': schedule,
        'days': processed_days,
        'divisions': divisions,
        'month_name': month_names[schedule.month - 1],
        'generated_at': datetime.now().strftime('%d/%m/%Y às %H:%M'),
        'use_team_rotation': schedule.use_team_rotation,
    })
