from collections import defaultdict
import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import ListView

from ..mixins import StaffRequiredMixin
from ...models import Ministry, Team
from ...models.member import Member
from ...models.ministry_membership import MinistryMembership


class TeamFormContextMixin:
    """Shared context builder for team create/edit forms.

    Batches the ministry-membership lookup to avoid N+1 queries.
    """

    def get_team_form_context(self):
        ministries = Ministry.objects.filter(deleted__isnull=True).order_by('name')

        all_members = Member.objects.filter(
            is_active=True,
            deleted__isnull=True,
        ).order_by('name')

        new_membership_qs = MinistryMembership.objects.filter(
            is_active=True,
            deleted__isnull=True,
        ).values_list('member_id', 'ministry_id')

        new_system_map = defaultdict(set)
        for member_id, ministry_id in new_membership_qs:
            new_system_map[member_id].add(ministry_id)

        members_payload = [
            {
                'id': member.id,
                'name': member.name,
                'ministries': sorted(new_system_map[member.id]),
            }
            for member in all_members
            if new_system_map[member.id]
        ]

        return {
            'ministries': ministries,
            'members': all_members,
            'members_with_ministries': members_payload,
            'members_with_ministries_json': json.dumps(members_payload),
        }


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
