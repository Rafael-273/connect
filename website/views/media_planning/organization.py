from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from ...forms.media_organization import (
    MediaLeadershipItemForm,
    MediaResourceCredentialForm,
    MediaResourceForm,
    MediaRoleForm,
)
from ...models.media_organization import (
    MediaCredentialAccessLog,
    MediaLeadershipItem,
    MediaResource,
    MediaResourceCredential,
    MediaSubTeamMembership,
)
from ...utils.media_vault import decrypt_secret, encrypt_secret
from website.services.ministry_organization import media_teams, scoped_roles
from website.views import ministry_organization as ministry_org
from .mixins import MediaLeaderRequiredMixin, MediaMemberRequiredMixin


class MediaOrganizationDashboardView(MediaMemberRequiredMixin, View):
    template_name = 'member/media_planning/organization_dashboard.html'

    def get(self, request):
        sub_teams = media_teams().filter(is_active=True).select_related('leader')
        leadership_open = MediaLeadershipItem.objects.filter(
            status__in=['open', 'in_progress']
        ).select_related('responsible')[:8]
        resources = MediaResource.objects.filter(is_active=True).select_related('responsible')[:8]
        ctx = {
            **self._nav_context(),
            'sub_teams': sub_teams,
            'ministry': self.media_membership.ministry,
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

class MediaOrganizationBridge(MediaMemberRequiredMixin, View):
    """Keep bookmarked media URLs using the same generic organization views."""
    target_view = None
    target_url = None
    membership_route = False

    def route_kwargs(self, pk=None):
        kwargs = {'ministry_id': self.media_membership.ministry_id}
        if pk is not None:
            if self.membership_route:
                membership = get_object_or_404(
                    MediaSubTeamMembership.objects.select_related('sub_team'),
                    pk=pk, sub_team__ministry_id=kwargs['ministry_id'],
                )
                kwargs.update(pk=membership.sub_team_id, membership_id=membership.pk)
            else:
                get_object_or_404(media_teams(), pk=pk)
                kwargs['pk'] = pk
        return kwargs

    def get(self, request, pk=None):
        return redirect(self.target_url, **self.route_kwargs(pk))

    def post(self, request, pk=None):
        return self.target_view.as_view()(request, **self.route_kwargs(pk))


class MediaSubTeamListView(MediaOrganizationBridge):
    target_view = ministry_org.MinistryTeamList
    target_url = 'ministry_team_list'


class MediaSubTeamCreateView(MediaOrganizationBridge):
    target_view = ministry_org.MinistryTeamEdit
    target_url = 'ministry_team_create'


class MediaSubTeamDetailView(MediaOrganizationBridge):
    target_view = ministry_org.MinistryTeamDetail
    target_url = 'ministry_team_detail'


class MediaSubTeamUpdateView(MediaOrganizationBridge):
    target_view = ministry_org.MinistryTeamEdit
    target_url = 'ministry_team_edit'


class MediaSubTeamMemberAddView(MediaOrganizationBridge):
    target_view = ministry_org.MinistryTeamMemberEdit
    target_url = 'ministry_team_member_add'


class MediaSubTeamMemberUpdateView(MediaOrganizationBridge):
    target_view = ministry_org.MinistryTeamMemberEdit
    target_url = 'ministry_team_member_edit'
    membership_route = True


class MediaSubTeamMemberRemoveView(MediaOrganizationBridge):
    target_view = ministry_org.MinistryTeamMemberRemove
    target_url = 'ministry_team_member_remove'
    membership_route = True

    def get(self, request, pk=None):
        return self.http_method_not_allowed(request)


# ─── Funções ──────────────────────────────────────────────────────────────────

class MediaRoleListView(MediaMemberRequiredMixin, View):
    template_name = 'member/media_planning/role_list.html'

    def get(self, request):
        roles = scoped_roles(self.media_membership.ministry).select_related('sub_team').order_by('name')
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
