"""Shared queries for ministry participation and legacy media entry points."""
from django.db.models import Q
from website.models import Member, Ministry, MinistryMembership, MediaSubTeam, User


def media_ministry():
    # Compatibility with the historic seed; generic organization never uses this lookup.
    return Ministry.objects.filter(code='midia_externa').first() or Ministry.objects.filter(name__iexact='Mídia Externa').first()


def ministry_members(ministry):
    return Member.objects.filter(
        is_active=True,
        pk__in=MinistryMembership.objects.filter(ministry=ministry, is_active=True).values('member_id'),
    ).order_by('name')


def media_teams():
    ministry = media_ministry()
    return MediaSubTeam.objects.filter(ministry=ministry) if ministry else MediaSubTeam.objects.none()


def team_users(team):
    return User.objects.filter(
        is_active=True,
        member__in=ministry_members(team.ministry),
        member__media_subteam_memberships__sub_team=team,
        member__media_subteam_memberships__is_active=True,
        member__media_subteam_memberships__deleted__isnull=True,
    ).distinct().order_by('member__name')


def scoped_roles(ministry):
    from website.models import MediaRole
    # Unscoped historic roles remain media-only.
    scope = Q(sub_team__ministry=ministry, sub_team__deleted__isnull=True)
    if ministry == media_ministry():
        scope |= Q(sub_team__isnull=True)
    return MediaRole.objects.filter(scope)


def deactivate_team_participation(membership):
    """Preserve participation records and historical demand assignments on departure."""
    from django.db import transaction
    from website.models import MediaSubTeamMembership
    with transaction.atomic():
        participations = MediaSubTeamMembership.objects.filter(
            sub_team__ministry_id=membership.ministry_id,
            member_id=membership.member_id, is_active=True,
        )
        for participation in participations:
            participation.is_active = False
            participation.save(update_fields=['is_active', 'update_at'])
        for team in MediaSubTeam.objects.filter(
            ministry_id=membership.ministry_id, leader_id=membership.member_id,
        ):
            team.leader = None
            team.save(update_fields=['leader', 'update_at'])


def media_team_user_map():
    from django.db.models import F
    from website.models import MediaSubTeamMembership
    result = {str(pk): [] for pk in media_teams().filter(is_active=True).values_list('pk', flat=True)}
    pairs = MediaSubTeamMembership.objects.filter(
        sub_team_id__in=result, is_active=True, member__is_active=True,
        member__deleted__isnull=True, member__user__is_active=True,
        member__ministry_memberships__ministry_id=F('sub_team__ministry_id'),
        member__ministry_memberships__is_active=True,
        member__ministry_memberships__deleted__isnull=True,
    ).values_list('sub_team_id', 'member__user_id')
    for team_id, user_id in pairs:
        result[str(team_id)].append(str(user_id))
    return result
