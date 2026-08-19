from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0051_member_church_role'),
    ]

    operations = [
        migrations.AddField(
            model_name='visitor',
            name='wants_house_of_peace',
            field=models.BooleanField(default=False, verbose_name='Tem interesse em Casa de Paz?'),
        ),
    ]
