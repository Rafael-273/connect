"""Keep data updates after the FK/index creation transaction has committed."""
from django.db import migrations, models


def preserve_existing_media(apps, schema_editor):
    Media = apps.get_model('website', 'ProjectBlockMedia')
    media = Media.objects.using(schema_editor.connection.alias).filter(
        take_analysis__isnull=True, take_analysis_status='pending',
    )
    media.filter(
        ~models.Q(trim_ranges=[]) | models.Q(trim_start_ms__gt=0) | models.Q(trim_end_ms__isnull=False),
    ).update(trim_reviewed=True)
    media.update(take_analysis_status='unavailable')


class Migration(migrations.Migration):
    dependencies = [('website', '0128_projectblockmedia_take_analysis_and_more')]
    operations = [migrations.RunPython(preserve_existing_media, migrations.RunPython.noop)]
