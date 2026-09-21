import datetime

from django.core.exceptions import ValidationError
from django.db import transaction

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from ...forms.media_planning import MediaDemandQuickForm, MediaEventQuickForm, MediaEventTypeQuickForm
from django.utils.dateparse import parse_date, parse_time

from ...models.event import Event, EventDate
from ...models.media_content import MediaContent
from ...models.media_event_type import MediaEventType, MediaPlanningTemplate
from ...models.media_month_plan import MediaMonthPlan
from ...services.event_media_integration import start_media_organization
from ...services.demands_hub import hub_redirect_url, parse_demand_assignments, sync_content_assignments
from ...services.media_planning import apply_template_to_event, get_template_for_event
from .mixins import MediaLeaderRequiredMixin, MediaMemberRequiredMixin
from .month_plan import DEFAULT_EVENT_BANNER


def _parse_extra_event_dates(post_data, prefix='event'):
    rows = []
    dates = post_data.getlist(f'{prefix}-extra_event_date')
    times = post_data.getlist(f'{prefix}-extra_event_time')
    for raw_date, raw_time in zip(dates, times):
        if not raw_date:
            continue
        parsed_date = parse_date(raw_date) if isinstance(raw_date, str) else raw_date
        if not parsed_date:
            continue
        parsed_time = parse_time(raw_time) if raw_time else None
        rows.append({'event_date': parsed_date, 'event_time': parsed_time})
    return rows


def _date_to_datetime(value):
    if not value:
        return None
    naive = datetime.datetime.combine(value, datetime.time(hour=12, minute=0))
    return timezone.make_aware(naive) if timezone.is_naive(naive) else naive


def _invalid_demand_response(view, request, form, assignments):
    from .content import MediaContentListView
    request.failed_demand_form = form
    from website.services.demands_hub import ASSIGNMENT_ROLE_PRESETS, decompress_assignment_offset
    failed_rows = []
    for row in assignments:
        offset_parts = decompress_assignment_offset(row.get('due_offset_days'))
        failed_rows.append({
            **row,
            'id': row.get('task_id', ''),
            'due_days': offset_parts['due_days'],
            'due_relation': offset_parts['due_relation'],
            'is_custom_role': row.get('role') not in ASSIGNMENT_ROLE_PRESETS,
        })
    request.failed_demand_assignments = failed_rows
    request.GET = request.GET.copy()
    if form.prefix == 'edit':
        request.GET['edit'] = '1'
        request.GET['selected'] = (
            f'event-{form.instance.event_id}' if form.instance.event_id else f'free-{form.instance.pk}'
        )
    else:
        request.GET['create'] = 'demand'
        event_id = request.POST.get('demand-event', '').strip()
        if event_id.isdigit():
            request.GET['selected'] = f'event-{event_id}'
    hub = MediaContentListView()
    hub.setup(request)
    hub.member = view.member
    hub.media_membership = view.media_membership
    response = hub.get(request)
    response.status_code = 400
    return response


class MediaDemandQuickCreateView(MediaMemberRequiredMixin, View):
    """Cria demanda/conteúdo a partir do hub."""

    def post(self, request):
        form = MediaDemandQuickForm(request.POST, prefix='demand')
        assignments = parse_demand_assignments(request.POST, prefix='demand')
        if not form.is_valid():
            return _invalid_demand_response(self, request, form, assignments)
        event = None
        event_id = request.POST.get('demand-event', '').strip()
        if event_id:
            if not event_id.isdigit():
                form.add_error(None, 'Evento inválido.')
                return _invalid_demand_response(self, request, form, assignments)
            event = Event.objects.filter(pk=int(event_id), is_recurring=False).first()
            if not event:
                form.add_error(None, 'Evento não encontrado.')
                return _invalid_demand_response(self, request, form, assignments)

        try:
            with transaction.atomic():
                content = form.save(commit=False)
                content.status = 'pending'
                content.priority = 'medium'
                content.event = event
                content.responsible = None
                content.due_date = None
                content.publication_date = None
                content.save()
                sync_content_assignments(content, assignments)
        except ValidationError as exc:
            form.instance.pk = None
            form.add_error(None, ' '.join(exc.messages))
            return _invalid_demand_response(self, request, form, assignments)

        messages.success(request, f'Demanda "{content.title}" criada!')
        if content.event_id:
            return redirect(hub_redirect_url(request, selected=f'event-{content.event_id}'))
        return redirect(hub_redirect_url(request, selected=f'free-{content.pk}'))


