from django.shortcuts import render
from django.views import View
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie

from .mixins import MediaMemberRequiredMixin


@method_decorator(ensure_csrf_cookie, name='dispatch')
class MediaDashboardView(MediaMemberRequiredMixin, View):
    template_name = 'member/media_planning/dashboard.html'

    def get(self, request):
        return render(request, self.template_name, self._nav_context())
