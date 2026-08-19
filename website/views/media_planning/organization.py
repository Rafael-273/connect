from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from ...forms.media_organization import (
    MediaLeadershipItemForm,
    MediaResourceCredentialForm,
    MediaResourceForm,
    MediaRoleForm,
    MediaSubTeamForm,
    MediaSubTeamMembershipForm,
)
from ...models.media_organization import (
    MediaCredentialAccessLog,
    MediaLeadershipItem,
    MediaResource,
    MediaResourceCredential,
    MediaRole,
    MediaSubTeam,
    MediaSubTeamMembership,
)
from ...utils.media_vault import decrypt_secret, encrypt_secret
from .mixins import MediaLeaderRequiredMixin, MediaMemberRequiredMixin


class MediaOrganizationDashboardView(MediaMemberRequiredMixin, View):
    template_name = 'member/media_planning/organization_dashboard.html'

    def get(self, request):
        sub_teams = MediaSubTeam.objects.filter(is_active=True).prefetch_related('memberships')
        leadership_open = MediaLeadershipItem.objects.filter(
            status__in=['open', 'in_progress']
        ).select_related('responsible')[:8]
        resources = MediaResource.objects.filter(is_active=True).select_related('responsible')[:8]
        ctx = {
            **self._nav_context(),
            'sub_teams': sub_teams,
            'leadership_open': leadership_open,
            'resources': resources,
            'sub_team_count': sub_teams.count(),
            'leadership_count': MediaLeadershipItem.objects.filter(
                status__in=['open', 'in_progress']
            ).count(),
            'resource_count': MediaResource.objects.filter(is_active=True).count(),
        }
        return render(request, self.template_name, ctx)


# ─── Subequipes ───────────────────────────────────────────────────────────────

class MediaSubTeamListView(MediaMemberRequiredMixin, View):
    template_name = 'member/media_planning/subteam_list.html'

    def get(self, request):
        teams = MediaSubTeam.objects.prefetch_related(
            'memberships__member', 'memberships__roles'
        ).order_by('name')
        ctx = {**self._nav_context(), 'sub_teams': teams}
        return render(request, self.template_name, ctx)


class MediaSubTeamCreateView(MediaLeaderRequiredMixin, View):
    template_name = 'member/media_planning/subteam_form.html'

    def get(self, request):
        ctx = {**self._nav_context(), 'form': MediaSubTeamForm(), 'action': 'create'}
        return render(request, self.template_name, ctx)

    def post(self, request):
        form = MediaSubTeamForm(request.POST)
        if form.is_valid():
            team = form.save()
            messages.success(request, f'Equipe "{team.name}" criada!')
            return redirect('media_subteam_detail', pk=team.pk)
        ctx = {**self._nav_context(), 'form': form, 'action': 'create'}
        return render(request, self.template_name, ctx)


class MediaSubTeamDetailView(MediaMemberRequiredMixin, View):
    template_name = 'member/media_planning/subteam_detail.html'

    def get(self, request, pk):
        team = get_object_or_404(
            MediaSubTeam.objects.select_related('leader'),
            pk=pk,
        )
        memberships = team.memberships.filter(is_active=True).select_related(
            'member'
        ).prefetch_related('roles')
        ctx = {
            **self._nav_context(),
            'sub_team': team,
            'memberships': memberships,
            'member_form': MediaSubTeamMembershipForm(sub_team=team),
        }
        return render(request, self.template_name, ctx)


class MediaSubTeamUpdateView(MediaLeaderRequiredMixin, View):
    template_name = 'member/media_planning/subteam_form.html'

    def get(self, request, pk):
        team = get_object_or_404(MediaSubTeam, pk=pk)
        ctx = {
            **self._nav_context(),
            'form': MediaSubTeamForm(instance=team),
            'sub_team': team,
            'action': 'edit',
        }
        return render(request, self.template_name, ctx)

    def post(self, request, pk):
        team = get_object_or_404(MediaSubTeam, pk=pk)
        form = MediaSubTeamForm(request.POST, instance=team)
        if form.is_valid():
            form.save()
            messages.success(request, f'Equipe "{team.name}" atualizada!')
            return redirect('media_subteam_detail', pk=team.pk)
        ctx = {
            **self._nav_context(),
            'form': form,
            'sub_team': team,
            'action': 'edit',
        }
        return render(request, self.template_name, ctx)


