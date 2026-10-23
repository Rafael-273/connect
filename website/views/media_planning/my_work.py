from django.contrib import messages
from datetime import timedelta

from django.conf import settings
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.utils.dateparse import parse_date
from django.http import FileResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views import View

from website.models import MediaContent
from website.models.media_task import TASK_STATUS_CHOICES
from website.services.demands_hub import sync_content_status_from_tasks
from website.services.media_work import (
    activity_attachments, activity_data, individual_tasks, ordered_tasks,
)
from website.views.mixins import MemberRequiredMixin
from .mixins import _get_media_membership


class IndividualMediaRequiredMixin(MemberRequiredMixin):
    """Active ministry members and leaders share the same personal scope."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        member = getattr(request.user, 'member', None)
        self.media_membership = _get_media_membership(member) if member else None
        if not self.media_membership or not request.user.is_active:
            messages.error(request, 'Acesso exclusivo aos membros ativos da Mídia Externa.')
            return redirect('member_dashboard')
        with timezone.override(settings.MEDIA_WORK_TIME_ZONE):
            return super().dispatch(request, *args, **kwargs)

    def nav_context(self):
        return {
            'member': self.member, 'is_external_media_member': True,
            'is_media_member': True,
            'is_media_leader': self.media_membership.role == 'leader',
            'can_consolidate': self.member.is_available_to_consolidate,
            'nav_active': 'external_media',
        }


class MediaMyWorkView(IndividualMediaRequiredMixin, View):
    def get(self, request):
        tab = request.GET.get('tab', 'all')
        if tab not in ('all', 'pending', 'completed', 'archived'):
            tab = 'all'
        archive_before = timezone.now() - timedelta(days=30)
        tasks = ordered_tasks(request.user, tab, archive_before)
        query = request.GET.get('q', '').strip()[:200]
        status = request.GET.get('status', '')
        if status not in ('pending', 'in_progress') or tab in ('completed', 'archived'):
            status = ''
        if query:
            tasks = tasks.filter(Q(title__icontains=query) | Q(content__title__icontains=query) | Q(content__event__title__icontains=query))
        if status:
            tasks = tasks.filter(status=status)
        dates = {}
        for key, lookup in [('period_from', 'due_date__date__gte'), ('period_to', 'due_date__date__lte')]:
            try:
                value = parse_date(request.GET.get(key, ''))
            except ValueError:
                value = None
            dates[key] = value.isoformat() if value else ''
            if value:
                tasks = tasks.filter(**{lookup: value})
        page = Paginator(tasks, 40).get_page(request.GET.get('page'))
        params = request.GET.copy()
        params.pop('page', None)
        today = timezone.localdate()
        activities = [activity_data(task, today) for task in page]
        if tab == 'completed':
            for activity in activities:
                activity['deadline']['group'] = 'Concluídas recentemente'
        elif tab == 'archived':
            for activity in activities:
                activity['deadline']['group'] = 'Arquivadas'
        elif tab == 'all':
            for activity in activities:
                if activity['status'] == 'completed':
                    activity['deadline']['group'] = 'Concluídas recentemente'
        return render(request, 'member/media_planning/my_work.html', {
            **self.nav_context(), 'tab': tab, 'page': page,
            'activities': activities,
            'filter_q': query, 'filter_status': status, **dates,
            'has_filters': bool(tab != 'all' or status or any(dates.values())),
            'has_search': bool(query or status or any(dates.values())),
            'pagination_query': params.urlencode(),
        })


class MediaMyWorkDetailView(IndividualMediaRequiredMixin, View):
    def get(self, request, pk):
        task = get_object_or_404(
            individual_tasks(request.user).select_related('content__event').only(
                'title', 'description', 'status', 'due_date', 'content_id',
                'content__title', 'content__description', 'content__observations',
                'content__event_id', 'content__event__title',
            ), pk=pk,
        )
        activity = activity_data(task)
        activity.update({
            'description': task.description,
            'briefing': task.content.description,
            'observations': task.content.observations,
        })
        activity['header_title'] = '{} · {}'.format(
            activity['demand_title'], activity['event_title'] or 'Demanda avulsa',
        )
        template = 'member/media_planning/my_work_detail.html'
        if request.GET.get('fragment') == 'activity':
            template = 'member/media_planning/partials/my_work_detail_content.html'
        return render(request, template, {
            **self.nav_context(), 'activity': activity,
            'attachments': activity_attachments(task),
        })


class MediaMyWorkStatusView(IndividualMediaRequiredMixin, View):
    def post(self, request, pk):
        task = get_object_or_404(individual_tasks(request.user), pk=pk)
        status = request.POST.get('status')
        if status not in dict(TASK_STATUS_CHOICES):
            return HttpResponseBadRequest('Status inválido.')
        with transaction.atomic():
            # Serialize personal updates on the same demand before recalculating it.
            content = get_object_or_404(
                MediaContent.objects.select_for_update(), pk=task.content_id,
            )
            task = get_object_or_404(
                individual_tasks(request.user).select_for_update(), pk=pk,
                content_id=content.pk,
            )
            task.status = status
            task.save(update_fields=['status', 'update_at'])
            sync_content_status_from_tasks(content)
        messages.success(request, 'Sua atividade foi atualizada.')
        if request.POST.get('return_to') == 'list':
            return redirect('media_my_work')
        return redirect('media_my_work_detail', pk=pk)


class MediaMyWorkAttachmentView(IndividualMediaRequiredMixin, View):
    def get(self, request, pk, attachment_pk):
        task = get_object_or_404(individual_tasks(request.user), pk=pk)
        attachment = get_object_or_404(activity_attachments(task), pk=attachment_pk)
        return FileResponse(
            attachment.file.open('rb'), as_attachment=True,
            filename=attachment.file.name.rsplit('/', 1)[-1],
        )
