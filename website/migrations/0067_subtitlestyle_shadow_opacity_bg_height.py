from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0066_subtitlestyle_primary_opacity'),
    ]

    operations = [
        migrations.AddField(
            model_name='subtitlestyle',
            name='shadow_opacity',
            field=models.PositiveSmallIntegerField(default=70),
        ),
        migrations.AddField(
            model_name='subtitlestyle',
            name='background_height_percent',
            field=models.PositiveSmallIntegerField(default=100),
        ),
    ]
