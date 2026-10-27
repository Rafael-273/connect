from django.db import migrations, models
import django.db.models.deletion
import website.models.external_media


class Migration(migrations.Migration):
    dependencies = [('website', '0118_editor_persistence')]
    operations = [
        migrations.CreateModel(name='ProjectPreviewArtifact', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('key', models.CharField(max_length=64)),
            ('kind', models.CharField(max_length=24)),
            ('status', models.CharField(default='PENDING', max_length=16)),
            ('source', models.JSONField(default=dict)),
            ('options', models.JSONField(default=dict)),
            ('file', models.FileField(blank=True, max_length=500, storage=website.models.external_media.get_external_media_storage, upload_to='external_media/preview_artifacts/')),
            ('lease', models.CharField(blank=True, max_length=32)),
            ('requested_at', models.DateTimeField()),
            ('error', models.CharField(blank=True, max_length=255)),
            ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='preview_artifacts', to='website.externalmediaproject')),
        ]),
        migrations.AddConstraint(model_name='projectpreviewartifact', constraint=models.UniqueConstraint(fields=('project', 'key'), name='unique_project_preview_artifact')),
    ]
