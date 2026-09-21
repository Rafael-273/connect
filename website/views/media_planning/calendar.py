import datetime
import json

from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.db.models import Q
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from website.forms.media_operations import DemandFiltersForm
from website.models import Event, MediaContent, MediaTask
from website.services.demands_hub import content_hub_url, content_type_meta
from website.services.media_operations import filtered_demands
from .mixins import MediaMemberRequiredMixin

_TASK_STATUS_COLORS = {
    'pending': '#F59E0B',
    'in_progress': '#C90905',
    'completed': '#10B981',
}


def _task_calendar_color(task, today):
    if task.status != 'completed' and task.due_date and task.due_date.date() < today:
        return '#DC2626'
    return _TASK_STATUS_COLORS.get(task.status, '#F59E0B')


def _task_assignee_name(task):
    user = task.assigned_to
    if not user:
        return ''
    member = getattr(user, 'member', None)
    if member and member.name:
        return member.name
    return user.get_username()


def build_task_calendar_rows(contents, start, end, *, open_only=False):
    today = timezone.localdate()
    tasks = MediaTask.objects.filter(
        content__in=contents,
        due_date__isnull=False,
        due_date__date__gte=start,
        due_date__date__lt=end,
    )
    if open_only:
        tasks = tasks.exclude(status='completed')
    tasks = tasks.select_related(
        'content', 'content__event', 'content__sub_team', 'assigned_to__member',
    ).order_by('due_date', 'sort_order', 'pk')
    rows = []
    for task in tasks:
        assignee = _task_assignee_name(task)
        content = task.content
        event = content.event
        is_overdue = task.status != 'completed' and task.due_date and task.due_date.date() < today
        due_local = timezone.localtime(task.due_date) if task.due_date else None
        type_meta = content_type_meta(content.content_type)
        rows.append({
            'id': f'task-{task.pk}',
            'title': task.title,
            'start': task.due_date.isoformat(),
            'color': _task_calendar_color(task, today),
            'extendedProps': {
                'type': 'task',
                'pk': task.pk,
                'taskTitle': task.title,
                'contentTitle': content.title,
                'contentId': content.pk,
                'status': 'overdue' if is_overdue else task.status,
                'statusLabel': 'Atrasada' if is_overdue else task.get_status_display(),
                'assignee': assignee or 'Sem responsável',
                'contentType': content.get_content_type_display(),
                'contentTypeIcon': type_meta.get('icon', 'file'),
                'eventTitle': event.title if event else '',
                'team': str(content.sub_team) if content.sub_team else '',
                'description': task.description or '',
                'dueLabel': due_local.strftime('%d %b · %H:%M') if due_local else '',
                'contentUrl': content_hub_url(content),
                'contentStatus': content.get_status_display(),
            },
        })
    return rows


class MediaCalendarView(MediaMemberRequiredMixin, View):
    template_name = 'member/media_planning/calendar.html'

    def get(self, request):
        return render(request, self.template_name, {**self._nav_context(), 'form': DemandFiltersForm(request.GET)})


class MediaCalendarEventsAPIView(MediaMemberRequiredMixin, View):
    def get(self, request):
        form = DemandFiltersForm(request.GET)
        if not form.is_valid():
            return JsonResponse({'errors': form.errors}, status=400)
        try:
            start = datetime.date.fromisoformat(request.GET['start'][:10]) if request.GET.get('start') else timezone.localdate().replace(day=1)
            end = datetime.date.fromisoformat(request.GET['end'][:10]) if request.GET.get('end') else start + datetime.timedelta(days=42)
            if end <= start or (end - start).days > 366:
                raise ValueError
        except ValueError:
            return JsonResponse({'error': 'Informe um período válido de até um ano.'}, status=400)
        contents = filtered_demands(form)
        scope = request.GET.get('scope', '')
        if scope == 'tasks':
            return JsonResponse(build_task_calendar_rows(contents, start, end), safe=False)

        rows = []
        from website.services.event_media_integration import media_eligible_events
        event_qs = media_eligible_events().filter(event_date__lt=end).filter(Q(end_date__gte=start) | Q(end_date__isnull=True, event_date__gte=start))
        # Once demand filters are used, event markers follow the same matching demands.
        if any(form.cleaned_data.values()):
            event_qs = event_qs.filter(pk__in=contents.values('event_id'))
        for event in event_qs:
            rows.append({'id': f'event-{event.pk}', 'title': event.title, 'start': event.event_date.isoformat(),
                         'end': (event.end_date + datetime.timedelta(days=1)).isoformat() if event.end_date and event.end_date < datetime.date.max else None,
                         'allDay': True, 'color': '#3B82F6', 'url': f"{reverse('media_content_list')}?selected=event-{event.pk}", 'extendedProps': {'type': 'event'}})
        for field, kind, label, color in (
            ('start_date', 'start', 'Iniciar', '#64748B'),
            ('due_date', 'deadline', 'Prazo', '#DC2626'),
            ('publication_date', 'publication', 'Publicar', '#10B981'),
        ):
            for content in contents.filter(**{field + '__date__gte': start, field + '__date__lt': end}):
                rows.append({'id': f'{kind}-{content.pk}', 'title': f'{label}: {content.title}',
                             'start': getattr(content, field).isoformat(), 'url': content_hub_url(content), 'color': color,
                             'extendedProps': {'type': kind, 'pk': content.pk, 'automatic': getattr(content, field + '_auto')}})
        rows.extend(build_task_calendar_rows(contents, start, end, open_only=True))
        return JsonResponse(rows, safe=False)


class MediaCalendarUpdateView(MediaMemberRequiredMixin, View):
    """Existing reschedule endpoint; a direct change becomes a manual deadline."""

    def post(self, request, pk):
        if not (self.media_membership and self.media_membership.role == 'leader'):
            return JsonResponse({'error': 'Sem permissão.'}, status=403)
        try:
            data = json.loads(request.body)
            if not isinstance(data, dict) or 'publication_date' not in data:
                raise ValidationError('Informe a data de publicação.')
            content = MediaContent.objects.get(pk=pk)
            value = MediaContent._meta.get_field('publication_date').to_python(data['publication_date'])
            if value is not None and timezone.is_naive(value):
                value = timezone.make_aware(value)
            content.publication_date = value
            content.save(update_fields=['publication_date', 'update_at'])
            return JsonResponse({'ok': True})
        except (MediaContent.DoesNotExist, ValueError, TypeError, ValidationError):
            return JsonResponse({'error': 'Demanda ou data inválida.'}, status=400)
