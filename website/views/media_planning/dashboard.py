from django.shortcuts import render
from django.views import View

from .mixins import MediaMemberRequiredMixin


class MediaDashboardView(MediaMemberRequiredMixin, View):
    template_name = 'member/media_planning/dashboard.html'

    def get(self, request):
        return render(request, self.template_name, self._nav_context())
