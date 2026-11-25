from django.db import migrations


def populate_default_activities(apps, schema_editor):
    Membership = apps.get_model('website', 'MediaSubTeamMembership')
    Activity = apps.get_model('website', 'MediaTeamActivity')
    through = Membership.activities.through
    db = schema_editor.connection.alias
    existing = set(through.objects.using(db).values_list('mediasubteammembership_id', flat=True))
    activities_by_team = {}
    for team_id, activity_id in Activity.objects.using(db).filter(is_active=True, deleted__isnull=True).values_list('sub_team_id', 'id'):
        activities_by_team.setdefault(team_id, []).append(activity_id)
    links = []
    for membership_id, team_id in Membership.objects.using(db).filter(deleted__isnull=True).values_list('id', 'sub_team_id'):
        if membership_id not in existing:
            links.extend(through(mediasubteammembership_id=membership_id, mediateamactivity_id=activity_id)
                         for activity_id in activities_by_team.get(team_id, []))
    through.objects.using(db).bulk_create(links, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [('website', '0126_mediasubteammembership_activities')]
    operations = [migrations.RunPython(populate_default_activities, migrations.RunPython.noop)]
