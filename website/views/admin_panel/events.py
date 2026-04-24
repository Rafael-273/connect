from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.text import slugify
from django.views import View

from ..mixins import ModulePermissionMixin
from ...models.event import Event


class EventsListView(LoginRequiredMixin, ModulePermissionMixin, View):
    """Lista de eventos com filtros e busca"""
    module_name = 'events'

    def _get_queryset(self):
        return Event.objects.all()

    def _apply_filters(self, qs, search, status_filter):
        if search:
            qs = qs.filter(
                Q(title__icontains=search)
                | Q(description__icontains=search)
                | Q(location__icontains=search)
            )
        if status_filter:
            today = timezone.now().date()
            if status_filter == 'upcoming':
                qs = qs.filter(event_date__gte=today)
            elif status_filter == 'past':
                qs = qs.filter(event_date__lt=today)
        return qs.order_by('-event_date')

    def _build_context(self, request):
        search = request.GET.get('search', '')
        status_filter = request.GET.get('status', '')

        qs = self._apply_filters(self._get_queryset(), search, status_filter)
        events = Paginator(qs, 20).get_page(request.GET.get('page'))

        return {
            'events': events,
            'search': search,
            'status_filter': status_filter,
        }

    def get(self, request):
        return render(request, 'admin_panel/events/list.html', self._build_context(request))


class EventEditView(LoginRequiredMixin, ModulePermissionMixin, View):
    """Criar ou editar evento"""
    module_name = 'events'

    def get(self, request, event_id=None):
        event = get_object_or_404(Event, id=event_id) if event_id else None
        messages.get_messages(request).used = True
        return render(request, 'admin_panel/events/edit.html', {'event': event})

    def post(self, request, event_id=None):
        event = get_object_or_404(Event, id=event_id) if event_id else None
        try:
            data = self._parse_event_data(request)
            data['slug'] = self._ensure_unique_slug(
                data['slug'] or slugify(data['title']), event_id
            )
            event = self._save_event(event, data, request.FILES)
            messages.success(request, f'Evento {"atualizado" if event_id else "criado"} com sucesso!')
            return redirect('admin_events_list')
        except Exception as e:
            messages.error(request, f'Erro ao salvar evento: {e}')
        return render(request, 'admin_panel/events/edit.html', {'event': event})

    @staticmethod
    def _parse_event_data(request):
        is_recurring = request.POST.get('is_recurring') == 'on'
        if is_recurring:
            weekday = int(request.POST.get('weekday', 0))
            today = timezone.now().date()
            days_ahead = (weekday - today.weekday()) % 7
            event_date = today + timezone.timedelta(days=days_ahead)
            display_start = today - timezone.timedelta(days=1)
            display_end = today + timezone.timedelta(days=3650)
            recurrence_pattern = request.POST.get('recurrence_pattern')
        else:
            event_date = request.POST.get('event_date')
            display_start = request.POST.get('display_start') or event_date
            display_end = request.POST.get('display_end') or event_date
            recurrence_pattern = None
        return {
            'title': request.POST.get('title'),
            'slug': request.POST.get('slug'),
            'description': request.POST.get('description'),
            'is_recurring': is_recurring,
            'event_date': event_date,
            'event_time': request.POST.get('event_time') or None,
            'location': request.POST.get('location') or None,
            'link_more_info': request.POST.get('link_more_info') or None,
            'link_type': request.POST.get('link_type') or 'more_info',
            'display_start': display_start,
            'display_end': display_end,
            'recurrence_pattern': recurrence_pattern,
            'recurrence_description': None,
        }

    @staticmethod
    def _ensure_unique_slug(slug, exclude_id=None):
        qs = Event.objects.filter(slug=slug)
        if exclude_id:
            qs = qs.exclude(id=exclude_id)
        if qs.exists():
            return f"{slug}-{timezone.now().strftime('%Y%m%d')}"
        return slug

    @staticmethod
    def _save_event(event, data, files):
        if event:
            for field, value in data.items():
                setattr(event, field, value)
        else:
            event = Event.objects.create(**data)
        if 'banner' in files:
            event.banner = files['banner']
        event.save()
        return event
