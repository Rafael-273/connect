from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0059_alter_mediatemplateplugin_code'),
    ]

    operations = [
        migrations.AddField(
            model_name='externalmediajob',
            name='translated_subtitle_style',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='translated_jobs',
                to='website.subtitlestyle',
            ),
        ),
        migrations.AddField(
            model_name='mediatemplateversion',
            name='translated_subtitle_style',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='translated_template_versions',
                to='website.subtitlestyle',
            ),
        ),
    ]
