"""Organization pages shared by every ministry and the legacy media entry points."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from website.forms.media_organization import MediaSubTeamForm, MediaSubTeamMembershipForm
from website.forms.ministry_manual import MinistryManualForm
from website.models import Ministry, MinistryMembership, MediaSubTeam, MediaSubTeamMembership, MinistryManual
from website.services.ministry_organization import media_ministry
from website.views.media_planning.mixins import _get_media_membership


def organization_admin(user):
    # Same rule as StaffRequiredMixin; ministry leaders manage their own ministry.
    return user.is_staff or user.user_type == 'admin'


class MinistryOrganizationIndex(LoginRequiredMixin, View):
    login_url = 'member_login'

    def get(self, request):
        member = getattr(request.user, 'member', None)
        if member and _get_media_membership(member):
            return redirect('media_organization')
        return redirect('member_dashboard')


class MinistryOrganizationMixin(LoginRequiredMixin):
    login_url = 'member_login'
    manage_only = False

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.ministry = get_object_or_404(Ministry, pk=kwargs['ministry_id'])
        self.can_manage = organization_admin(request.user)
        membership = MinistryMembership.objects.filter(
            ministry=self.ministry, member__user=request.user,
            member__is_active=True, member__deleted__isnull=True, is_active=True,
        ).first()
        if not self.can_manage:
            if not self.ministry.is_active or not membership:
                raise PermissionDenied
            media = media_ministry()
            if media and self.ministry.pk != media.pk:
                raise PermissionDenied
            if membership.role != 'leader':
                raise PermissionDenied
            self.can_manage = True
        if self.manage_only and not self.can_manage:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def context(self, **kwargs):
        return {'ministry': self.ministry, 'can_manage': self.can_manage, **kwargs}

    def team(self, pk):
        qs = MediaSubTeam.objects.filter(ministry=self.ministry)
        if not self.can_manage:
            qs = qs.filter(is_active=True)
        return get_object_or_404(qs, pk=pk)

    def teams_for_list(self):
        active_memberships = MediaSubTeamMembership.objects.filter(
            is_active=True,
            deleted__isnull=True,
            member__is_active=True,
            member__deleted__isnull=True,
        ).select_related('member').prefetch_related('roles')
        teams = MediaSubTeam.objects.filter(ministry=self.ministry)
        if not self.can_manage:
            teams = teams.filter(is_active=True)
        return teams.select_related('leader').prefetch_related(
            Prefetch('memberships', queryset=active_memberships),
        ).annotate(member_count=Count(
            'memberships',
            filter=Q(
                memberships__is_active=True,
                memberships__deleted__isnull=True,
                memberships__member__is_active=True,
                memberships__member__deleted__isnull=True,
            ),
        )).order_by('name')

    @staticmethod
    def add_member_form_prefix(team_pk):
        return f'add-member-{team_pk}'

    def add_member_forms_for(self, teams, overrides=None):
        overrides = overrides or {}
        return {
            team.pk: overrides.get(
                team.pk,
                MediaSubTeamMembershipForm(prefix=self.add_member_form_prefix(team.pk), sub_team=team),
            )
            for team in teams
        }

    def render_team_list(self, request, **extra):
        teams = self.teams_for_list()
        create_form = extra.pop('create_form', None)
        if create_form is None and self.can_manage:
            create_form = MediaSubTeamForm(ministry=self.ministry)
        add_member_forms = extra.pop('add_member_forms', None)
        if add_member_forms is None and self.can_manage:
            add_member_forms = self.add_member_forms_for(teams)
        add_member_team_id = extra.pop(
            'add_member_team_id',
            request.GET.get('add_member') if request.GET.get('add_member', '').isdigit() else None,
        )
        if add_member_team_id is not None:
            add_member_team_id = int(add_member_team_id)
        if add_member_forms:
            for team in teams:
                team.member_form = add_member_forms.get(team.pk)
        return render(request, 'member/ministry_organization/teams.html', self.context(
            teams=teams,
            section='teams',
            create_form=create_form,
            create_mode=extra.pop('create_mode', request.GET.get('create') == '1'),
            add_member_team_id=add_member_team_id,
            **extra,
        ))

    def manuals(self):
        qs = MinistryManual.objects.filter(ministry=self.ministry).select_related('sub_team', 'created_by')
        return qs if self.can_manage else qs.filter(is_active=True)


class MinistryTeamList(MinistryOrganizationMixin, View):
    def get(self, request, ministry_id):
        return self.render_team_list(request)


class MinistryTeamEdit(MinistryOrganizationMixin, View):
    manage_only = True

    def get(self, request, ministry_id, pk=None):
        if pk is None:
            return redirect(f"{reverse('ministry_team_list', args=[ministry_id])}?create=1")
        team = self.team(pk)
        form = MediaSubTeamForm(instance=team, ministry=self.ministry)
        return self.render_form(request, form, team)

    def render_form(self, request, form, team):
        return render(request, 'member/ministry_organization/form.html', self.context(
            form=form, title='Editar equipe' if team else 'Nova equipe', section='teams',
            back_url='ministry_team_list',
        ))

    def post(self, request, ministry_id, pk=None):
        team = self.team(pk) if pk else None
        form = MediaSubTeamForm(request.POST, instance=team, ministry=self.ministry)
        if form.is_valid():
            team = form.save()
            messages.success(request, 'Equipe salva.')
            return redirect('ministry_team_detail', ministry_id=ministry_id, pk=team.pk)
        if pk is None:
            return self.render_team_list(request, create_form=form, create_mode=True)
        return self.render_form(request, form, team)


class MinistryTeamDetail(MinistryOrganizationMixin, View):
    def render_team_detail(self, request, team, **extra):
        memberships = team.memberships.filter(
            is_active=True, member__is_active=True, member__deleted__isnull=True,
        ).select_related('member').prefetch_related('roles')
        add_member_form = extra.pop('add_member_form', None)
        if add_member_form is None and self.can_manage:
            add_member_form = MediaSubTeamMembershipForm(
                prefix=self.add_member_form_prefix(team.pk),
                sub_team=team,
            )
        add_member_open = extra.pop('add_member_open', request.GET.get('add_member') == '1')
        member_count = memberships.count()
        subtitle_parts = [f'{member_count} membro{"s" if member_count != 1 else ""}']
        if team.leader:
            subtitle_parts.append(f'Líder: {team.leader.name}')
        return render(request, 'member/ministry_organization/team.html', self.context(
            team=team,
            memberships=memberships,
            manuals=self.manuals().filter(sub_team=team),
            section='teams',
            team_detail_subtitle=' · '.join(subtitle_parts),
            add_member_form=add_member_form,
            add_member_open=add_member_open,
            **extra,
        ))

    def get(self, request, ministry_id, pk):
        return self.render_team_detail(request, self.team(pk))


class MinistryTeamMemberEdit(MinistryOrganizationMixin, View):
    manage_only = True

    def render_form(self, request, form, team):
        return render(request, 'member/ministry_organization/member_form.html', self.context(
            form=form, team=team, section='teams',
        ))

    def get(self, request, ministry_id, pk, membership_id=None):
        team = self.team(pk)
        membership = get_object_or_404(MediaSubTeamMembership, pk=membership_id, sub_team=team) if membership_id else None
        return self.render_form(request, MediaSubTeamMembershipForm(instance=membership, sub_team=team), team)

    @transaction.atomic
    def post(self, request, ministry_id, pk, membership_id=None):
        team = self.team(pk)
        membership = get_object_or_404(MediaSubTeamMembership, pk=membership_id, sub_team=team) if membership_id else None
        # Restore the same participation rather than colliding with its unique key.
        return_to = request.POST.get('return_to', '')
        from_modal = return_to in ('teams', 'detail')
        prefix = self.add_member_form_prefix(pk) if from_modal else None
        if membership is None:
            member_key = f'{prefix}-member' if prefix else 'member'
            member_id = request.POST.get(member_key, '')
            if member_id.isdigit():
                membership = MediaSubTeamMembership.all_objects.filter(sub_team=team, member_id=member_id).first()
        form = MediaSubTeamMembershipForm(
            request.POST, instance=membership, sub_team=team, prefix=prefix,
        )
        if form.is_valid():
            obj = form.save(commit=False)
            if from_modal and membership_id is None:
                obj.is_active = True
            obj.deleted = None
            obj.deleted_by_cascade = False
            obj.save()
            form.save_m2m()
            messages.success(request, 'Participação salva.')
            if not obj.is_active:
                messages.warning(request, 'As atribuições existentes foram preservadas. Revise os responsáveis das demandas em andamento.')
            if return_to == 'teams':
                return redirect('ministry_team_list', ministry_id=ministry_id)
            return redirect('ministry_team_detail', ministry_id=ministry_id, pk=pk)
        if return_to == 'teams' and membership_id is None:
            teams = self.teams_for_list()
            return self.render_team_list(
                request,
                add_member_forms=self.add_member_forms_for(teams, overrides={team.pk: form}),
                add_member_team_id=team.pk,
            )
        if return_to == 'detail' and membership_id is None:
            return self.render_team_detail(request, team, add_member_form=form, add_member_open=True)
        return self.render_form(request, form, team)


class MinistryTeamMemberRemove(MinistryOrganizationMixin, View):
    manage_only = True

    def post(self, request, ministry_id, pk, membership_id):
        team = self.team(pk)
        membership = get_object_or_404(MediaSubTeamMembership, pk=membership_id, sub_team=team)
        membership.is_active = False
        membership.save(update_fields=['is_active', 'update_at'])
        messages.success(request, 'Participação na equipe desativada.')
        messages.warning(request, 'As atribuições existentes foram preservadas. Revise os responsáveis das demandas em andamento.')
        return redirect('ministry_team_detail', ministry_id=ministry_id, pk=pk)


class MinistryManualList(MinistryOrganizationMixin, View):
    def get(self, request, ministry_id):
        teams = MediaSubTeam.objects.filter(ministry=self.ministry)
        if not self.can_manage:
            teams = teams.filter(is_active=True)
        manuals = self.manuals()
        selected = request.GET.get('team', '')
        if selected:
            team = get_object_or_404(teams, pk=selected) if selected.isdigit() else None
            manuals = manuals.filter(sub_team=team) if team else manuals.none()
        query = request.GET.get('q', '').strip()
        if query:
            manuals = manuals.filter(Q(title__icontains=query) | Q(summary__icontains=query))
        return render(request, 'member/ministry_organization/manuals.html', self.context(
            manuals=manuals, teams=teams, selected_team=selected, query=query, section='manuals',
        ))


class MinistryManualDetail(MinistryOrganizationMixin, View):
    def get(self, request, ministry_id, pk):
        manual = get_object_or_404(self.manuals(), pk=pk)
        return render(request, 'member/ministry_organization/manual.html', self.context(manual=manual, section='manuals'))


class MinistryManualEdit(MinistryOrganizationMixin, View):
    manage_only = True

    def render_form(self, request, form, manual):
        return render(request, 'member/ministry_organization/form.html', self.context(
            form=form, title='Editar manual' if manual else 'Novo manual', section='manuals',
            back_url='ministry_manual_list',
        ))

    def get(self, request, ministry_id, pk=None):
        manual = get_object_or_404(self.manuals(), pk=pk) if pk else None
        initial = {}
        if request.GET.get('team', '').isdigit():
            initial['sub_team'] = self.team(request.GET['team']).pk
        form = MinistryManualForm(instance=manual, ministry=self.ministry, initial=initial)
        return self.render_form(request, form, manual)

    def post(self, request, ministry_id, pk=None):
        manual = get_object_or_404(self.manuals(), pk=pk) if pk else None
        form = MinistryManualForm(request.POST, instance=manual, ministry=self.ministry)
        if form.is_valid():
            obj = form.save(commit=False)
            if not obj.pk:
                obj.created_by = request.user
            obj.save()
            messages.success(request, 'Manual salvo.')
            return redirect('ministry_manual_detail', ministry_id=ministry_id, pk=obj.pk)
        return self.render_form(request, form, manual)


class MinistryManualArchive(MinistryOrganizationMixin, View):
    manage_only = True

    def post(self, request, ministry_id, pk):
        manual = get_object_or_404(self.manuals(), pk=pk)
        manual.is_active = False
        manual.save(update_fields=['is_active', 'update_at'])
        messages.success(request, 'Manual arquivado. Ele pode ser reativado na edição.')
        return redirect('ministry_manual_list', ministry_id=ministry_id)


class MinistryManualPreview(MinistryOrganizationMixin, View):
    manage_only = True

    def post(self, request, ministry_id):
        from website.utils.manual_content import sanitize_manual_content
        content = request.POST.get('content', '')
        if len(content) > 200000:
            return JsonResponse({'error': 'O conteúdo deve ter até 200.000 caracteres.'}, status=400)
        return JsonResponse({'html': sanitize_manual_content(content)})
