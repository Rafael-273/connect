from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from ...forms.media_organization import EventTypeAssignForm
from ...models.event import Event
from ...models.media_event_type import MediaPlanningTemplateItem
from ...services.media_planning import (
    apply_template_to_event,
    get_available_template_items,
    get_template_for_event,
)
from .mixins import MediaLeaderRequiredMixin


class MediaEventSetTypeView(MediaLeaderRequiredMixin, View):
    template_name = 'member/media_planning/event_set_type.html'

    def get(self, request, event_pk):
        event = get_object_or_404(Event, pk=event_pk)
        ctx = {
            **self._nav_context(),
            'event': event,
            'form': EventTypeAssignForm(instance=event),
        }
        return render(request, self.template_name, ctx)

    def post(self, request, event_pk):
        event = get_object_or_404(Event, pk=event_pk)
        form = EventTypeAssignForm(request.POST, instance=event)
        if form.is_valid():
            form.save()
            messages.success(request, 'Tipo do evento atualizado!')
            return redirect('media_event_contents', event_pk=event.pk)
        ctx = {**self._nav_context(), 'event': event, 'form': form}
        return render(request, self.template_name, ctx)


class MediaApplyTemplateView(MediaLeaderRequiredMixin, View):
    template_name = 'member/media_planning/apply_template.html'

    def get(self, request, event_pk):
        event = get_object_or_404(Event.objects.select_related('event_type'), pk=event_pk)
        template = get_template_for_event(event)
        available_items = get_available_template_items(event)

        if not event.event_type_id:
            messages.warning(request, 'Defina o template do evento antes de aplicar as demandas sugeridas.')
            return redirect('media_event_set_type', event_pk=event.pk)

        if not template:
            messages.warning(
                request,
                f'Não existe template ativo para "{event.event_type.name}".',
            )
            return redirect('media_event_contents', event_pk=event.pk)

        ctx = {
            **self._nav_context(),
            'event': event,
            'template': template,
            'available_items': available_items,
        }
        return render(request, self.template_name, ctx)

    def post(self, request, event_pk):
        event = get_object_or_404(Event.objects.select_related('event_type'), pk=event_pk)
        template = get_template_for_event(event)
        if not template:
            messages.error(request, 'Template não encontrado.')
            return redirect('media_event_contents', event_pk=event.pk)

        selected_ids = request.POST.getlist('item_ids')
        if not selected_ids:
            messages.error(request, 'Selecione ao menos um item do template.')
            return redirect('media_apply_template', event_pk=event.pk)

        items = list(
            MediaPlanningTemplateItem.objects.filter(
                pk__in=selected_ids,
                template=template,
            ).select_related('default_sub_team', 'default_role')
        )
        month_plan_pk = request.POST.get('month_plan', '')
        month_plan = None
        if month_plan_pk:
            from ...models.media_month_plan import MediaMonthPlan
            try:
                month_plan = MediaMonthPlan.objects.get(pk=month_plan_pk)
            except MediaMonthPlan.DoesNotExist:
                pass

        created = apply_template_to_event(event, items, month_plan=month_plan)
        messages.success(
            request,
            f'{len(created)} demanda(s) criada(s) a partir do template. Você pode editá-las livremente.',
        )
        return redirect('media_event_contents', event_pk=event.pk)
