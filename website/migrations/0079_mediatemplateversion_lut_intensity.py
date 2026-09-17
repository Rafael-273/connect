from django.core.validators import MaxValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('website', '0078_mediatemplateblock_remove_background_voice')]

    operations = [
        migrations.AddField(
            model_name='mediatemplateversion',
            name='lut_intensity',
            field=models.PositiveSmallIntegerField(
                default=50,
                help_text='Mistura o LUT com a imagem original. 50% é o padrão recomendado.',
                validators=[MaxValueValidator(100)],
                verbose_name='Intensidade do LUT (%)',
            ),
        ),
    ]
