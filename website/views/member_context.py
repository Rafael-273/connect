"""Shared context for personal member pages and ministry tools."""

from django.db.models import Q
from django.utils import timezone

from ..models.ministry_membership import MinistryMembership
from ..models.schedule import MonthlySchedule
from ..services.ministry_organization import media_ministry
from .mixins import MinistrationContextMixin


class MemberPersonalContextMixin(MinistrationContextMixin):
    def _build_base_context(self, member):
        flags = self._get_ministry_flags(member)
        member_schedules = self._get_current_schedules(member)
        media = media_ministry() if flags['is_media_leader'] else None
        memberships = list(
            member.ministry_memberships.filter(is_active=True, ministry__is_active=True)
            .select_related('ministry')
            .order_by('ministry__name')
        )

        return {
            'member': member,
            'member_nav_active': 'home',
            'can_consolidate': flags['can_consolidate'],
            'is_approver': flags['is_approver'],
            'is_ministration_member': flags['is_ministration'],
            'is_media_member': flags['is_media_leader'],
            # Kept as a separate context name because existing dashboard cards
            # and the bottom navigation already use it.
            'is_external_media_member': member.ministry_memberships.filter(
                is_active=True, ministry__is_active=True, ministry__code='midia_externa',
            ).exists(),
            'is_media_leader': flags['is_media_leader'],
            'media_ministry_id': media.pk if media else None,
            'is_boas_vindas_member': flags['is_boas_vindas'],
            'is_pastor': member.church_role == 'pastor',
            'is_moderacao_member': flags['is_moderacao'],
            'can_music_member': flags['is_worship'],
            'member_schedules': member_schedules,
            'member_ministries': memberships,
            'is_new_member': (
                member.church_role == 'member'
                and not any([flags['has_ministries'], flags['is_approver'], flags['can_consolidate']])
            ),
            'is_house_of_peace_member': flags['has_ministries'],
        }

    def _get_ministry_flags(self, member):
        return {
            'has_ministries': member.ministry_memberships.filter(is_active=True).exists(),
            'is_approver': member.is_approver,
            'can_consolidate': member.is_available_to_consolidate,
            'is_ministration': self.get_ministration_status(member),
            'is_media': self.get_media_status(member),
            'is_media_leader': self.get_media_leader_status(member),
            'is_worship': self.get_worship_status(member),
            'is_boas_vindas': MinistryMembership.objects.filter(
                member=member,
                is_active=True,
                ministry__name__icontains='boas vindas',
            ).exists(),
            'is_moderacao': MinistryMembership.objects.filter(
                member=member,
                is_active=True,
                ministry__name__icontains='modera',
            ).exists(),
        }

    def _get_current_schedules(self, member):
        now = timezone.now()
        next_month = now.month % 12 + 1
        next_year = now.year + 1 if now.month == 12 else now.year
        ministry_ids = MinistryMembership.objects.filter(
            member=member, is_active=True
        ).values_list('ministry_id', flat=True)
        return list(
            MonthlySchedule.objects
            .filter(
                ministry_id__in=ministry_ids,
            )
            .filter(
                Q(month=now.month, year=now.year) |
                Q(month=next_month, year=next_year)
            )
            .select_related('ministry')
            .order_by('year', 'month', 'ministry__name', 'title')
        )

    def _add_followup_context(self, member, context):
        if hasattr(member, 'performed_followups'):
            context['followups_responsible'] = (
                member.performed_followups
                .filter(end_date__isnull=True)
                .select_related('accompanied')[:5]
            )
        if hasattr(member, 'received_followups'):
            context['followups_received'] = (
                member.received_followups
                .filter(end_date__isnull=True)
                .select_related('responsible')[:5]
            )
