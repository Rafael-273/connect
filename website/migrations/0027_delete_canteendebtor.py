from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0026_testimony_content_to_title'),
    ]

    operations = [
        migrations.DeleteModel(
            name='CanteenDebtor',
        ),
    ]
