from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0064_fix_external_media_brand_glossary'),
    ]

    operations = [
        migrations.AddField(
            model_name='subtitlestyle',
            name='font_weight',
            field=models.PositiveSmallIntegerField(
                choices=[
                    (400, 'Regular'),
                    (500, 'Medium'),
                    (600, 'SemiBold'),
                    (700, 'Bold'),
                    (800, 'ExtraBold'),
                    (900, 'Black'),
                ],
                default=700,
            ),
        ),
        migrations.AlterField(
            model_name='subtitlestyle',
            name='alignment',
            field=models.PositiveSmallIntegerField(
                choices=[
                    (1, 'Inferior esquerdo'),
                    (2, 'Inferior centro'),
                    (3, 'Inferior direito'),
                    (4, 'Meio esquerdo'),
                    (5, 'Meio centro'),
                    (6, 'Meio direito'),
                    (7, 'Superior esquerdo'),
                    (8, 'Superior centro'),
                    (9, 'Superior direito'),
                ],
                default=2,
            ),
        ),
    ]
