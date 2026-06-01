from django.shortcuts import get_object_or_404, render
from django.views import View

from ...models.event import Event
from ...models.media_content import CONTENT_TYPE_CHOICES, MediaContent
from .mixins import MediaMemberRequiredMixin


class MediaEventContentsView(MediaMemberRequiredMixin, View):
    template_name = 'member/media_planning/event_contents.html'

    def get(self, request, event_pk):
        event = get_object_or_404(Event, pk=event_pk)
        contents = (
            MediaContent.objects.filter(event=event)
            .select_related('responsible__member')
            .order_by('due_date', '-created_at')
        )

        total = contents.count()
        done = contents.filter(status__in=['approved', 'scheduled', 'published']).count()
        progress = int(done / total * 100) if total else 0

        ctx = {
            **self._nav_context(),
            'event': event,
            'contents': contents,
            'total': total,
            'done': done,
            'progress': progress,
            'content_type_choices': CONTENT_TYPE_CHOICES,
        }
        return render(request, self.template_name, ctx)
