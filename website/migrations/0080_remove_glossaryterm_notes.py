from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0079_mediatemplateversion_lut_intensity'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='glossaryterm',
            name='notes',
        ),
    ]
