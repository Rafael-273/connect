from django.db import migrations, models
from django.core.validators import MaxValueValidator


class Migration(migrations.Migration):
    dependencies = [('website', '0086_colorlut_mediatemplateversion_color_lut')]

    operations = [
        migrations.AddField(
            model_name='colorlut', name='default_intensity',
            field=models.PositiveSmallIntegerField(default=50, help_text='Valor sugerido ao selecionar este LUT em um template.', validators=[MaxValueValidator(100)], verbose_name='Intensidade padrão (%)'),
        ),
    ]
