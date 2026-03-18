from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0028_prayerrequest_alter_user_user_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='evangelized',
            name='wants_peace_house',
            field=models.BooleanField(default=False, verbose_name='Deseja casa de paz?'),
        ),
    ]
