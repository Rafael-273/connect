from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Q, F
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from website.forms.media_operations import DemandFiltersForm, EventFiltersForm
from website.forms.media_planning import MediaEventQuickForm
from website.models import Event
from website.services.demands_hub import DONE_STATUSES, content_hub_url
from website.services.media_operations import filtered_demands
from .mixins import MediaLeaderRequiredMixin, MediaMemberRequiredMixin


class MediaEventsView(MediaMemberRequiredMixin, View):
    def get(self, request):
        form = EventFiltersForm(request.GET)
        events = Event.objects.select_related('event_type').annotate(
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
        return render(request, 'member/media_planning/events.html', {
            **self._nav_context(), 'form': form, 'page_obj': page, 'pagination_query': pagination_query(request),
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
    def get_form(self, event, data=None):
        fields = ('title', 'event_date', 'event_time', 'end_date', 'end_time', 'location', 'description')
        form = MediaEventQuickForm(data, initial={field: getattr(event, field) for field in fields}, prefix='event')
        del form.fields['event_type']
        return form

    def response(self, request, event, form, status=200):
        auto = event.media_contents.filter(due_date_auto=True).count()
        manual = event.media_contents.filter(due_date_auto=False, due_date__isnull=False).count()
        return render(request, 'member/media_planning/event_form.html', {
            **self._nav_context(), 'event': event, 'form': form, 'automatic_count': auto, 'manual_count': manual,
        }, status=status)

    def get(self, request, pk):
        event = get_object_or_404(Event, pk=pk)
        return self.response(request, event, self.get_form(event))

    def post(self, request, pk):
        event = get_object_or_404(Event, pk=pk)
        form = self.get_form(event, request.POST)
        if not form.is_valid():
            return self.response(request, event, form, status=400)
        for field, value in form.cleaned_data.items():
            setattr(event, field, value)
        try:
            event.save(update_fields=[*form.cleaned_data, 'update_at'])
        except ValidationError as exc:
            form.add_error(None, ' '.join(exc.messages))
            return self.response(request, event, form, status=400)
        messages.success(request, 'Evento atualizado. Datas automáticas foram recalculadas; datas manuais foram preservadas.')
        return redirect(f"{reverse('media_content_list')}?selected=event-{event.pk}")
