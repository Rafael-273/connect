# Generated manually to preserve the current event records during deployment.
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('website', '0107_media_planning_template_item_step'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='institutional_published_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='event',
            name='institutional_status',
            field=models.CharField(
                choices=[('pending', 'Pendente de publicação institucional'), ('published', 'Publicado institucionalmente')],
                default='published', max_length=16, verbose_name='Status institucional',
            ),
        ),
        migrations.CreateModel(
            name='MediaEventOrganization',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('update_at', models.DateTimeField(auto_now=True)),
                ('deleted', models.DateTimeField(db_index=True, editable=False, null=True)),
                ('deleted_by_cascade', models.BooleanField(default=False, editable=False)),
                ('status', models.CharField(choices=[('pending', 'Pendente de organização'), ('organized', 'Organizado')], default='pending', max_length=16)),
                ('created_from_media', models.BooleanField(default=False)),
                ('organized_at', models.DateTimeField(blank=True, null=True)),
                ('event', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='media_organization', to='website.event')),
            ],
            options={
                'verbose_name': 'Organização de mídia do evento',
                'verbose_name_plural': 'Organizações de mídia dos eventos',
            },
        ),
    ]
