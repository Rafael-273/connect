"""Legacy schedules keep their visibility; assistant drafts require explicit publication."""
from django.db.models import Q


def member_visible_schedules(queryset):
    return queryset.filter(
        Q(assistant_state__isnull=True) |
        Q(assistant_state__status='published', assistant_state__deleted__isnull=True, is_published=True)
    )


def can_manage_media_schedules(user, ministry):
    from website.models import MinistryMembership
    if not user.is_authenticated:
        return False
    if user.is_staff or getattr(user, 'user_type', None) == 'admin':
        return True
    return ministry.is_active and MinistryMembership.objects.filter(
        ministry=ministry, member__user=user, member__is_active=True,
        member__deleted__isnull=True, is_active=True, role='leader',
    ).exists()
