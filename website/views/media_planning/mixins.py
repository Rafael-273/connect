from django.contrib import messages
from django.shortcuts import redirect

from ...models.ministry_membership import MinistryMembership
from ..mixins import MemberRequiredMixin
from website.services.ministry_organization import media_ministry

MEDIA_MINISTRY_NAME = 'Mídia Externa'


def _get_media_membership(member):
    """Returns the active MinistryMembership for the Mídia Externa ministry, or None."""
    return (
        MinistryMembership.objects.filter(
            member=member,
            ministry=media_ministry(),
            ministry__is_active=True,
            member__is_active=True,
            is_active=True,
        )
        .select_related('ministry')
        .first()
    )


def _is_media_org_admin(user):
    return user.is_staff or getattr(user, 'user_type', None) == 'admin'


class MediaMemberRequiredMixin(MemberRequiredMixin):
    """Requires the user to be a leader of the Mídia Externa ministry."""

    media_membership = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)

        member = getattr(request.user, 'member', None)
        if member:
            membership = _get_media_membership(member)
            if _is_media_org_admin(request.user):
                self.media_membership = membership
            elif membership and membership.role == 'leader':
                self.media_membership = membership
            else:
                messages.error(
                    request,
                    'Apenas líderes da Mídia Externa têm acesso a esta área.',
                )
                return redirect('member_dashboard')

        return super().dispatch(request, *args, **kwargs)

    def _nav_context(self):
        """Base nav context shared by all media planning views."""
        return {
            'can_consolidate': getattr(self.member, 'is_available_to_consolidate', False),
            'is_ministration_member': MinistryMembership.objects.filter(
                member=self.member,
                ministry__name__icontains='ministração',
                is_active=True,
            ).exists(),
            'is_media_member': True,
            'is_media_leader': True,
            'nav_active': 'external_media',
        }

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(self._nav_context())
        return ctx


class MediaLeaderRequiredMixin(MediaMemberRequiredMixin):
    """Alias for leader-only media planning views (same gate as MediaMemberRequiredMixin)."""

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(self._nav_context())
        return ctx
