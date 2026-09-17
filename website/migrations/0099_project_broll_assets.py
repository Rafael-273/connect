import django.db.models.deletion
import uuid
from django.db import migrations, models
import website.models.external_media


class Migration(migrations.Migration):
    dependencies = [('website', '0098_alter_mediatemplateplugin_code_off_context')]

    operations = [
        migrations.AlterField(
            model_name='mediatemplateplugin', name='code',
            field=models.CharField(max_length=32, choices=[
                ('silence_removal', 'Corte de silêncio'), ('filler_removal', 'Remover vícios de fala'),
                ('auto_tracking', 'Auto Reframe inteligente'), ('off_context_detection', 'Detectar trechos fora de contexto'),
                ('subtitle_pt', 'Legenda PT'), ('translation_en', 'Tradução EN'), ('lut', 'Aplicar LUT'),
                ('intro', 'Intro'), ('outro', 'Tela final'), ('music', 'Música'),
                ('broll', 'B-roll (vídeos e imagens)'),
            ]),
        ),
        migrations.CreateModel(
            name='ProjectBrollAsset',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('update_at', models.DateTimeField(auto_now=True)),
                ('deleted', models.DateTimeField(db_index=True, editable=False, null=True)),
                ('deleted_by_cascade', models.BooleanField(default=False, editable=False)),
                ('public_id', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('media_type', models.CharField(choices=[('VIDEO', 'Vídeo'), ('IMAGE', 'Imagem')], max_length=12)),
                ('file', models.FileField(max_length=500, storage=website.models.external_media.get_external_media_storage, upload_to=website.models.external_media.external_media_broll_asset_path)),
                ('original_filename', models.CharField(max_length=255)),
                ('description', models.CharField(blank=True, max_length=500)),
                ('duration_ms', models.PositiveBigIntegerField(blank=True, null=True)),
                ('file_size', models.PositiveBigIntegerField(default=0)),
                ('position', models.PositiveSmallIntegerField(default=1)),
                ('defaults', models.JSONField(blank=True, default=dict)),
                ('is_enabled', models.BooleanField(default=True)),
                ('block', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='project_broll_assets', to='website.mediatemplateblock')),
                ('custom_block', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='broll_assets', to='website.projectcustomblock')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='broll_assets', to='website.externalmediaproject')),
            ],
            options={'verbose_name': 'Asset de B-roll', 'verbose_name_plural': 'Assets de B-roll', 'ordering': ['block__order', 'custom_block__position', 'position', 'pk']},
        ),
        migrations.AddConstraint(
            model_name='projectbrollasset',
            constraint=models.CheckConstraint(
                check=(models.Q(('block__isnull', False), ('custom_block__isnull', True)) | models.Q(('block__isnull', True), ('custom_block__isnull', False))),
                name='broll_asset_exactly_one_block',
            ),
        ),
    ]
