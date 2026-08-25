from django.db import migrations, models


def make_existing_blocks_unlimited(apps, schema_editor):
    Block = apps.get_model('website', 'MediaTemplateBlock')
    Block.objects.update(allows_multiple=True, max_occurrences=0)


class Migration(migrations.Migration):
    dependencies = [('website', '0074_external_media_job_processing_project')]

    operations = [
        migrations.AlterField(
            model_name='mediatemplateblock',
            name='allows_multiple',
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name='mediatemplateblock',
            name='max_occurrences',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.RunPython(make_existing_blocks_unlimited, migrations.RunPython.noop),
    ]
