"""Personal work queries. Never reuse the leadership demand-detail builders here."""
from datetime import timedelta

from django.db.models import Case, F, IntegerField, Q, Value, When
from django.utils import timezone

from website.models import MediaAttachment, MediaTask


def individual_tasks(user):
    return MediaTask.objects.filter(
        assigned_to=user, content__deleted__isnull=True,
    )


def ordered_tasks(user, tab='all', archive_before=None):
    archive_before = archive_before or timezone.now() - timedelta(days=30)
    tasks = individual_tasks(user)
    if tab == 'pending':
        tasks = tasks.exclude(status='completed')
    elif tab == 'completed':
        tasks = tasks.filter(status='completed', update_at__gte=archive_before)
    elif tab == 'archived':
        tasks = tasks.filter(status='completed', update_at__lt=archive_before)
    else:
        tasks = tasks.filter(
            Q(status__in=['pending', 'in_progress'])
            | Q(status='completed', update_at__gte=archive_before)
        )
    return tasks.select_related('content__event').only(
        'title', 'status', 'due_date', 'content_id', 'content__title',
        'content__event_id', 'content__event__title',
    ).annotate(
        work_sort_group=Case(
            When(status='completed', then=Value(1)),
            default=Value(0), output_field=IntegerField(),
        ),
    ).order_by('work_sort_group', F('due_date').asc(nulls_last=True), 'pk')


def deadline_info(due_date, today=None):
    if not due_date:
        return {'group': 'Sem prazo', 'label': 'Sem prazo', 'overdue': False}
    days = (timezone.localdate(due_date) - (today or timezone.localdate())).days
    if days < 0:
        return {'group': 'Atrasadas', 'label': 'Atrasada', 'overdue': True}
    if days == 0:
        return {'group': 'Hoje', 'label': 'Hoje', 'overdue': False}
    if days == 1:
        return {'group': 'Amanhã', 'label': 'Amanhã', 'overdue': False}
    return {
        'group': 'Próximos dias' if days <= 7 else 'Mais adiante',
        'label': f'Em {days} dias', 'overdue': False,
    }


def activity_data(task, today=None):
    """Explicit allowlist: no demand status, assignees or sibling tasks."""
    return {
        'id': task.pk, 'title': task.title, 'status': task.status,
        'status_label': task.get_status_display(), 'due_date': task.due_date,
        'demand_title': task.content.title,
        'event_title': task.content.event.title if task.content.event_id else '',
        'deadline': deadline_info(task.due_date, today),
    }


def activity_attachments(task):
    # Both FKs can coexist in legacy/invalid rows: deny those as well.
    return MediaAttachment.objects.filter(
        Q(content_id=task.content_id, task__isnull=True)
        | Q(task_id=task.pk, content__isnull=True)
    ).only('name', 'file')
