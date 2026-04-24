from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0035_attendancecourse_recurrence_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='courselesson',
            name='based_on_recurrence',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='courselesson',
            name='was_held',
            field=models.BooleanField(default=True),
        ),
    ]
