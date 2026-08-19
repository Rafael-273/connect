from django.shortcuts import redirect
from django.urls import reverse
from django.views import View

from .mixins import MediaLeaderRequiredMixin, MediaMemberRequiredMixin


class MediaEventTypeListView(MediaMemberRequiredMixin, View):
    def get(self, request):
        return redirect(f"{reverse('media_content_list')}?tab=templates")


class MediaEventTypeCreateView(MediaLeaderRequiredMixin, View):
    def get(self, request):
        return redirect('media_template_create')


class MediaEventTypeUpdateView(MediaLeaderRequiredMixin, View):
    def get(self, request, pk):
        from ...models.media_event_type import MediaPlanningTemplate
        try:
            tpl = MediaPlanningTemplate.objects.get(event_type_id=pk)
            return redirect('media_template_edit', pk=tpl.pk)
        except MediaPlanningTemplate.DoesNotExist:
            return redirect('media_template_setup', event_type_pk=pk)

    def post(self, request, pk):
        return self.get(request, pk)
