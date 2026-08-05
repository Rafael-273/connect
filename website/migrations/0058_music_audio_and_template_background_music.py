from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0057_shorten_external_media_block_paths'),
    ]

    operations = [
        migrations.AddField(
            model_name='music',
            name='audio_file',
            field=models.FileField(
                blank=True,
                help_text='Arquivo de áudio usado como trilha ou referência sonora.',
                max_length=255,
                upload_to='music/audio/',
            ),
        ),
        migrations.AddField(
            model_name='mediatemplateversion',
            name='background_music',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.PROTECT,
                related_name='external_media_versions',
                to='website.music',
            ),
        ),
    ]
