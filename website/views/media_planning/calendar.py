from django.http import JsonResponse
from django.shortcuts import render
from django.views import View

from ...models.event import Event
from ...models.media_content import MediaContent
from ...models.media_task import MediaTask
from .mixins import MediaMemberRequiredMixin


class MediaCalendarView(MediaMemberRequiredMixin, View):
    template_name = 'member/media_planning/calendar.html'

    def get(self, request):
        ctx = {**self._nav_context()}
        return render(request, self.template_name, ctx)


class MediaCalendarEventsAPIView(MediaMemberRequiredMixin, View):
    def get(self, request):
        start = request.GET.get('start', '')
        end = request.GET.get('end', '')
        events = []

        # Church events (shown as informational markers)
        event_qs = Event.objects.all()
        if start:
            event_qs = event_qs.filter(event_date__gte=start[:10])
        if end:
            event_qs = event_qs.filter(event_date__lte=end[:10])
        for e in event_qs:
            events.append({
                'id': f'event-{e.pk}',
                'title': e.title,
                'start': e.event_date.isoformat(),
                'color': '#3B82F6',
                'textColor': '#ffffff',
                'extendedProps': {'type': 'event'},
            })

        # Media content publication dates
        content_qs = MediaContent.objects.filter(publication_date__isnull=False)
        if start:
            content_qs = content_qs.filter(publication_date__date__gte=start[:10])
        if end:
            content_qs = content_qs.filter(publication_date__date__lte=end[:10])
        for c in content_qs.select_related('event'):
            events.append({
                'id': f'content-{c.pk}',
                'title': c.title,
                'start': c.publication_date.isoformat(),
                'url': f'/media/contents/{c.pk}/',
                'color': '#10B981',
                'textColor': '#ffffff',
                'extendedProps': {'type': 'content', 'pk': c.pk},
            })

        # Task due dates (incomplete only)
        task_qs = MediaTask.objects.filter(
            due_date__isnull=False,
        ).exclude(status='completed')
        if start:
            task_qs = task_qs.filter(due_date__date__gte=start[:10])
        if end:
            task_qs = task_qs.filter(due_date__date__lte=end[:10])
        for t in task_qs.select_related('content'):
            events.append({
                'id': f'task-{t.pk}',
                'title': t.title,
                'start': t.due_date.isoformat(),
                'url': f'/media/contents/{t.content_id}/',
                'color': '#F59E0B',
                'textColor': '#ffffff',
                'extendedProps': {'type': 'task', 'pk': t.pk},
            })

        return JsonResponse(events, safe=False)


class MediaCalendarUpdateView(MediaMemberRequiredMixin, View):
    """PATCH endpoint for drag-and-drop rescheduling (leaders only)."""

    def post(self, request, pk):
        from ...models.media_content import MediaContent as MC
        import json

        if not (self.media_membership and self.media_membership.role == 'leader'):
            return JsonResponse({'error': 'Sem permissão.'}, status=403)

        try:
            data = json.loads(request.body)
            new_date = data.get('publication_date')
            content = MC.objects.get(pk=pk)
            content.publication_date = new_date
            content.save(update_fields=['publication_date', 'update_at'])
            return JsonResponse({'ok': True})
        except (MC.DoesNotExist, KeyError, ValueError) as exc:
            return JsonResponse({'error': str(exc)}, status=400)
