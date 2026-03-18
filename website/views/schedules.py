import json
import os
from calendar import monthrange
from collections import defaultdict
from datetime import datetime

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError
from django.db.models import Count, Prefetch, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import get_template
from django.views import View
from django.views.generic import ListView
from xhtml2pdf import pisa

from ..models import Member, Ministry, MonthlySchedule, ScheduleDay, Team
from ..models.ministry_membership import MinistryMembership
from ..models.schedule import DivisionMember, ScaleDivision
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


# FBV aliases — mantidos para compatibilidade com urls.py
team_list_view = TeamListView.as_view()
team_create_view = TeamCreateView.as_view()
team_edit_view = TeamEditView.as_view()
team_delete_view = TeamDeleteView.as_view()


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


        # Criar divisões enviadas no formulário
        division_names = [n.strip() for n in request.POST.getlist('divisions') if n.strip()]
        for i, name in enumerate(division_names):
            ScaleDivision.objects.create(schedule=schedule, name=name, order=i)

        return redirect('schedule_detail', schedule_id=schedule.id)


# FBV aliases — mantidos para compatibilidade com urls.py
schedule_list_view = ScheduleListView.as_view()
schedule_create_view = ScheduleCreateView.as_view()


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

        ScaleDivision.objects.filter(schedule=schedule, deleted__isnull=True).delete()
        division_names = [n.strip() for n in request.POST.getlist('divisions') if n.strip()]
        for i, name in enumerate(division_names):
            ScaleDivision.objects.create(schedule=schedule, name=name, order=i)

        return redirect('schedule_detail', schedule_id=schedule.id)


@login_required
def schedule_edit_view(request, schedule_id):
    """Alias mantido para compatibilidade — delega para a CBV."""
    return ScheduleEditView.as_view()(request, schedule_id=schedule_id)


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


@login_required
def schedule_detail_view(request, schedule_id):
    """Alias mantido para compatibilidade — delega para a CBV."""
    return ScheduleDetailView.as_view()(request, schedule_id=schedule_id)


