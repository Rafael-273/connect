from django.db import migrations, models
import django.db.models.deletion
import website.models.external_media


def seed_overlay_presets(apps, schema_editor):
    OverlayPreset = apps.get_model('website', 'OverlayPreset')
    presets = [
        ('QR Filadélfia', 'qr-filadelfia', 'QR_CODE_CARD', {'background': '#FFFFFF', 'color': '#111827', 'font': 'Montserrat-Bold.ttf', 'font_size': 42, 'padding': 34, 'border_radius': 22, 'qr_color': '#111111', 'qr_background': '#FFFFFF'}, {'x': .86, 'y': .76, 'width': .18}, {'type': 'SLIDE_UP', 'duration': .4, 'easing': 'ease-out'}),
        ('Data de evento Filadélfia', 'event-date-filadelfia', 'TEXT', {'background': '#C90905', 'background_opacity': .94, 'color': '#FFFFFF', 'font': 'Montserrat-Bold.ttf', 'font_size': 54, 'padding': 28, 'border_radius': 14}, {'x': .5, 'y': .82, 'width': .48}, {'type': 'SLIDE_UP', 'duration': .35, 'easing': 'ease-out'}),
        ('Lower Third Filadélfia', 'lower-third-filadelfia', 'TEXT', {'background': '#C90905', 'background_opacity': .92, 'color': '#FFFFFF', 'font': 'Montserrat-Bold.ttf', 'font_size': 46, 'padding': 24, 'border_radius': 10}, {'x': .25, 'y': .8, 'width': .38}, {'type': 'SLIDE_LEFT', 'duration': .35, 'easing': 'ease-out'}),
        ('CTA Filadélfia', 'cta-filadelfia', 'TEXT', {'background': '#FFFFFF', 'background_opacity': .96, 'color': '#C90905', 'font': 'Montserrat-Bold.ttf', 'font_size': 50, 'padding': 26, 'border_radius': 16}, {'x': .5, 'y': .82, 'width': .42}, {'type': 'POP', 'duration': .3, 'easing': 'ease-out'}),
    ]
    for name, code, overlay_type, style, position, animation in presets:
        OverlayPreset.objects.get_or_create(code=code, defaults={
            'name': name, 'overlay_type': overlay_type, 'style': style,
            'position': position, 'animation': animation, 'is_active': True,
        })


class Migration(migrations.Migration):
    dependencies = [('website', '0092_mediatemplateversion_audio_noise_cleanup')]

    operations = [
        migrations.CreateModel(
            name='OverlayPreset',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('deleted', models.DateTimeField(db_index=True, editable=False, null=True)),
                ('deleted_by_cascade', models.BooleanField(default=False, editable=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('update_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=120, unique=True)),
                ('code', models.SlugField(max_length=80, unique=True)),
                ('overlay_type', models.CharField(choices=[('TEXT', 'Texto'), ('QR_CODE', 'QR Code'), ('QR_CODE_CARD', 'QR Code + CTA'), ('IMAGE', 'Imagem')], default='TEXT', max_length=24)),
                ('style', models.JSONField(blank=True, default=dict)),
                ('position', models.JSONField(blank=True, default=dict)),
                ('animation', models.JSONField(blank=True, default=dict)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={'ordering': ['name'], 'verbose_name': 'Preset de overlay', 'verbose_name_plural': 'Presets de overlays'},
        ),
        migrations.AddField(
            model_name='mediatemplateblock',
            name='overlay_definitions',
            field=models.JSONField(blank=True, default=list, help_text='Definições estruturadas de textos, QR Codes e imagens preenchidas em cada projeto.', verbose_name='Overlays deste bloco'),
        ),
        migrations.CreateModel(
            name='ProjectOverlay',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('deleted', models.DateTimeField(db_index=True, editable=False, null=True)),
                ('deleted_by_cascade', models.BooleanField(default=False, editable=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('update_at', models.DateTimeField(auto_now=True)),
                ('overlay_id', models.CharField(max_length=160)),
                ('source', models.CharField(choices=[('TEMPLATE', 'Template'), ('MANUAL', 'Adicionado no preview')], default='TEMPLATE', max_length=16)),
                ('overlay_type', models.CharField(choices=[('TEXT', 'Texto'), ('QR_CODE', 'QR Code'), ('QR_CODE_CARD', 'QR Code + CTA'), ('IMAGE', 'Imagem')], max_length=24)),
                ('purpose', models.CharField(blank=True, max_length=80)),
                ('content_schema', models.JSONField(blank=True, default=dict)),
                ('content', models.JSONField(blank=True, default=dict)),
                ('position', models.JSONField(blank=True, default=dict)),
                ('style', models.JSONField(blank=True, default=dict)),
                ('animation', models.JSONField(blank=True, default=dict)),
                ('timing_mode', models.CharField(choices=[('BLOCK_START', 'Início do bloco'), ('BLOCK_END', 'Final do bloco'), ('MANUAL', 'Manual'), ('AUTO_BEST_MOMENT', 'Melhor momento automático')], default='BLOCK_START', max_length=24)),
                ('start_ms', models.PositiveBigIntegerField(default=0)),
                ('end_ms', models.PositiveBigIntegerField(blank=True, null=True)),
                ('duration_ms', models.PositiveIntegerField(default=5000)),
                ('allowed_overrides', models.JSONField(blank=True, default=list)),
                ('portability', models.CharField(choices=[('PORTABLE', 'Editável'), ('APPROXIMATE', 'Aproximado'), ('PRE_RENDERED', 'Pré-renderizado')], default='APPROXIMATE', max_length=16)),
                ('image_file', models.FileField(blank=True, max_length=500, storage=website.models.external_media.get_external_media_storage, upload_to=website.models.external_media.external_media_overlay_asset_path)),
                ('is_required', models.BooleanField(default=False)),
                ('is_enabled', models.BooleanField(default=True)),
                ('block', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='project_overlays', to='website.mediatemplateblock')),
                ('preset', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='project_overlays', to='website.overlaypreset')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='overlays', to='website.externalmediaproject')),
            ],
            options={'ordering': ['block__order', 'created_at', 'pk']},
        ),
        migrations.AddConstraint(
            model_name='projectoverlay',
            constraint=models.UniqueConstraint(fields=('project', 'overlay_id'), name='unique_project_overlay_id'),
        ),
        migrations.RunPython(seed_overlay_presets, migrations.RunPython.noop),
    ]
