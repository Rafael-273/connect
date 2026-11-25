from django.db import migrations


def restore_confirmation_for_corrections(apps, schema_editor):
    Task = apps.get_model('website', 'MediaTask')
    Requirement = apps.get_model('website', 'MediaDeliveryRequirement')
    Delivery = apps.get_model('website', 'MediaDelivery')
    alias = schema_editor.connection.alias
    for task in Task.objects.using(alias).filter(status='in_review', deleted__isnull=True).iterator():
        has_correction = False
        requirements = Requirement.objects.using(alias).filter(task_id=task.pk, active=True, deleted__isnull=True)
        for requirement in requirements.iterator():
            versions = list(Delivery.objects.using(alias).filter(
                requirement_id=requirement.pk, deleted__isnull=True,
            ).order_by('-version').values('pk', 'status')[:2])
            if len(versions) == 2 and versions[0]['status'] == 'submitted' and versions[1]['status'] == 'rejected':
                Delivery.objects.using(alias).filter(pk=versions[0]['pk'], status='submitted').update(status='draft')
                has_correction = True
        if has_correction:
            Task.objects.using(alias).filter(pk=task.pk, status='in_review').update(status='in_progress')


class Migration(migrations.Migration):
    dependencies = [('website', '0125_per_delivery_review')]
    operations = [migrations.RunPython(restore_confirmation_for_corrections, migrations.RunPython.noop)]
