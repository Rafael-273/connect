from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0034_attendance_courses'),
    ]

    operations = [
        migrations.AddField(
            model_name='attendancecourse',
            name='recurrence_interval_days',
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='attendancecourse',
            name='recurrence_type',
            field=models.CharField(
                choices=[
                    ('none', 'Sem recorrencia fixa'),
                    ('weekly', 'Semanal'),
                    ('biweekly', 'Quinzenal'),
                    ('custom_days', 'A cada X dias'),
                ],
                default='none',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='attendancecourse',
            name='recurrence_weekday',
            field=models.CharField(
                blank=True,
                choices=[
                    ('monday', 'Segunda-feira'),
                    ('tuesday', 'Terca-feira'),
                    ('wednesday', 'Quarta-feira'),
                    ('thursday', 'Quinta-feira'),
                    ('friday', 'Sexta-feira'),
                    ('saturday', 'Sabado'),
                    ('sunday', 'Domingo'),
                ],
                max_length=12,
                null=True,
            ),
        ),
    ]