class ScheduleDeleteView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Deleta uma escala mensal (POST-only, retorna JSON)."""

    def post(self, request, schedule_id):
        schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)
        schedule.delete()
        return JsonResponse({'success': True})

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


# FBV alias — mantido para compatibilidade com urls.py
schedule_delete_view = ScheduleDeleteView.as_view()


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
                    schedule__deleted__isnull=True
                ).exclude(schedule_id=schedule.id).select_related('schedule', 'schedule__ministry')
                
                if conflicting_days.exists():
                    conflicts = []
                    seen = set()
                    for cd in conflicting_days:
                        for member in cd.members.filter(id__in=int_member_ids):
                            key = (member.id, cd.schedule.id)
                            if key not in seen:
                                seen.add(key)
                                conflicts.append(f'{member.name} → {cd.schedule.ministry.name} - {cd.schedule.title}')
                    if conflicts:
                        return JsonResponse({
                            'success': False,
                            'conflict': True,
                            'error': 'Conflito de escala detectado',
                            'conflict_details': conflicts
                        }, status=409)
        
        # Verifica se já existe um dia ATIVO para esta data nesta escala
        existing_active_day = ScheduleDay.objects.filter(
            schedule=schedule,
            date=date
        ).first()
        
        if existing_active_day:
            # Se já existe um dia ativo, retorna erro
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


# FBV alias — mantido para compatibilidade com urls.py
schedule_day_create_view = ScheduleDayCreateView.as_view()


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
                    schedule__deleted__isnull=True
                ).exclude(schedule_id=day.schedule_id).select_related('schedule', 'schedule__ministry')
                
                if conflicting_days.exists():
                    conflicts = []
                    seen = set()
                    for cd in conflicting_days:
                        for member in cd.members.filter(id__in=int_member_ids):
                            key = (member.id, cd.schedule.id)
                            if key not in seen:
                                seen.add(key)
                                conflicts.append(f'{member.name} → {cd.schedule.ministry.name} - {cd.schedule.title}')
                    if conflicts:
                        return JsonResponse({
                            'success': False,
                            'conflict': True,
                            'error': 'Conflito de escala detectado',
                            'conflict_details': conflicts
                        }, status=409)
        
        day.team_id = team_id if team_id else None
        day.description = description
        day.notes = notes
        day.save()

        day.members.set(member_ids)
        return JsonResponse({'success': True})

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


# FBV alias — mantido para compatibilidade com urls.py
schedule_day_edit_view = ScheduleDayEditView.as_view()


class ScheduleDayDeleteView(LoginRequiredMixin, StaffRequiredMixin, View):
    """Deleta um dia da escala (POST-only, retorna JSON)."""

    def post(self, request, day_id):
        day = get_object_or_404(ScheduleDay, id=day_id, deleted__isnull=True)
        day.delete()
        return JsonResponse({'success': True})

    def http_method_not_allowed(self, request, *args, **kwargs):
        return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


# FBV alias — mantido para compatibilidade com urls.py
schedule_day_delete_view = ScheduleDayDeleteView.as_view()


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


# FBV alias — mantido para compatibilidade com urls.py
schedule_day_toggle_cancel_view = ScheduleDayToggleCancelView.as_view()


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


@login_required
def schedule_export_pdf_view(request, schedule_id):
    """Alias mantido para compatibilidade — delega para a CBV."""
    return ScheduleExportPDFView.as_view()(request, schedule_id=schedule_id)


@login_required
def schedule_print_view(request, schedule_id):
    """Exibe a escala em formato printável para impressão nativa do navegador"""
    schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)
    days = ScheduleDay.objects.filter(
        schedule=schedule,
        deleted__isnull=True
    ).select_related('team').prefetch_related(
        'members',
        'division_assignments',
        'division_assignments__division',
        'division_assignments__member',
    ).order_by('date')

    # Flat ordered list of all divisions for this schedule (for table columns)
    divisions = list(ScaleDivision.objects.filter(
        schedule=schedule,
        deleted__isnull=True,
        is_active=True,
    ).order_by('order', 'name'))

    month_names = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
                   "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]

    day_names = ['Segunda', 'Terça', 'Quarta', 'Quinta', 'Sexta', 'Sábado', 'Domingo']

    processed_days = []
    for day in days:
        # Build division → members map for this day
        div_members_map = {}  # division_id: [member, ...]
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

    context = {
        'schedule': schedule,
        'days': processed_days,
        'divisions': divisions,
        'month_name': month_names[schedule.month - 1],
        'generated_at': datetime.now().strftime('%d/%m/%Y às %H:%M'),
        'use_team_rotation': schedule.use_team_rotation,
    }

    return render(request, 'admin_panel/schedules/monthly/print.html', context)


# ==================== SCHEDULE CONFLICT CHECK ====================

@login_required
def check_schedule_conflict_view(request):
    """Verifica se membros já estão escalados em outra escala na mesma data"""
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

    # Buscar dias de escala na mesma data que contenham algum dos membros selecionados
    conflicting_days = ScheduleDay.objects.filter(
        date=date,
        members__id__in=member_ids,
        is_cancelled=False,
        deleted__isnull=True,
        schedule__deleted__isnull=True
    ).select_related('schedule', 'schedule__ministry')

    # Excluir dias da escala atual (não é conflito consigo mesma)
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
        'conflicts': conflicts
    })


# ==================== DIVISION VIEWS ====================

@login_required
def division_list_view(request, ministry_id):
    """Redireciona para listagem de escalas (divisões agora são por escala)"""
    return redirect('schedule_list')


@login_required
def division_create_view(request, schedule_id):
    """Cria uma nova divisão para uma escala (AJAX)"""
    schedule = get_object_or_404(MonthlySchedule, id=schedule_id, deleted__isnull=True)

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        parent_id = request.POST.get('parent') or None
        order = request.POST.get('order', 0)

        if not name:
            return JsonResponse({'success': False, 'error': 'Nome obrigatório'}, status=400)

        try:
            division = ScaleDivision(
                name=name,
                schedule=schedule,
                parent_id=parent_id,
                order=int(order)
            )
            division.save()
            return JsonResponse({
                'success': True,
                'division_id': division.id,
                'name': division.name
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


@login_required
def division_edit_view(request, division_id):
    """Edita uma divisão (AJAX)"""
    division = get_object_or_404(ScaleDivision, id=division_id, deleted__isnull=True)

    if request.method == 'POST':
        division.name = request.POST.get('name', '').strip()
        parent_id = request.POST.get('parent')
        division.parent_id = parent_id if parent_id else None
        division.order = int(request.POST.get('order', 0))
        division.is_active = request.POST.get('is_active') == 'true'

        try:
            division.save()
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


@login_required
def division_delete_view(request, division_id):
    """Deleta uma divisão"""
    if request.method == 'POST':
        division = get_object_or_404(ScaleDivision, id=division_id, deleted__isnull=True)
        division.delete()
        return JsonResponse({'success': True})

    return JsonResponse({'success': False, 'error': 'Método não permitido'}, status=405)


# ==================== DIVISION MEMBER ASSIGNMENT VIEWS ====================

@login_required
def schedule_day_division_assign_view(request, day_id):
    """Atribui membros a divisões em um dia de escala"""
    day = get_object_or_404(ScheduleDay, id=day_id, deleted__isnull=True)
    
    if request.method == 'POST':
        import json
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'JSON inválido'}, status=400)
        
        assignments = data.get('assignments', [])
        
        # Limpar atribuições existentes para este dia
        DivisionMember.objects.filter(
            schedule_day=day,
            deleted__isnull=True
        ).delete()
        
        # Criar novas atribuições
        created = []
        for item in assignments:
            division_id = item.get('division_id')
            member_ids = item.get('member_ids', [])
            
            division = get_object_or_404(
                ScaleDivision, id=division_id, deleted__isnull=True
            )
            
            for member_id in member_ids:
                dm = DivisionMember.objects.create(
                    division=division,
                    schedule_day=day,
                    member_id=member_id
                )
                created.append(dm.id)
        
        return JsonResponse({
            'success': True,
            'created_count': len(created)
        })
    
    # GET: return current assignments
    assignments = DivisionMember.objects.filter(
        schedule_day=day,
        deleted__isnull=True
    ).select_related('division', 'member').order_by('division__order', 'member__name')
    
    divisions = ScaleDivision.objects.filter(
        schedule=day.schedule,
        is_active=True,
        deleted__isnull=True
    ).order_by('order', 'name')
    
    data = {
        'day_id': day.id,
        'date': day.date.strftime('%d/%m/%Y'),
        'divisions': [
            {
                'id': d.id,
                'name': d.name,
                'parent_id': d.parent_id,
                'order': d.order,
            }
            for d in divisions
        ],
        'assignments': [
            {
                'id': a.id,
                'division_id': a.division_id,
                'division_name': a.division.name,
                'member_id': a.member_id,
                'member_name': a.member.name,
            }
            for a in assignments
        ]
    }
    
    return JsonResponse(data)
