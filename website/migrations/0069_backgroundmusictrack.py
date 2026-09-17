from django.db import migrations, models
import django.db.models.deletion
import website.models.external_media


def migrate_template_background_music(apps, schema_editor):
    Music = apps.get_model('website', 'Music')
    BackgroundMusicTrack = apps.get_model('website', 'BackgroundMusicTrack')
    MediaTemplateVersion = apps.get_model('website', 'MediaTemplateVersion')
    mapping = {}
    for version in MediaTemplateVersion.objects.exclude(background_music_id=None):
        music = Music.objects.filter(pk=version.background_music_id).first()
        if not music:
            continue
        if music.pk not in mapping:
            track = BackgroundMusicTrack.objects.create(
                name=music.name,
                category='other',
                tempo=music.tempo or '',
            )
            if music.audio_file:
                track.audio_file.name = music.audio_file.name
                track.save(update_fields=['audio_file', 'update_at'])
            mapping[music.pk] = track.pk
        version.background_music_track_id = mapping[music.pk]
        version.save(update_fields=['background_music_track_id', 'update_at'])


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0068_subtitlestyle_shadow_angle_size_blur'),
    ]

    operations = [
        migrations.CreateModel(
            name='BackgroundMusicTrack',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('update_at', models.DateTimeField(auto_now=True)),
                ('deleted', models.DateTimeField(db_index=True, editable=False, null=True)),
                ('deleted_by_cascade', models.BooleanField(default=False, editable=False)),
                ('name', models.CharField(max_length=200)),
                ('category', models.CharField(
                    choices=[
                        ('instrumental', 'Instrumental'),
                        ('worship', 'Adoração'),
                        ('calm', 'Calma'),
                        ('upbeat', 'Animada'),
                        ('cinematic', 'Cinematográfica'),
                        ('announcement', 'Anúncio'),
                        ('other', 'Outra'),
                    ],
                    default='instrumental',
                    max_length=32,
                    verbose_name='Categoria',
                )),
                ('tempo', models.CharField(
                    blank=True,
                    choices=[('rapida', 'Rápida'), ('media', 'Média'), ('lenta', 'Lenta')],
                    default='',
                    max_length=10,
                    verbose_name='Andamento',
                )),
                ('audio_file', models.FileField(
                    blank=True,
                    help_text='Arquivo de áudio usado como trilha de fundo nos templates.',
                    max_length=255,
                    storage=website.models.external_media.get_external_media_storage,
                    upload_to=website.models.external_media.background_music_upload_path,
                )),
            ],
            options={
                'verbose_name': 'Trilha de fundo',
                'verbose_name_plural': 'Trilhas de fundo',
                'ordering': ['category', 'name'],
            },
        ),
        migrations.AddField(
            model_name='mediatemplateversion',
            name='background_music_track',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='+',
                to='website.backgroundmusictrack',
            ),
        ),
        migrations.RunPython(migrate_template_background_music, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='mediatemplateversion',
            name='background_music',
        ),
        migrations.RenameField(
            model_name='mediatemplateversion',
            old_name='background_music_track',
            new_name='background_music',
        ),
        migrations.AlterField(
            model_name='mediatemplateversion',
            name='background_music',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='template_versions',
                to='website.backgroundmusictrack',
            ),
        ),
    ]
