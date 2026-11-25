"""Media-only extensions to the shared ministry team screens."""
from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View

from website.forms.media_organization import MediaTeamActivityForm, MediaTeamServiceForm
from website.models import MediaSubTeam, MediaSubTeamMembership
from website.services.ministry_organization import media_ministry
from website.views.ministry_organization import MinistryOrganizationMixin


class MediaTeamConfigurationMixin(MinistryOrganizationMixin):
    manage_only = True

    def dispatch(self, request, *args, **kwargs):
        ministry = media_ministry()
        if not ministry or ministry.pk != kwargs['ministry_id']:
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class MediaTeamExperienceEdit(MediaTeamConfigurationMixin, View):
    def post(self, request, ministry_id, pk, membership_id):
        team = self.team(pk)
        membership = get_object_or_404(MediaSubTeamMembership, sub_team=team, pk=membership_id)
        field = forms.ChoiceField(choices=MediaSubTeamMembership.EXPERIENCE_CHOICES)
        try:
            level = field.clean(request.POST.get('experience_level'))
        except forms.ValidationError:
            messages.error(request, 'Selecione uma classificação válida.')
        else:
            membership.experience_level = level
            membership.save(update_fields=['experience_level', 'update_at'])
            messages.success(request, 'Classificação atualizada.')
        return redirect('ministry_team_detail', ministry_id=ministry_id, pk=pk)


class MediaTeamDefaultEdit(MediaTeamConfigurationMixin, View):
    kind = 'activity'

    def get_instance(self, team, item_id):
        manager = team.activities if self.kind == 'activity' else team.service_defaults
        return get_object_or_404(manager, pk=item_id) if item_id else None

    def form(self, team, instance, data=None):
        form_class = MediaTeamActivityForm if self.kind == 'activity' else MediaTeamServiceForm
        return form_class(
            data, sub_team=team, instance=instance,
            compact=self.is_modal_request(self.request),
        )

    def is_modal_request(self, request):
        return request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    def render_form(self, request, team, form, instance=None, status=200):
        context = self.context(
            team=team, form=form, section='teams',
            title='Atividade da equipe' if self.kind == 'activity' else 'Atuação por culto',
            is_service=self.kind == 'service',
            is_edit=instance is not None,
        )
        template = (
            'member/ministry_organization/team_default_form_modal.html'
            if self.is_modal_request(request)
            else 'member/ministry_organization/team_default_form.html'
        )
        return render(request, template, context, status=status)

    def get(self, request, ministry_id, pk, item_id=None):
        team = self.team(pk)
        instance = self.get_instance(team, item_id)
        return self.render_form(request, team, self.form(team, instance), instance)

    @transaction.atomic
    def post(self, request, ministry_id, pk, item_id=None):
        team = self.team(pk)
        MediaSubTeam.objects.select_for_update().get(pk=team.pk)
        instance = self.get_instance(team, item_id)
        if instance:
            # Serialize edits, including changes to the selected activity links.
            type(instance).objects.select_for_update().get(pk=instance.pk)
        form = self.form(team, instance, request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Configuração salva. Escalas existentes foram preservadas.')
            if self.is_modal_request(request):
                return JsonResponse({'success': True, 'redirect_url': reverse(
                    'ministry_team_detail', kwargs={'ministry_id': ministry_id, 'pk': pk},
                )})
            return redirect('ministry_team_detail', ministry_id=ministry_id, pk=pk)
        return self.render_form(
            request, team, form, instance,
            status=400 if self.is_modal_request(request) else 200,
        )


class MediaTeamServiceEdit(MediaTeamDefaultEdit):
    kind = 'service'
