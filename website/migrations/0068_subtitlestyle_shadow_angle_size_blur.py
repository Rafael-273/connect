from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0067_subtitlestyle_shadow_opacity_bg_height'),
    ]

    operations = [
        migrations.AddField(
            model_name='subtitlestyle',
            name='shadow_angle',
            field=models.PositiveSmallIntegerField(default=45),
        ),
        migrations.AddField(
            model_name='subtitlestyle',
            name='shadow_size',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='subtitlestyle',
            name='shadow_blur',
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
