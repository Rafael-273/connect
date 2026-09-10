from django.db import migrations, models


def enable_mixing_for_templates_with_music(apps, schema_editor):
    Version = apps.get_model('website', 'MediaTemplateVersion')
    Version.objects.filter(background_music__isnull=False).update(audio_mixing_enabled=True)
    Version.objects.exclude(music_file='').update(audio_mixing_enabled=True)


class Migration(migrations.Migration):
    dependencies = [('website', '0075_unlimited_media_template_block_uploads')]

    operations = [
        migrations.AlterField(
            model_name='mediatemplateversion',
            name='audio_mixing_enabled',
            field=models.BooleanField(default=True, verbose_name='Mixagem inteligente'),
        ),
        migrations.RunPython(enable_mixing_for_templates_with_music, migrations.RunPython.noop),
    ]
