from django.db import migrations, models
import django.db.models.deletion
import uuid
import website.models.external_media


class Migration(migrations.Migration):
    dependencies = [('website', '0072_video_mastering_tool')]

    operations = [
        migrations.CreateModel(
            name='ExternalMediaProjectExport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('deleted', models.DateTimeField(db_index=True, editable=False, null=True)),
                ('deleted_by_cascade', models.BooleanField(default=False, editable=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('update_at', models.DateTimeField(auto_now=True)),
                ('public_id', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('format', models.CharField(choices=[('PREMIERE', 'Adobe Premiere Pro')], default='PREMIERE', max_length=16)),
                ('status', models.CharField(choices=[('PREPARING', 'Preparando'), ('BUILDING_TIMELINE', 'Construindo timeline'), ('CONVERTING', 'Convertendo projeto'), ('PACKAGING_ASSETS', 'Organizando arquivos'), ('VALIDATING', 'Validando exportação'), ('COMPRESSING', 'Compactando pacote'), ('FINISHED', 'Finalizado'), ('ERROR', 'Erro')], default='PREPARING', max_length=24)),
                ('progress', models.PositiveSmallIntegerField(default=0)),
                ('current_step', models.CharField(blank=True, max_length=180)),
                ('error_message', models.TextField(blank=True)),
                ('archive', models.FileField(blank=True, max_length=255, storage=website.models.external_media.get_external_media_storage, upload_to=website.models.external_media.project_export_path)),
                ('timeline_json', models.FileField(blank=True, max_length=255, storage=website.models.external_media.get_external_media_storage, upload_to=website.models.external_media.project_export_path)),
                ('compatibility', models.JSONField(blank=True, default=dict)),
                ('validation_report', models.JSONField(blank=True, default=dict)),
                ('celery_task_id', models.CharField(blank=True, max_length=255)),
                ('download_count', models.PositiveIntegerField(default=0)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='external_media_exports', to='website.member')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='exports', to='website.externalmediaproject')),
            ],
            options={'verbose_name': 'Exportação de projeto de mídia', 'verbose_name_plural': 'Exportações de projetos de mídia', 'ordering': ['-created_at']},
        ),
        migrations.AddIndex(
            model_name='externalmediaprojectexport',
            index=models.Index(fields=['project', '-created_at'], name='website_ext_project_a76efe_idx'),
        ),
        migrations.AddIndex(
            model_name='externalmediaprojectexport',
            index=models.Index(fields=['status'], name='website_ext_status_985ac0_idx'),
        ),
    ]
