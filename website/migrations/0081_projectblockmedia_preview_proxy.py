from django.db import migrations, models
import website.models.external_media


class Migration(migrations.Migration):
    dependencies = [('website', '0080_remove_glossaryterm_notes')]

    operations = [
        migrations.AddField(model_name='projectblockmedia', name='preview_file', field=models.FileField(blank=True, max_length=255, storage=website.models.external_media.get_external_media_storage, upload_to=website.models.external_media.external_media_project_preview_path)),
        migrations.AddField(model_name='projectblockmedia', name='preview_status', field=models.CharField(choices=[('PENDING', 'Preparando preview'), ('READY', 'Preview pronto'), ('ERROR', 'Falha no preview')], default='PENDING', max_length=16)),
        migrations.AddField(model_name='projectblockmedia', name='preview_error', field=models.CharField(blank=True, max_length=255)),
    ]
