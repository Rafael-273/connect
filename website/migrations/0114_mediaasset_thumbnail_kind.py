from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0113_allow_project_level_broll_assets'),
    ]

    operations = [
        migrations.AlterField(
            model_name='mediaasset',
            name='kind',
            field=models.CharField(
                choices=[
                    ('VIDEO', 'Vídeo renderizado'),
                    ('THUMBNAIL', 'Miniatura do vídeo'),
                    ('SRT', 'Legenda SRT'),
                    ('VTT', 'Legenda VTT'),
                ],
                max_length=20,
            ),
        ),
    ]
