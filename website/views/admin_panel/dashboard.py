import json
from datetime import timedelta

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.shortcuts import render
from django.utils import timezone
from django.views import View

from ..mixins import AdminRequiredMixin
from ...models.event import Event
from ...models.member import Member
from ...models.ministry import Ministry
from ...models.visitor import Visitor


class DashboardView(LoginRequiredMixin, AdminRequiredMixin, View):
    def _get_totals(self):
        return {
            'total_members': Member.objects.filter(is_active=True).count(),
            'total_visitors': Visitor.objects.count(),
            'total_events': Event.objects.count(),
        }

    def _get_recent_stats(self, last_month):
        recent_visitor_conversions = Visitor.objects.filter(
            visit_date__gte=last_month, decision_for_jesus=True,
        ).count()
        recent_member_conversions = Member.objects.filter(
            conversion_date__gte=last_month.date(), conversion='new_convert',
        ).count()
        return {
            'recent_conversions': recent_visitor_conversions + recent_member_conversions,
        }

    def _get_visitors_chart_data(self):
        visitors_by_month = []
        for i in range(6):
            month_start = (timezone.now() - timedelta(days=30 * i)).replace(day=1)
            month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            count = Visitor.objects.filter(
                visit_date__gte=month_start, visit_date__lte=month_end,
            ).count()
            visitors_by_month.append({'month': month_start.strftime('%b/%Y'), 'count': count})
        visitors_by_month.reverse()
        return json.dumps(visitors_by_month)

    def _get_lists(self):
        return {
            'ministries_stats': Ministry.objects.annotate(
                member_count=Count('memberships'),
            ).order_by('-member_count')[:5],
            'recent_visitors_list': Visitor.objects.order_by('-visit_date')[:5],
            'recently_converted_members': Member.objects.filter(
                conversion='new_convert',
            ).order_by('-update_at')[:3],
            'upcoming_events_list': Event.objects.filter(
                event_date__gte=timezone.now().date(),
            ).order_by('event_date')[:5],
        }

    def _build_context(self):
        last_month = timezone.now() - timedelta(days=30)
        return {
            **self._get_totals(),
            **self._get_recent_stats(last_month),
            'visitors_by_month_json': self._get_visitors_chart_data(),
            **self._get_lists(),
        }

    def get(self, request):
        return render(request, 'admin_panel/dashboard.html', self._build_context())