class MediaSubTeamMemberAddView(MediaLeaderRequiredMixin, View):
    def post(self, request, pk):
        team = get_object_or_404(MediaSubTeam, pk=pk)
        form = MediaSubTeamMembershipForm(request.POST, sub_team=team)
        if form.is_valid():
            membership = form.save(commit=False)
            membership.sub_team = team
            membership.save()
            form.save_m2m()
            messages.success(request, f'{membership.member.name} adicionado à equipe!')
        else:
            messages.error(request, 'Erro ao adicionar membro. Verifique os campos.')
        return redirect('media_subteam_detail', pk=pk)


class MediaSubTeamMemberUpdateView(MediaLeaderRequiredMixin, View):
    template_name = 'member/media_planning/subteam_member_form.html'

    def get(self, request, pk):
        membership = get_object_or_404(
            MediaSubTeamMembership.objects.select_related('sub_team', 'member'),
            pk=pk,
        )
        ctx = {
            **self._nav_context(),
            'membership': membership,
            'sub_team': membership.sub_team,
            'form': MediaSubTeamMembershipForm(instance=membership, sub_team=membership.sub_team),
        }
        return render(request, self.template_name, ctx)

    def post(self, request, pk):
        membership = get_object_or_404(
            MediaSubTeamMembership.objects.select_related('sub_team'),
            pk=pk,
        )
        form = MediaSubTeamMembershipForm(
            request.POST, instance=membership, sub_team=membership.sub_team
        )
        if form.is_valid():
            form.save()
            messages.success(request, 'Participação atualizada!')
            return redirect('media_subteam_detail', pk=membership.sub_team_id)
        ctx = {
            **self._nav_context(),
            'membership': membership,
            'sub_team': membership.sub_team,
            'form': form,
        }
        return render(request, self.template_name, ctx)


class MediaSubTeamMemberRemoveView(MediaLeaderRequiredMixin, View):
    def post(self, request, pk):
        membership = get_object_or_404(MediaSubTeamMembership, pk=pk)
        team_pk = membership.sub_team_id
        name = membership.member.name
        membership.delete()
        messages.success(request, f'{name} removido da equipe.')
        return redirect('media_subteam_detail', pk=team_pk)


# ─── Funções ──────────────────────────────────────────────────────────────────

class MediaRoleListView(MediaMemberRequiredMixin, View):
    template_name = 'member/media_planning/role_list.html'

    def get(self, request):
        roles = MediaRole.objects.select_related('sub_team').order_by('name')
        ctx = {**self._nav_context(), 'roles': roles}
        return render(request, self.template_name, ctx)


class MediaRoleCreateView(MediaLeaderRequiredMixin, View):
    template_name = 'member/media_planning/role_form.html'

    def get(self, request):
        ctx = {**self._nav_context(), 'form': MediaRoleForm(), 'action': 'create'}
        return render(request, self.template_name, ctx)

    def post(self, request):
        form = MediaRoleForm(request.POST)
        if form.is_valid():
            role = form.save()
            messages.success(request, f'Função "{role.name}" criada!')
            return redirect('media_role_list')
        ctx = {**self._nav_context(), 'form': form, 'action': 'create'}
        return render(request, self.template_name, ctx)


# ─── Liderança ────────────────────────────────────────────────────────────────

class MediaLeadershipListView(MediaMemberRequiredMixin, View):
    template_name = 'member/media_planning/leadership_list.html'

    def get(self, request):
        items = MediaLeadershipItem.objects.select_related('responsible').order_by(
            '-priority', '-created_at'
        )
        ctx = {**self._nav_context(), 'items': items}
        return render(request, self.template_name, ctx)


class MediaLeadershipCreateView(MediaLeaderRequiredMixin, View):
    template_name = 'member/media_planning/leadership_form.html'

    def get(self, request):
        ctx = {**self._nav_context(), 'form': MediaLeadershipItemForm(), 'action': 'create'}
        return render(request, self.template_name, ctx)

    def post(self, request):
        form = MediaLeadershipItemForm(request.POST)
        if form.is_valid():
            item = form.save()
            messages.success(request, f'Item "{item.title}" registrado!')
            return redirect('media_leadership_list')
        ctx = {**self._nav_context(), 'form': form, 'action': 'create'}
        return render(request, self.template_name, ctx)


class MediaLeadershipUpdateView(MediaLeaderRequiredMixin, View):
    template_name = 'member/media_planning/leadership_form.html'

    def get(self, request, pk):
        item = get_object_or_404(MediaLeadershipItem, pk=pk)
        ctx = {
            **self._nav_context(),
            'form': MediaLeadershipItemForm(instance=item),
            'item': item,
            'action': 'edit',
        }
        return render(request, self.template_name, ctx)

    def post(self, request, pk):
        item = get_object_or_404(MediaLeadershipItem, pk=pk)
        form = MediaLeadershipItemForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            messages.success(request, f'Item "{item.title}" atualizado!')
            return redirect('media_leadership_list')
        ctx = {
            **self._nav_context(),
            'form': form,
            'item': item,
            'action': 'edit',
        }
        return render(request, self.template_name, ctx)


