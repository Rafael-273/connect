import datetime

from django.db.models import Count, Q
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from ...models.media_content import MediaContent
from ...models.media_month_plan import MediaMonthPlan
from ...models.media_task import MediaTask
from .mixins import MediaMemberRequiredMixin


class MediaDashboardView(MediaMemberRequiredMixin, View):
    template_name = 'member/media_planning/dashboard.html'

    def get(self, request):
        now = timezone.now()
        week_ahead = now + datetime.timedelta(days=7)

        plans = (
            MediaMonthPlan.objects.annotate(
                total_contents=Count('contents', distinct=True),
                event_count=Count('events', distinct=True),
                category_count=Count('categories', distinct=True),
                done_count=Count(
                    'contents',
                    filter=Q(contents__status__in=['approved', 'scheduled', 'published']),
                    distinct=True,
                ),
            )
            .order_by('-year', '-month')[:12]
        )
        # Compute progress for each plan
        for p in plans:
            p.progress = int(p.done_count / p.total_contents * 100) if p.total_contents else 0

        ctx = {
            **self._nav_context(),
            'plans': plans,
            'total_pending': MediaContent.objects.filter(
                status__in=['pending', 'in_progress', 'review']
            ).count(),
            'total_delayed': MediaTask.objects.filter(
                due_date__lt=now,
                status__in=['pending', 'in_progress'],
            ).count(),
            'total_this_week': MediaContent.objects.filter(
                publication_date__range=(now, week_ahead)
            ).count(),
        }
        return render(request, self.template_name, ctx)
