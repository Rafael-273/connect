from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0041_anuncioroteirofoto'),
    ]

    operations = [
        migrations.AddField(
            model_name='divisionmember',
            name='shift',
            field=models.CharField(
                choices=[('none', 'Sem turno'), ('morning', 'Manhã'), ('evening', 'Noite')],
                default='none',
                max_length=10,
                verbose_name='Turno',
            ),
        ),
        migrations.AlterUniqueTogether(
            name='divisionmember',
            unique_together={('division', 'schedule_day', 'member', 'shift')},
        ),
    ]
