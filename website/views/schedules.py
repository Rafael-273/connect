import os
from collections import defaultdict
from datetime import datetime

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import get_template
from django.views import View
from django.views.generic import ListView
from xhtml2pdf import pisa

from ..models import Member, Ministry, MonthlySchedule, ScheduleDay, Team
from ..models.ministry_membership import MinistryMembership
from .mixins import StaffRequiredMixin

WEEKDAYS_PT = [
    'Segunda-feira',
    'Terça-feira',
    'Quarta-feira',
    'Quinta-feira',
    'Sexta-feira',
    'Sábado',
    'Domingo',
]


class TeamFormContextMixin:
    """Shared context builder for team create/edit forms.

    Batches the ministry-membership lookup to avoid N+1 queries.
    """

    def get_team_form_context(self):
        ministries = Ministry.objects.filter(deleted__isnull=True).order_by('name')

        all_members = Member.objects.filter(
            is_active=True,
            deleted__isnull=True,
        ).prefetch_related('ministry').order_by('name')

        # Batch-fetch new-system memberships to avoid per-member queries
        new_membership_qs = MinistryMembership.objects.filter(
            is_active=True,
        ).values_list('member_id', 'ministry_id')

        new_system_map = defaultdict(set)
        for member_id, ministry_id in new_membership_qs:
            new_system_map[member_id].add(ministry_id)

        members_with_ministries = []
        for member in all_members:
            # Uses prefetch cache (all() instead of values_list)
            ministry_ids_old = {m.id for m in member.ministry.all()}
            ministry_ids_new = new_system_map.get(member.id, set())
            all_ministry_ids = ministry_ids_old | ministry_ids_new

            if all_ministry_ids:
                members_with_ministries.append({
                    'id': member.id,
                    'name': member.name,
                    'ministry_ids': list(all_ministry_ids),
                })

        return {
            'ministries': ministries,
            'members': all_members,
            'members_with_ministries': members_with_ministries,
        }


# ==================== TEAM VIEWS ====================


class TeamListView(LoginRequiredMixin, StaffRequiredMixin, ListView):
    """Lista todas as equipes."""

    model = Team
    template_name = 'admin_panel/schedules/teams/list.html'
    context_object_name = 'teams'

    def get_queryset(self):
        queryset = (
            Team.objects
            .select_related('ministry', 'leader')
            .prefetch_related('members')
            .filter(deleted__isnull=True)
        )

        ministry_id = self.request.GET.get('ministry')
        search = self.request.GET.get('search', '')

        if ministry_id:
            queryset = queryset.filter(ministry_id=ministry_id)

        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) | Q(ministry__name__icontains=search)
            )

        return queryset.annotate(member_count=Count('members')).order_by('ministry__name', 'name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['ministries'] = Ministry.objects.filter(deleted__isnull=True).order_by('name')
        context['selected_ministry'] = self.request.GET.get('ministry')
        context['search'] = self.request.GET.get('search', '')
        return context


class TeamCreateView(LoginRequiredMixin, StaffRequiredMixin, TeamFormContextMixin, View):
    """Cria uma nova equipe."""

    def get(self, request):
        context = self.get_team_form_context()
        return render(request, 'admin_panel/schedules/teams/form.html', context)

    def post(self, request):
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
            color=color,
        )

        if member_ids:
            team.members.set(member_ids)

        return redirect('team_list')


class TeamEditView(LoginRequiredMixin, StaffRequiredMixin, TeamFormContextMixin, View):
    """Edita uma equipe existente."""

    def get(self, request, team_id):
        team = get_object_or_404(Team, id=team_id, deleted__isnull=True)
        context = self.get_team_form_context()
        context['team'] = team
        return render(request, 'admin_panel/schedules/teams/form.html', context)

    def post(self, request, team_id):
        team = get_object_or_404(Team, id=team_id, deleted__isnull=True)

        team.name = request.POST.get('name')
        team.ministry_id = request.POST.get('ministry')
        leader_id = request.POST.get('leader')
        team.leader_id = leader_id if leader_id else None
        team.color = request.POST.get('color', '#3B82F6')

        member_ids = request.POST.getlist('members')
        team.members.set(member_ids)

        team.save()
        return redirect('team_list')


