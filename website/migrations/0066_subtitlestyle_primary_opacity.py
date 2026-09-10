from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0065_subtitlestyle_font_weight'),
    ]

    operations = [
        migrations.AddField(
            model_name='subtitlestyle',
            name='primary_opacity',
            field=models.PositiveSmallIntegerField(default=100),
        ),
    ]
