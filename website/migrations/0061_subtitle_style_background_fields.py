from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0060_translated_subtitle_styles'),
    ]

    operations = [
        migrations.AddField(
            model_name='subtitlestyle',
            name='background_color',
            field=models.CharField(default='#000000', max_length=10),
        ),
        migrations.AddField(
            model_name='subtitlestyle',
            name='background_enabled',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='subtitlestyle',
            name='background_opacity',
            field=models.PositiveSmallIntegerField(default=70),
        ),
        migrations.AddField(
            model_name='subtitlestyle',
            name='background_padding_x',
            field=models.PositiveSmallIntegerField(default=14),
        ),
        migrations.AddField(
            model_name='subtitlestyle',
            name='background_padding_y',
            field=models.PositiveSmallIntegerField(default=8),
        ),
        migrations.AddField(
            model_name='subtitlestyle',
            name='background_radius',
            field=models.PositiveSmallIntegerField(default=10),
        ),
    ]
