from .models.ministry_membership import MinistryMembership


def member_module_access(request):
    user = getattr(request, 'user', None)
    member = getattr(user, 'member', None) if user and user.is_authenticated else None
    return {
        'is_external_media_member': bool(member) and MinistryMembership.objects.filter(
            member=member,
            ministry__code='midia_externa',
            ministry__is_active=True,
            is_active=True,
        ).exists(),
    }
