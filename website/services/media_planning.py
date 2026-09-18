"""Serviços auxiliares do módulo de planejamento de mídia."""

import datetime

from django.utils import timezone
from django.db import transaction
from django.core.exceptions import ValidationError

from website.models.media_content import MediaContent
from website.services.ministry_organization import media_teams
from website.models.media_event_type import MediaPlanningTemplate, MediaPlanningTemplateItem


def offset_to_datetime(event_date, offset_days):
    """Converte offset em dias (relativo ao evento) para datetime com timezone."""
    if event_date is None or offset_days is None:
        return None
    try:
        target_date = event_date + datetime.timedelta(days=offset_days)
    except (OverflowError, ValueError):
        raise ValidationError('O prazo relativo ultrapassa o intervalo de datas válido. Revise o template.')
    naive = datetime.datetime.combine(target_date, datetime.time(hour=12, minute=0))
    return timezone.make_aware(naive) if timezone.is_naive(naive) else naive


@transaction.atomic
def apply_template_to_event(event, template_items, month_plan=None):
    """
    Cria MediaContent a partir de itens de template selecionados.
    Não altera conteúdos existentes — apenas cria novos registros.
    """
    from website.models.event import Event
    event = Event.objects.select_for_update().get(pk=event.pk)
    template = get_template_for_event(event)
    if template is None:
        raise ValidationError('Este evento não possui um template ativo.')
    items = list(template_items)
    if any(item.template_id != template.pk for item in items):
        raise ValidationError('Há uma demanda que não pertence ao template deste evento.')
    applied = set(MediaContent.all_objects.filter(event=event).values_list('template_item_id', flat=True))
    created = []
    from website.services.demands_hub import sync_content_assignments

    for item in items:
        if item.pk in applied:
            continue
        if item.default_sub_team_id and not media_teams().filter(pk=item.default_sub_team_id, is_active=True).exists():
            raise ValidationError('A equipe sugerida deve ser uma equipe ativa do ministério de mídia.')
        steps = list(item.steps.order_by('sort_order', 'pk'))
        has_steps = bool(steps)
        content = MediaContent(
            title=item.title,
            description=item.description,
            content_type=item.content_type,
            event=event,
            month_plan=month_plan,
            sub_team=item.default_sub_team,
            assigned_role=None if has_steps else item.default_role,
            requires_recording=item.requires_recording,
            requires_editing=item.requires_editing,
            publication_channel=item.publication_channel if not has_steps else '',
            publication_date=None if has_steps else offset_to_datetime(event.event_date, item.publication_offset_days),
            due_date=None if has_steps else offset_to_datetime(event.event_date, item.due_offset_days),
            start_date=None if has_steps else offset_to_datetime(event.event_date, item.lead_offset_days),
            start_offset_days=None if has_steps else item.lead_offset_days,
            due_offset_days=None if has_steps else item.due_offset_days,
            publication_offset_days=None if has_steps else item.publication_offset_days,
            start_date_auto=not has_steps and item.lead_offset_days is not None,
            due_date_auto=not has_steps and item.due_offset_days is not None,
            publication_date_auto=not has_steps and item.publication_offset_days is not None,
            observations=item.notes,
            template_item=item,
            status='pending',
            priority='medium',
        )
        content.save()
        if has_steps:
            sync_content_assignments(content, [{
                'role': step.title,
                'user_id': None,
                'due_offset_days': step.due_offset_days,
                'description': step.description,
                'task_id': None,
            } for step in steps])
        created.append(content)
        applied.add(item.pk)
    return created


def get_template_for_event(event):
    """Retorna o template ativo para o tipo do evento, se existir."""
    if not event.event_type_id or not event.event_type.is_active or event.event_type.deleted:
        return None
    try:
        template = event.event_type.planning_template
    except MediaPlanningTemplate.DoesNotExist:
        return None
    if not template.is_active or template.deleted:
        return None
    return template


def get_available_template_items(event):
    """Itens do template ainda não aplicados a este evento."""
    template = get_template_for_event(event)
    if not template:
        return MediaPlanningTemplateItem.objects.none()

    applied_ids = MediaContent.all_objects.filter(
        event=event,
        template_item__isnull=False,
    ).values_list('template_item_id', flat=True)

    return template.items.select_related('default_sub_team').prefetch_related(
        'steps'
    ).exclude(pk__in=applied_ids).order_by('sort_order', 'title')


@transaction.atomic
def recalculate_event_dates(event):
    """Move only automatic dates, using the rules copied at generation time.

    Called inside Event.save's transaction. Queryset updates intentionally bypass
    the model's manual-edit detection; no tasks or manual dates are touched.
    """
    for field, offset in (('start_date', 'start_offset_days'), ('due_date', 'due_offset_days'), ('publication_date', 'publication_offset_days')):
        contents = MediaContent.objects.select_for_update().filter(event=event, **{field + '_auto': True, offset + '__isnull': False})
        for content in contents:
            MediaContent.objects.filter(pk=content.pk).update(**{
                field: offset_to_datetime(event.event_date, getattr(content, offset)),
                'update_at': timezone.now(),
            })

    from website.models.media_task import MediaTask
    tasks = MediaTask.objects.select_for_update().filter(
        content__event=event,
        due_offset_days__isnull=False,
    )
    for task in tasks:
        MediaTask.objects.filter(pk=task.pk).update(
            due_date=offset_to_datetime(event.event_date, task.due_offset_days),
            update_at=timezone.now(),
        )


def relative_days_label(days):
    if days is None:
        return 'Sem prazo definido'
    if days == 0:
        return 'No dia do evento'
    count = abs(days)
    return f"{count} {'dia' if count == 1 else 'dias'} {'antes' if days < 0 else 'depois'} do evento"


def template_preview_data():
    """Serializable preview, scoped to the same active types used by creation."""
    templates = MediaPlanningTemplate.objects.filter(is_active=True, event_type__is_active=True, event_type__deleted__isnull=True).prefetch_related('items__default_sub_team')
    return {str(template.event_type_id): [
        {'title': item.title, 'team': str(item.default_sub_team) if item.default_sub_team else '',
         'due': relative_days_label(item.due_offset_days), 'offset': item.due_offset_days}
        for item in template.items.all()
    ] for template in templates}
