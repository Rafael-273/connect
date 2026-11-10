from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0111_make_instagram_mastering_more_transparent'),
    ]

    operations = [
        migrations.AddField(
            model_name='mediatemplateversion',
            name='intro_enabled',
            field=models.BooleanField(default=True, verbose_name='Usar vídeo de abertura'),
        ),
        migrations.AddField(
            model_name='mediatemplateversion',
            name='outro_enabled',
            field=models.BooleanField(default=True, verbose_name='Usar vídeo de encerramento'),
        ),
    ]