class MediaDemandQuickUpdateView(MediaMemberRequiredMixin, View):
    """Atualiza demanda/conteúdo a partir do modal do hub."""

    def post(self, request, pk):
        content = get_object_or_404(MediaContent, pk=pk)
        form = MediaDemandQuickForm(request.POST, instance=content, prefix='edit')
        assignments = parse_demand_assignments(request.POST, prefix='edit')
        if not form.is_valid():
            return _invalid_demand_response(self, request, form, assignments)
        try:
            with transaction.atomic():
                content = form.save(commit=False)
                content.save()
                sync_content_assignments(content, assignments)
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
            return _invalid_demand_response(self, request, form, assignments)

        messages.success(request, f'Demanda "{content.title}" atualizada!')
        if content.event_id:
            return redirect(hub_redirect_url(request, selected=f'event-{content.event_id}'))
        return redirect(hub_redirect_url(request, selected=f'free-{content.pk}'))


class MediaEventQuickCreateView(MediaMemberRequiredMixin, View):
    """Cria evento a partir do hub, com template opcional."""

    def invalid_response(self, request, form):
        from website.services.media_planning import template_preview_data
        return render(request, 'member/media_planning/event_form.html', {
            **self._nav_context(), 'form': form, 'template_previews': template_preview_data(),
        }, status=400)

    def get(self, request):
        from website.services.media_planning import template_preview_data
        return render(request, 'member/media_planning/event_form.html', {
            **self._nav_context(), 'form': MediaEventQuickForm(prefix='event'),
            'template_previews': template_preview_data(),
        })

    def post(self, request):
        form = MediaEventQuickForm(request.POST, prefix='event')
        if not form.is_valid():
            return self.invalid_response(request, form)

        data = form.cleaned_data
        event_date = data['event_date']
        extra_dates = _parse_extra_event_dates(request.POST, prefix='event')
        all_dates = [event_date, *[row['event_date'] for row in extra_dates]]
        span_start = min(all_dates)
        span_end = max(all_dates)
        event = Event(
            title=data['title'],
            description=data.get('description') or '',
            event_date=event_date,
            event_time=data.get('event_time'),
            end_date=span_end if len(all_dates) > 1 else data.get('end_date'),
            end_time=data.get('end_time'),
            location='',
            display_start=span_start,
            display_end=span_end,
            event_type=data.get('event_type'),
            banner=DEFAULT_EVENT_BANNER,
            institutional_status=Event.INSTITUTIONAL_STATUS_PENDING,
        )
        try:
            with transaction.atomic():
                event.save()
                start_media_organization(event, created_from_media=True)
                EventDate.objects.bulk_create([
                    EventDate(event=event, event_date=row['event_date'], event_time=row['event_time'])
                    for row in extra_dates
                ])

                plan, _ = MediaMonthPlan.objects.get_or_create(
                    year=event_date.year,
                    month=event_date.month,
                )
                plan.events.add(event)

                template_count = 0
                if event.event_type_id:
                    template = get_template_for_event(event)
                    if template:
                        items = list(template.items.select_related('default_sub_team', 'default_role').prefetch_related('steps'))
                        if items:
                            template_count = len(apply_template_to_event(event, items, month_plan=plan))
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
            return self.invalid_response(request, form)

        if template_count:
            messages.success(
                request,
                f'Evento "{event.title}" criado com {template_count} conteúdo(s) do template!',
            )
        else:
            messages.success(request, f'Evento "{event.title}" criado!')

        return redirect(hub_redirect_url(request, selected=f'event-{event.pk}'))


class MediaEventSuggestionsView(MediaMemberRequiredMixin, View):
    """Explicitly offers existing central events; it never links one automatically."""

    def get(self, request):
        query = request.GET.get('q', '').strip()
        if len(query) < 2:
            return JsonResponse({'results': []})
        events = Event.objects.filter(
            is_recurring=False,
            title__icontains=query,
        ).order_by('event_date', 'pk')[:5]
        return JsonResponse({'results': [{
            'id': event.pk,
            'title': event.title,
            'date': event.event_date.strftime('%d/%m/%Y'),
            'url': f"{reverse('media_content_list')}?selected=event-{event.pk}",
        } for event in events]})


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