class TeamDeleteView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Deleta uma equipe (POST-only, retorna JSON)."""

    def post(self, request, team_id):
        team = get_object_or_404(Team, id=team_id, deleted__isnull=True)
        team.delete()
        return JsonResponse({'success': True})

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


# ==================== MONTHLY SCHEDULE VIEWS ====================


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

        return redirect('schedule_detail', schedule_id=schedule.id)


class ScheduleEditView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Edita uma escala mensal existente."""

    def get(self, request, schedule_id):
        schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)
        current_year = datetime.now().year
        context = {
            'schedule': schedule,
            'ministries': Ministry.objects.filter(deleted__isnull=True).order_by('name'),
            'years': range(current_year - 1, current_year + 2),
            'months': MonthlySchedule.MONTH_CHOICES,
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

        # Hybrid member lookup (old M2M + new MinistryMembership)
        member_ids_old = set(
            Member.objects
            .filter(ministry=schedule.ministry, is_active=True, deleted__isnull=True)
            .values_list('id', flat=True)
        )
        member_ids_new = set(
            MinistryMembership.objects
            .filter(ministry=schedule.ministry, is_active=True)
            .values_list('member_id', flat=True)
        )
        all_member_ids = member_ids_old | member_ids_new
        members = (
            Member.objects
            .filter(id__in=all_member_ids, is_active=True, deleted__isnull=True)
            .order_by('name')
        )

        # Organise days by week
        weeks = {}
        for day in days:
            try:
                day.pt_weekday = WEEKDAYS_PT[day.date.weekday()]
            except Exception:
                day.pt_weekday = ''
            week_num = day.get_week_number()
            weeks.setdefault(week_num, []).append(day)

        context = {
            'schedule': schedule,
            'days': days,
            'weeks': weeks,
            'teams': teams,
            'members': members,
        }
        return render(request, 'admin_panel/schedules/monthly/detail.html', context)


class ScheduleDeleteView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Deleta uma escala mensal (POST-only, retorna JSON)."""

    def post(self, request, schedule_id):
        schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)
        schedule.delete()
        return JsonResponse({'success': True})

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


# Publishing feature removed — schedules are considered active by creation month


# ==================== SCHEDULE DAY VIEWS ====================


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

        # Check for existing active day on this date
        if ScheduleDay.objects.filter(schedule=schedule, date=date).exists():
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

        try:
            day = ScheduleDay.objects.create(
                schedule=schedule,
                date=date,
                team_id=team_id if team_id else None,
                description=description,
                notes=notes,
            )

            if member_ids:
                day.members.set(member_ids)

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

        day.team_id = team_id if team_id else None
        day.description = description
        day.notes = notes
        day.save()

        day.members.set(member_ids)
        return JsonResponse({'success': True})

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


class ScheduleExportPDFView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Exporta a escala em PDF."""

    MONTH_NAMES = [
        'Janeiro', 'Fevereiro', 'Março', 'Abril', 'Maio', 'Junho',
        'Julho', 'Agosto', 'Setembro', 'Outubro', 'Novembro', 'Dezembro',
    ]
    DAY_NAMES = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']

    def get(self, request, schedule_id):
        schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)
        days = (
            ScheduleDay.objects
            .filter(schedule=schedule, deleted__isnull=True)
            .select_related('team')
            .prefetch_related('members')
            .order_by('date')
        )

        processed_days = []
        for day in days:
            day_data = {
                'date': day.date,
                'date_str': day.date.strftime('%d/%m/%Y'),
                'day_name': self.DAY_NAMES[day.date.weekday()],
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

        logo_path = os.path.join(settings.BASE_DIR, 'static', 'assets', 'red_logo.png')
        month_name = self.MONTH_NAMES[schedule.month - 1]

        context = {
            'schedule': schedule,
            'days': processed_days,
            'month_name': month_name,
            'generated_at': datetime.now().strftime('%d/%m/%Y às %H:%M'),
            'use_team_rotation': schedule.use_team_rotation,
            'logo_path': logo_path,
        }

        template = get_template('admin_panel/schedules/monthly/pdf_template.html')
        html = template.render(context)

        response = HttpResponse(content_type='application/pdf')
        filename = f"escala_{schedule.ministry.name.replace(' ', '_')}_{month_name}_{schedule.year}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'

        pisa_status = pisa.CreatePDF(
            html.encode('utf-8'),
            dest=response,
            encoding='utf-8',
        )

        if pisa_status.err:
            return HttpResponse('Erro ao gerar PDF', status=500)

        return response
