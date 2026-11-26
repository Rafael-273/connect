from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('website', '0133_merge_schedule_and_production')]

    operations = [
        migrations.AddField(
            model_name='externalmediaproject',
            name='stage_started_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
