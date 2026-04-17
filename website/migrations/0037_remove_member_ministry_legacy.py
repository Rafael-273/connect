from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0036_courselesson_was_held_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='member',
            name='ministry',
        ),
    ]
