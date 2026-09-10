from django.db import migrations, models
import django.db.models.deletion
import website.models.external_media


class Migration(migrations.Migration):
    dependencies = [('website', '0085_mediatemplateversion_subtitle_options')]

    operations = [
        migrations.CreateModel(
            name='ColorLUT',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('deleted', models.DateTimeField(db_index=True, editable=False, null=True)),
                ('deleted_by_cascade', models.BooleanField(default=False, editable=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('update_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=160, unique=True)),
                ('description', models.TextField(blank=True)),
                ('lut_file', models.FileField(help_text='Arquivo .cube usado para aplicar a correção de cor.', max_length=255, storage=website.models.external_media.get_external_media_storage, upload_to=website.models.external_media.color_lut_upload_path)),
                ('is_active', models.BooleanField(default=True)),
            ],
            options={'verbose_name': 'LUT de cor', 'verbose_name_plural': 'LUTs de cor', 'ordering': ['name']},
        ),
        migrations.AddField(
            model_name='mediatemplateversion', name='color_lut',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='template_versions', to='website.colorlut', verbose_name='LUT de cor'),
        ),
    ]
