from django.db import migrations, models
import website.models.external_media


class Migration(migrations.Migration):
    dependencies = [('website', '0099_project_broll_assets')]

    operations = [
        migrations.AddField(
            model_name='projectbrollasset', name='preview_file',
            field=models.FileField(blank=True, max_length=500, storage=website.models.external_media.get_external_media_storage, upload_to=website.models.external_media.external_media_broll_preview_path),
        ),
        migrations.AddField(
            model_name='projectbrollasset', name='preview_status',
            field=models.CharField(choices=[('PENDING', 'Aguardando preview'), ('READY', 'Preview pronto'), ('ERROR', 'Erro no preview')], default='PENDING', max_length=16),
        ),
        migrations.AddField(
            model_name='projectbrollasset', name='preview_error',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='projectbrollasset', name='trim_start_ms',
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='projectbrollasset', name='trim_end_ms',
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
    ]
