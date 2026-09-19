# Generated manually to merge divergent migration branches after production merge.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0051_seed_media_event_types_templates'),
        ('website', '0052_visitor_wants_house_of_peace'),
    ]

    operations = []
