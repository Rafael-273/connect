from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0105_mediatask_sort_order'),
    ]

    operations = [
        migrations.AddField(
            model_name='mediatask',
            name='due_offset_days',
            field=models.IntegerField(
                blank=True,
                help_text='Negativo = antes do evento. Positivo = depois. Ex: -7 = 7 dias antes.',
                null=True,
                verbose_name='Prazo (dias em relação ao evento)',
            ),
        ),
    ]
