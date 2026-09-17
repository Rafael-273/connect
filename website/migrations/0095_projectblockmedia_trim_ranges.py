from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('website', '0094_overlaypreset_timing')]

    operations = [
        migrations.AddField(
            model_name='projectblockmedia', name='trim_ranges',
            field=models.JSONField(blank=True, default=list, help_text='Trechos preservados do vídeo, em milissegundos e na ordem de reprodução.'),
        ),
    ]
