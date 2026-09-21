from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q, F
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date, parse_time
from django.urls import reverse
from django.views import View
from website.forms.media_operations import DemandFiltersForm, EventFiltersForm
from website.forms.media_planning import MediaEventQuickForm
from website.models import Event, EventDate
from website.models.event import MediaEventOrganization
from website.services.event_media_integration import media_eligible_events
from website.services.demands_hub import DONE_STATUSES, content_hub_url
from website.services.media_operations import filtered_demands
from .mixins import MediaLeaderRequiredMixin, MediaMemberRequiredMixin


class MediaEventsView(MediaMemberRequiredMixin, View):
    def get(self, request):
        form = EventFiltersForm(request.GET)
        events = media_eligible_events().select_related('event_type', 'media_organization').annotate(
            demand_count=Count('media_contents', filter=Q(media_contents__deleted__isnull=True)),
            done_count=Count('media_contents', filter=Q(media_contents__deleted__isnull=True, media_contents__status__in=DONE_STATUSES)),
        ).order_by('event_date', 'event_time', 'pk')
        if form.is_valid():
            data = form.cleaned_data
            if data.get('q'):
                events = events.filter(title__icontains=data['q'])
            if data.get('event_type'):
                events = events.filter(event_type=data['event_type'])
            if data.get('date_from'):
                events = events.filter(event_date__gte=data['date_from'])
            if data.get('date_to'):
                events = events.filter(event_date__lte=data['date_to'])
        else:
            events = events.none()
        page = Paginator(events, 30).get_page(request.GET.get('page'))
        for event in page:
            event.progress = round(100 * event.done_count / event.demand_count) if event.demand_count else 0
            event.operation_status = 'Sem demandas' if not event.demand_count else 'Concluído' if event.progress == 100 else 'Em andamento' if event.done_count else 'Pendente'
        pending_institutional_count = events.filter(
            institutional_status=Event.INSTITUTIONAL_STATUS_PENDING,
        ).count()
        pending_organization_count = events.exclude(media_organization__status='organized').count()
        return render(request, 'member/media_planning/events.html', {
            **self._nav_context(), 'form': form, 'page_obj': page, 'pagination_query': pagination_query(request),
            'pending_institutional_count': pending_institutional_count,
            'pending_organization_count': pending_organization_count,
        })


def pagination_query(request):
    query = request.GET.copy()
    query.pop('page', None)
    return query.urlencode()


class MediaDemandsView(MediaMemberRequiredMixin, View):
    def get(self, request):
        form = DemandFiltersForm(request.GET)
        qs = filtered_demands(form).order_by(F('due_date').asc(nulls_last=True), '-created_at')
        page = Paginator(qs, 40).get_page(request.GET.get('page'))
        for content in page:
            content.detail_url = content_hub_url(content)
        return render(request, 'member/media_planning/demands.html', {
            **self._nav_context(), 'form': form, 'page_obj': page, 'pagination_query': pagination_query(request),
        })


class MediaEventUpdateView(MediaLeaderRequiredMixin, View):
    def get_form(self, event, data=None, prefix='event'):
        fields = ('title', 'event_date', 'event_time', 'end_date', 'end_time', 'location', 'description')
        form = MediaEventQuickForm(data, initial={field: getattr(event, field) for field in fields}, prefix=prefix)
        del form.fields['event_type']
        return form

    def response(self, request, event, form, status=200):
        auto = event.media_contents.filter(due_date_auto=True).count()
        manual = event.media_contents.filter(due_date_auto=False, due_date__isnull=False).count()
        return render(request, 'member/media_planning/event_form.html', {
            **self._nav_context(), 'event': event, 'form': form, 'automatic_count': auto, 'manual_count': manual,
        }, status=status)

    def get(self, request, pk):
        event = get_object_or_404(Event, pk=pk, is_recurring=False)
        return self.response(request, event, self.get_form(event))

    def post(self, request, pk):
        event = get_object_or_404(Event, pk=pk, is_recurring=False)
        modal_edit = 'event-edit-title' in request.POST
        form = self.get_form(event, request.POST, prefix='event-edit' if modal_edit else 'event')
        if not form.is_valid():
            if modal_edit:
                messages.error(request, 'Revise os campos do evento antes de salvar.')
                return redirect(f"{reverse('media_content_list')}?selected=event-{event.pk}")
            return self.response(request, event, form, status=400)
        extra_dates = []
        if modal_edit:
            for raw_date, raw_time in zip(
                request.POST.getlist('event-edit-extra_event_date'),
                request.POST.getlist('event-edit-extra_event_time'),
            ):
                if not raw_date:
                    continue
                parsed_date = parse_date(raw_date)
                if not parsed_date:
                    messages.error(request, 'Informe datas adicionais válidas.')
                    return redirect(f"{reverse('media_content_list')}?selected=event-{event.pk}")
                extra_dates.append({
                    'event_date': parsed_date,
                    'event_time': parse_time(raw_time) if raw_time else None,
                })
        for field, value in form.cleaned_data.items():
            setattr(event, field, value)
        if modal_edit and extra_dates:
            event.end_date = max([event.event_date, *[row['event_date'] for row in extra_dates]])
        try:
            event.save(update_fields=[*form.cleaned_data, 'update_at'])
            if modal_edit:
                EventDate.objects.filter(event=event).delete()
                EventDate.objects.bulk_create([
                    EventDate(event=event, event_date=row['event_date'], event_time=row['event_time'])
                    for row in extra_dates
                ])
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
            return self.response(request, event, form, status=400)
        messages.success(request, 'Evento atualizado. Datas automáticas foram recalculadas; datas manuais foram preservadas.')
        return redirect(f"{reverse('media_content_list')}?selected=event-{event.pk}")


class MediaEventRemoveFromMediaView(MediaLeaderRequiredMixin, View):
    """Removes operational media data without deleting the central Event."""

    def post(self, request, pk):
        event = get_object_or_404(Event, pk=pk, is_recurring=False)
        organization = getattr(event, 'media_organization', None)
        with transaction.atomic():
            if organization and organization.created_from_media and event.institutional_status == Event.INSTITUTIONAL_STATUS_PENDING:
                event.delete()
                message = f'O evento "{event.title}" e suas demandas foram excluídos.'
            else:
                event.media_contents.all().delete()
                event.month_plans.clear()
                if organization:
                    organization.status = MediaEventOrganization.STATUS_REMOVED
                    organization.save(update_fields=['status', 'update_at'])
                message = f'O evento "{event.title}" foi removido da organização da Mídia. O cadastro institucional foi preservado.'
        messages.success(request, message)
        return redirect('media_content_list')
