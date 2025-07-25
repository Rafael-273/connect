# Generated by Django 4.2.16 on 2025-06-04 04:19

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0004_alter_visitor_conversion'),
    ]

    operations = [
        migrations.CreateModel(
            name='Event',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('deleted', models.DateTimeField(db_index=True, editable=False, null=True)),
                ('deleted_by_cascade', models.BooleanField(default=False, editable=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('update_at', models.DateTimeField(auto_now=True)),
                ('title', models.CharField(max_length=255)),
                ('description', models.TextField()),
                ('banner', models.ImageField(upload_to='event_banners/')),
                ('event_date', models.DateField()),
                ('event_time', models.TimeField(blank=True, null=True)),
                ('display_start', models.DateField()),
                ('display_end', models.DateField()),
                ('location', models.CharField(blank=True, max_length=255)),
                ('link_more_info', models.URLField(blank=True, null=True)),
                ('slug', models.SlugField(blank=True, max_length=200, unique=True)),
            ],
            options={
                'abstract': False,
            },
        ),
    ]
