"""Keep organization participation consistent with ministry departures."""
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from website.models import MinistryMembership
from website.services.ministry_organization import deactivate_team_participation


@receiver(post_save, sender=MinistryMembership)
def ministry_membership_saved(sender, instance, **kwargs):
    if not instance.is_active or instance.deleted:
        deactivate_team_participation(instance)


@receiver(pre_delete, sender=MinistryMembership)
def ministry_membership_deleted(sender, instance, **kwargs):
    deactivate_team_participation(instance)
