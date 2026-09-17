from django.db import migrations, models


def optimize_render_presets(apps, schema_editor):
    RenderPreset = apps.get_model('website', 'RenderPreset')
    RenderPreset.objects.filter(video_codec='libx264').update(
        video_crf=23,
        audio_codec='copy',
    )


def restore_render_presets(apps, schema_editor):
    RenderPreset = apps.get_model('website', 'RenderPreset')
    RenderPreset.objects.filter(video_codec='libx264').update(
        video_crf=20,
        audio_codec='aac',
    )


class Migration(migrations.Migration):
    dependencies = [('website', '0053_alter_externalmediajob_original_video_and_more')]

    operations = [
        migrations.AlterField(
            model_name='renderpreset',
            name='audio_codec',
            field=models.CharField(default='copy', max_length=30),
        ),
        migrations.AlterField(
            model_name='renderpreset',
            name='video_crf',
            field=models.PositiveSmallIntegerField(default=23),
        ),
        migrations.RunPython(optimize_render_presets, restore_render_presets),
    ]
