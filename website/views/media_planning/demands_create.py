import datetime

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views import View

from ...forms.media_planning import MediaDemandQuickForm, MediaEventQuickForm, MediaEventTypeQuickForm
from ...models.event import Event
from ...models.media_content import MediaContent
from ...models.media_event_type import MediaEventType, MediaPlanningTemplate
from ...models.media_month_plan import MediaMonthPlan
from ...services.demands_hub import hub_redirect_url, parse_demand_assignments, sync_content_assignments
from ...services.media_planning import apply_template_to_event, get_template_for_event
from .mixins import MediaLeaderRequiredMixin
from .month_plan import DEFAULT_EVENT_BANNER


def _date_to_datetime(value):
    if not value:
        return None
    naive = datetime.datetime.combine(value, datetime.time(hour=12, minute=0))
    return timezone.make_aware(naive) if timezone.is_naive(naive) else naive


class MediaDemandQuickCreateView(MediaLeaderRequiredMixin, View):
    """Cria demanda/conteúdo a partir do hub."""

    def post(self, request):
        form = MediaDemandQuickForm(request.POST, prefix='demand')
        if not form.is_valid():
            messages.error(request, 'Verifique os campos da demanda.')
            return redirect(hub_redirect_url(request, create='demand'))

        content = form.save(commit=False)
        content.status = 'pending'
        content.priority = 'medium'
        content.event = None
        content.responsible = None
        content.due_date = None
        content.publication_date = None
        content.save()

        assignments = parse_demand_assignments(request.POST, prefix='demand')
        sync_content_assignments(content, assignments)

        messages.success(request, f'Demanda "{content.title}" criada!')
        return redirect(hub_redirect_url(request, selected=f'free-{content.pk}'))


class MediaDemandQuickUpdateView(MediaLeaderRequiredMixin, View):
    """Atualiza demanda/conteúdo a partir do modal do hub."""

    def post(self, request, pk):
        content = get_object_or_404(MediaContent, pk=pk)
        form = MediaDemandQuickForm(request.POST, instance=content, prefix='edit')
        if not form.is_valid():
            messages.error(request, 'Verifique os campos da demanda.')
            if content.event_id:
                return redirect(hub_redirect_url(request, selected=f'event-{content.event_id}', edit='1'))
            return redirect(hub_redirect_url(request, selected=f'free-{pk}', edit='1'))

        content = form.save(commit=False)
        content.save()

        assignments = parse_demand_assignments(request.POST, prefix='edit')
        sync_content_assignments(content, assignments)

        messages.success(request, f'Demanda "{content.title}" atualizada!')
        if content.event_id:
            return redirect(hub_redirect_url(request, selected=f'event-{content.event_id}'))
        return redirect(hub_redirect_url(request, selected=f'free-{content.pk}'))


class MediaEventQuickCreateView(MediaLeaderRequiredMixin, View):
    """Cria evento a partir do hub, com template opcional."""

    def post(self, request):
        form = MediaEventQuickForm(request.POST, prefix='event')
        if not form.is_valid():
            messages.error(request, 'Verifique os campos do evento.')
            return redirect(hub_redirect_url(request, create='event'))

        data = form.cleaned_data
        event_date = data['event_date']
        event = Event(
            title=data['title'],
            description=data['title'],
            event_date=event_date,
            event_time=data.get('event_time'),
            location=data.get('location') or '',
            display_start=event_date,
            display_end=event_date,
            event_type=data.get('event_type'),
            banner=DEFAULT_EVENT_BANNER,
        )
        event.save()

        plan, _ = MediaMonthPlan.objects.get_or_create(
            year=event_date.year,
            month=event_date.month,
        )
        plan.events.add(event)

        template_count = 0
        if data.get('apply_template') and event.event_type_id:
            template = get_template_for_event(event)
            if template:
                items = list(template.items.select_related('default_sub_team', 'default_role'))
                if items:
                    apply_template_to_event(event, items, month_plan=plan)
                    template_count = len(items)

        if template_count:
            messages.success(
                request,
                f'Evento "{event.title}" criado com {template_count} conteúdo(s) do template!',
            )
        else:
            messages.success(request, f'Evento "{event.title}" criado!')

        return redirect(hub_redirect_url(request, selected=f'event-{event.pk}'))


class MediaEventTypeQuickCreateView(MediaLeaderRequiredMixin, View):
    """Cria tipo de evento + template a partir do modal de novo evento."""

    def post(self, request):
        form = MediaEventTypeQuickForm(request.POST, prefix='event_type')
        if not form.is_valid():
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                errors = {k: v[0] for k, v in form.errors.items()}
                return JsonResponse({'errors': errors}, status=400)
            messages.error(request, 'Verifique os campos do tipo de evento.')
            return redirect(hub_redirect_url(request, create='event'))

        data = form.cleaned_data
        event_type = MediaEventType.objects.create(
            name=data['name'],
            description=data.get('description', ''),
            is_active=True,
        )
        template = MediaPlanningTemplate.objects.create(
            event_type=event_type,
            name=event_type.name,
            description=event_type.description,
            is_active=True,
        )

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            from django.urls import reverse
            return JsonResponse({
                'id': event_type.pk,
                'name': event_type.name,
                'template_id': template.pk,
                'template_url': reverse('media_template_detail', args=[template.pk]),
            })

        messages.success(request, f'Tipo "{event_type.name}" criado! Configure as demandas do template.')
        return redirect('media_template_detail', pk=template.pk)
