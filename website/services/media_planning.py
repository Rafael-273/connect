"""Serviços auxiliares do módulo de planejamento de mídia."""

import datetime

from django.utils import timezone

from website.models.media_content import MediaContent
from website.models.media_event_type import MediaPlanningTemplate, MediaPlanningTemplateItem


def offset_to_datetime(event_date, offset_days):
    """Converte offset em dias (relativo ao evento) para datetime com timezone."""
    if event_date is None or offset_days is None:
        return None
    target_date = event_date + datetime.timedelta(days=offset_days)
    naive = datetime.datetime.combine(target_date, datetime.time(hour=12, minute=0))
    return timezone.make_aware(naive) if timezone.is_naive(naive) else naive


def apply_template_to_event(event, template_items, month_plan=None):
    """
    Cria MediaContent a partir de itens de template selecionados.
    Não altera conteúdos existentes — apenas cria novos registros.
    """
    created = []
    for item in template_items:
        content = MediaContent(
            title=item.title,
            description=item.description,
            content_type=item.content_type,
            event=event,
            month_plan=month_plan,
            sub_team=item.default_sub_team,
            assigned_role=item.default_role,
            requires_recording=item.requires_recording,
            requires_editing=item.requires_editing,
            publication_channel=item.publication_channel,
            publication_date=offset_to_datetime(event.event_date, item.publication_offset_days),
            due_date=offset_to_datetime(event.event_date, item.due_offset_days),
            observations=item.notes,
            template_item=item,
            status='pending',
            priority='medium',
        )
        content.save()
        created.append(content)
    return created


def get_template_for_event(event):
    """Retorna o template ativo para o tipo do evento, se existir."""
    if not event.event_type_id:
        return None
    try:
        template = event.event_type.planning_template
    except MediaPlanningTemplate.DoesNotExist:
        return None
    if not template.is_active:
        return None
    return template


def get_available_template_items(event):
    """Itens do template ainda não aplicados a este evento."""
    template = get_template_for_event(event)
    if not template:
        return MediaPlanningTemplateItem.objects.none()

    applied_ids = MediaContent.objects.filter(
        event=event,
        template_item__isnull=False,
    ).values_list('template_item_id', flat=True)

    return template.items.select_related(
        'default_sub_team', 'default_role'
    ).exclude(pk__in=applied_ids).order_by('sort_order', 'title')
