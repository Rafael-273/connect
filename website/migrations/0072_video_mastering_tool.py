from django.db import migrations, models
import django.db.models.deletion
import uuid
import website.models.external_media


def seed_mastering_profiles(apps, schema_editor):
    Profile = apps.get_model('website', 'MasteringProfile')
    profiles = [
        ('church_pa', 'PA Igreja', -16.0, -1.0, 11.0, True),
        ('instagram_stories', 'Instagram / Stories', -14.0, -1.0, 9.0, False),
        ('youtube', 'YouTube', -14.0, -1.0, 11.0, False),
        ('digital_standard', 'Padrão Digital', -16.0, -1.0, 11.0, False),
    ]
    for code, name, lufs, peak, dynamic_range, is_default in profiles:
        Profile.objects.update_or_create(
            code=code,
            defaults={
                'name': name,
                'target_lufs': lufs,
                'true_peak_db': peak,
                'dynamic_range_target': dynamic_range,
                'bus_compression_enabled': False,
                'limiter_enabled': True,
                'is_active': True,
                'is_default': is_default,
            },
        )


class Migration(migrations.Migration):
    dependencies = [('website', '0071_dialogue_processing')]

    operations = [
        migrations.AddField(
            model_name='masteringprofile', name='compression_limits',
            field=models.JSONField(blank=True, default=dict, verbose_name='Limites de compressão'),
        ),
        migrations.AddField(
            model_name='masteringprofile', name='dynamic_range_target',
            field=models.DecimalField(decimal_places=1, default=11.0, max_digits=4, verbose_name='Faixa dinâmica alvo (LU)'),
        ),
        migrations.AddField(
            model_name='masteringprofile', name='eq_profile',
            field=models.JSONField(blank=True, default=dict, verbose_name='Perfil de EQ'),
        ),
        migrations.AddField(
            model_name='masteringprofile', name='high_frequency_control',
            field=models.DecimalField(decimal_places=1, default=0, max_digits=4, verbose_name='Controle de agudos (dB)'),
        ),
        migrations.AddField(
            model_name='masteringprofile', name='limiter_settings',
            field=models.JSONField(blank=True, default=dict, verbose_name='Configuração do limiter'),
        ),
        migrations.AddField(
            model_name='masteringprofile', name='low_frequency_control',
            field=models.DecimalField(decimal_places=1, default=0, max_digits=4, verbose_name='Controle de graves (dB)'),
        ),
        migrations.AddField(
            model_name='masteringprofile', name='max_gain_db',
            field=models.DecimalField(decimal_places=1, default=12.0, max_digits=4, verbose_name='Ganho máximo (dB)'),
        ),
        migrations.AddField(
            model_name='masteringprofile', name='max_limiter_reduction_db',
            field=models.DecimalField(decimal_places=1, default=4.0, max_digits=4, verbose_name='Redução máxima do limiter (dB)'),
        ),
        migrations.CreateModel(
            name='VideoMasteringJob',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('deleted', models.DateTimeField(db_index=True, editable=False, null=True)),
                ('deleted_by_cascade', models.BooleanField(default=False, editable=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('update_at', models.DateTimeField(auto_now=True)),
                ('public_id', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('name', models.CharField(max_length=180)),
                ('original_video', models.FileField(max_length=255, storage=website.models.external_media.get_external_media_storage, upload_to=website.models.external_media.video_mastering_upload_path)),
                ('output_video', models.FileField(blank=True, max_length=255, storage=website.models.external_media.get_external_media_storage, upload_to=website.models.external_media.video_mastering_output_path)),
                ('status', models.CharField(choices=[('UPLOADING', 'Enviando vídeo'), ('ANALYZING', 'Analisando áudio'), ('READY', 'Pronto para masterizar'), ('MASTERING', 'Masterizando áudio'), ('VALIDATING', 'Validando resultado'), ('MUXING', 'Finalizando vídeo'), ('FINISHED', 'Finalizado'), ('ERROR', 'Erro')], default='UPLOADING', max_length=16)),
                ('progress', models.PositiveSmallIntegerField(default=0)),
                ('current_step', models.CharField(blank=True, max_length=180)),
                ('error_message', models.TextField(blank=True)),
                ('input_metrics', models.JSONField(blank=True, default=dict)),
                ('output_metrics', models.JSONField(blank=True, default=dict)),
                ('input_lufs', models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True)),
                ('output_lufs', models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True)),
                ('input_true_peak', models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True)),
                ('output_true_peak', models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True)),
                ('gain_applied', models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True)),
                ('limiter_gain_reduction', models.DecimalField(blank=True, decimal_places=2, max_digits=6, null=True)),
                ('video_reencoded', models.BooleanField(default=False)),
                ('download_count', models.PositiveIntegerField(default=0)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('finished_at', models.DateTimeField(blank=True, null=True)),
                ('celery_task_id', models.CharField(blank=True, max_length=255)),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='video_mastering_jobs', to='website.member')),
                ('mastering_profile', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='video_jobs', to='website.masteringprofile')),
            ],
            options={'verbose_name': 'Masterização de vídeo', 'verbose_name_plural': 'Masterizações de vídeo', 'ordering': ['-created_at']},
        ),
        migrations.AddIndex(
            model_name='videomasteringjob',
            index=models.Index(fields=['created_by', '-created_at'], name='website_vid_created_ba1fd6_idx'),
        ),
        migrations.AddIndex(
            model_name='videomasteringjob',
            index=models.Index(fields=['status'], name='website_vid_status_c4fdda_idx'),
        ),
        migrations.RunPython(seed_mastering_profiles, migrations.RunPython.noop),
    ]
