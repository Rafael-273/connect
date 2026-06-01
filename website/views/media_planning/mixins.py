from django.contrib import messages
from django.shortcuts import redirect

from ...models.ministry_membership import MinistryMembership
from ..mixins import MemberRequiredMixin

MEDIA_MINISTRY_NAME = 'Mídia Externa'


def _get_media_membership(member):
    """Returns the active MinistryMembership for the Mídia Externa ministry, or None."""
    return (
        MinistryMembership.objects.filter(
            member=member,
            ministry__name__iexact=MEDIA_MINISTRY_NAME,
            is_active=True,
        )
        .select_related('ministry')
        .first()
    )


class MediaMemberRequiredMixin(MemberRequiredMixin):
    """Requires the user to be an active member of the Mídia ministry."""

    media_membership = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)

        member = getattr(request.user, 'member', None)
        if member:
            self.media_membership = _get_media_membership(member)
            if not self.media_membership:
                messages.error(
                    request,
                    'Você não tem acesso ao módulo de Planejamento de Mídia.',
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
            'is_media_leader': bool(
                self.media_membership and self.media_membership.role == 'leader'
            ),
            'nav_active': 'media',
        }

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(self._nav_context())
        return ctx


class MediaLeaderRequiredMixin(MemberRequiredMixin):
    """Requires the user to be a leader of the Mídia ministry."""

    media_membership = None

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)

        member = getattr(request.user, 'member', None)
        if member:
            membership = _get_media_membership(member)
            if not membership:
                messages.error(
                    request,
                    'Você não tem acesso ao módulo de Planejamento de Mídia.',
                )
                return redirect('member_dashboard')
            if membership.role != 'leader':
                messages.error(request, 'Apenas líderes podem realizar esta ação.')
                return redirect('media_dashboard')
            self.media_membership = membership

        return super().dispatch(request, *args, **kwargs)

    def _nav_context(self):
        return {
            'can_consolidate': getattr(self.member, 'is_available_to_consolidate', False),
            'is_ministration_member': MinistryMembership.objects.filter(
                member=self.member,
                ministry__name__icontains='ministração',
                is_active=True,
            ).exists(),
            'is_media_member': True,
            'is_media_leader': True,
            'nav_active': 'media',
        }

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(self._nav_context())
        return ctx
