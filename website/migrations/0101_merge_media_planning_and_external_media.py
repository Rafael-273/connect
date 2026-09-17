# Merge the media-planning and external-media migration branches.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0053_merge_media_production_branches'),
        ('website', '0100_projectbrollasset_preview_fields'),
    ]

    operations = []
