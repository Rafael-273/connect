from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('website', '0108_event_media_integration')]

    operations = [
        migrations.AlterField(
            model_name='mediaeventorganization',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pendente de organização'),
                    ('organized', 'Organizado'),
                    ('removed', 'Removido da mídia'),
                ],
                default='pending',
                max_length=16,
            ),
        ),
    ]
