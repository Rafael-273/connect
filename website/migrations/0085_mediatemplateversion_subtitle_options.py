from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('website', '0084_subtitlevideoversionasset_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='mediatemplateversion',
            name='subtitles_enabled',
            field=models.BooleanField(default=True, verbose_name='Gerar legendas'),
        ),
        migrations.AddField(
            model_name='mediatemplateversion',
            name='translated_subtitles_enabled',
            field=models.BooleanField(default=True, verbose_name='Gerar legenda traduzida'),
        ),
    ]
