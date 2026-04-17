from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import render
from django.views import View

from ..mixins import ConsolidationPermissionMixin
from ...models.follow_up import FollowUp, FollowUpTemplate


class ReportsView(LoginRequiredMixin, ConsolidationPermissionMixin, View):
    """Relatórios de consolidação com dados reais"""

    def _get_followup_counts(self):
        """Returns (total, completed, overdue) using model properties."""
        all_followups = list(FollowUp.objects.all())
        total = len(all_followups)
        completed = sum(1 for f in all_followups if f.is_completed)
        overdue = sum(1 for f in all_followups if not f.is_completed and f.is_overdue)
        return total, completed, overdue

    def _get_template_stats(self):
        """Returns per-template usage and average progress."""
        stats = []
        for tmpl in FollowUpTemplate.objects.all():
            usages = list(FollowUp.objects.filter(template=tmpl))
            if usages:
                avg_progress = sum(f.progress_percentage for f in usages) / len(usages)
                stats.append({'name': tmpl.name, 'progress': avg_progress, 'total_followups': len(usages)})
        return stats

    def get(self, request):
        total, completed, overdue = self._get_followup_counts()
        active = total - completed
        rate = (completed / total * 100) if total else 0
        recent = FollowUp.objects.select_related(
            'accompanied', 'responsible', 'template',
        ).order_by('-created_at')[:10]
        return render(request, 'admin_panel/reports.html', {
            'title': 'Relatórios de Consolidação',
            'total_followups': total,
            'active_followups': active,
            'completed_followups': completed,
            'overdue_followups': overdue,
            'completion_rate': rate,
            'recent_followups': recent,
            'template_stats': self._get_template_stats(),
        })