# ─── Recursos ─────────────────────────────────────────────────────────────────

class MediaResourceListView(MediaMemberRequiredMixin, View):
    template_name = 'member/media_planning/resource_list.html'

    def get(self, request):
        resources = MediaResource.objects.select_related(
            'responsible'
        ).prefetch_related('access_members').order_by('name')
        ctx = {**self._nav_context(), 'resources': resources}
        return render(request, self.template_name, ctx)


class MediaResourceCreateView(MediaLeaderRequiredMixin, View):
    template_name = 'member/media_planning/resource_form.html'

    def get(self, request):
        ctx = {**self._nav_context(), 'form': MediaResourceForm(), 'action': 'create'}
        return render(request, self.template_name, ctx)

    def post(self, request):
        form = MediaResourceForm(request.POST)
        if form.is_valid():
            resource = form.save()
            form.save_m2m()
            messages.success(request, f'Recurso "{resource.name}" criado!')
            return redirect('media_resource_detail', pk=resource.pk)
        ctx = {**self._nav_context(), 'form': form, 'action': 'create'}
        return render(request, self.template_name, ctx)


class MediaResourceDetailView(MediaMemberRequiredMixin, View):
    template_name = 'member/media_planning/resource_detail.html'

    def get(self, request, pk):
        resource = get_object_or_404(
            MediaResource.objects.select_related('responsible').prefetch_related('access_members'),
            pk=pk,
        )
        credential = getattr(resource, 'credential', None)
        ctx = {
            **self._nav_context(),
            'resource': resource,
            'credential': credential,
            'has_credential': credential and bool(credential.encrypted_password),
            'credential_form': MediaResourceCredentialForm(),
        }
        return render(request, self.template_name, ctx)


class MediaResourceUpdateView(MediaLeaderRequiredMixin, View):
    template_name = 'member/media_planning/resource_form.html'

    def get(self, request, pk):
        resource = get_object_or_404(MediaResource, pk=pk)
        ctx = {
            **self._nav_context(),
            'form': MediaResourceForm(instance=resource),
            'resource': resource,
            'action': 'edit',
        }
        return render(request, self.template_name, ctx)

    def post(self, request, pk):
        resource = get_object_or_404(MediaResource, pk=pk)
        form = MediaResourceForm(request.POST, instance=resource)
        if form.is_valid():
            form.save()
            form.save_m2m()
            messages.success(request, f'Recurso "{resource.name}" atualizado!')
            return redirect('media_resource_detail', pk=resource.pk)
        ctx = {
            **self._nav_context(),
            'form': form,
            'resource': resource,
            'action': 'edit',
        }
        return render(request, self.template_name, ctx)


class MediaResourceCredentialUpdateView(MediaLeaderRequiredMixin, View):
    def post(self, request, pk):
        resource = get_object_or_404(MediaResource, pk=pk)
        form = MediaResourceCredentialForm(request.POST)
        if not form.is_valid():
            messages.error(request, 'Erro ao salvar credenciais.')
            return redirect('media_resource_detail', pk=pk)

        credential, _ = MediaResourceCredential.objects.get_or_create(resource=resource)
        password = form.cleaned_data.get('password', '')
        notes = form.cleaned_data.get('sensitive_notes', '')

        if password:
            credential.encrypted_password = encrypt_secret(password)
        if notes:
            credential.encrypted_notes = encrypt_secret(notes)
        credential.last_updated_by = request.user
        credential.save()

        MediaCredentialAccessLog.objects.create(
            credential=credential,
            user=request.user,
            action='update',
        )
        messages.success(request, 'Credenciais atualizadas com segurança.')
        return redirect('media_resource_detail', pk=pk)


class MediaResourceCredentialRevealView(MediaLeaderRequiredMixin, View):
    def post(self, request, pk):
        resource = get_object_or_404(MediaResource, pk=pk)
        try:
            credential = resource.credential
        except MediaResourceCredential.DoesNotExist:
            return JsonResponse({'error': 'Sem credenciais'}, status=404)

        if not credential.encrypted_password:
            return JsonResponse({'error': 'Senha não cadastrada'}, status=404)

        MediaCredentialAccessLog.objects.create(
            credential=credential,
            user=request.user,
            action='view',
        )
        return JsonResponse({
            'password': decrypt_secret(credential.encrypted_password),
            'notes': decrypt_secret(credential.encrypted_notes) if credential.encrypted_notes else '',
        })
