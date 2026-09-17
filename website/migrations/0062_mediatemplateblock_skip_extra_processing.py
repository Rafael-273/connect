from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0061_subtitle_style_background_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='mediatemplateblock',
            name='skip_extra_processing',
            field=models.BooleanField(default=False),
        ),
    ]
