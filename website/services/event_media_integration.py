"""Rules that keep central events and media operations in sync."""

from django.core.exceptions import ValidationError
from django.utils import timezone

from website.models.event import Event, MediaEventOrganization


def media_eligible_events():
    """Recurring events are institutional only and never enter media workflows."""
    return Event.objects.filter(is_recurring=False).exclude(
        media_organization__status=MediaEventOrganization.STATUS_REMOVED,
    ).exclude(
        # Compatibility for media-created events removed before the operational
        # removal state existed: they are pending institutionally with no link.
        institutional_status=Event.INSTITUTIONAL_STATUS_PENDING,
        media_organization__isnull=True,
    )


def require_media_eligible(event):
    if event.is_recurring:
        raise ValidationError('Eventos recorrentes não podem ser organizados pela Mídia.')
    return event


def start_media_organization(event, *, created_from_media=False):
    require_media_eligible(event)
    organization, created = MediaEventOrganization.objects.get_or_create(
        event=event,
        defaults={'created_from_media': created_from_media},
    )
    if created_from_media and not organization.created_from_media:
        organization.created_from_media = True
        organization.save(update_fields=['created_from_media', 'update_at'])
    return organization, created


def mark_media_organized(event):
    organization, _ = start_media_organization(event)
    if organization.status != MediaEventOrganization.STATUS_ORGANIZED:
        organization.status = MediaEventOrganization.STATUS_ORGANIZED
        organization.organized_at = timezone.now()
        organization.save(update_fields=['status', 'organized_at', 'update_at'])
    return organization


def mark_institutional_published(event):
    if event.institutional_status != Event.INSTITUTIONAL_STATUS_PUBLISHED:
        event.institutional_status = Event.INSTITUTIONAL_STATUS_PUBLISHED
        event.institutional_published_at = timezone.now()
        event.save(update_fields=['institutional_status', 'institutional_published_at', 'update_at'])
    return event
