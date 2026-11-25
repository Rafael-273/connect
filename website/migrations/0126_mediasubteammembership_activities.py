from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('website', '0125_schedule_availability_coverage'),
    ]

    operations = [
        migrations.AddField(
            model_name='mediasubteammembership',
            name='activities',
            field=models.ManyToManyField(blank=True, related_name='member_participations', to='website.mediateamactivity', verbose_name='Atividades na equipe'),
        ),
    ]
