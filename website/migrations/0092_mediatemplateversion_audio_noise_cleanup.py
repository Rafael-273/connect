from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0091_projectblockmedia_camera_key'),
    ]

    operations = [
        migrations.AddField(
            model_name='mediatemplateversion',
            name='audio_noise_cleanup_enabled',
            field=models.BooleanField(default=False, verbose_name='Limpeza de ruído'),
        ),
        migrations.AddField(
            model_name='mediatemplateversion',
            name='audio_noise_cleanup_config',
            field=models.JSONField(blank=True, default=dict, verbose_name='Configuração avançada de limpeza de ruído'),
        ),
    ]
