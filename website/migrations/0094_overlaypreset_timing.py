from django.db import migrations, models


def set_seed_timing(apps, schema_editor):
    OverlayPreset = apps.get_model('website', 'OverlayPreset')
    settings = {
        'qr-filadelfia': ('BLOCK_END', 8000),
        'event-date-filadelfia': ('BLOCK_START', 5000),
        'lower-third-filadelfia': ('BLOCK_START', 5000),
        'cta-filadelfia': ('BLOCK_END', 5000),
    }
    for code, (timing_mode, duration_ms) in settings.items():
        OverlayPreset.objects.filter(code=code).update(
            timing_mode=timing_mode, duration_ms=duration_ms,
        )


class Migration(migrations.Migration):
    dependencies = [('website', '0093_generic_overlays')]

    operations = [
        migrations.AddField(
            model_name='overlaypreset', name='timing_mode',
            field=models.CharField(
                choices=[
                    ('BLOCK_START', 'Início do bloco'),
                    ('BLOCK_END', 'Final do bloco'),
                    ('AUTO_BEST_MOMENT', 'Melhor momento automático'),
                ], default='BLOCK_START', max_length=24,
            ),
        ),
        migrations.AddField(
            model_name='overlaypreset', name='duration_ms',
            field=models.PositiveIntegerField(default=5000),
        ),
        migrations.RunPython(set_seed_timing, migrations.RunPython.noop),
    ]
