from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0062_mediatemplateblock_skip_extra_processing'),
    ]

    operations = [
        migrations.AddField(
            model_name='projectblockmedia',
            name='trim_start_ms',
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='projectblockmedia',
            name='trim_end_ms',
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
    ]
