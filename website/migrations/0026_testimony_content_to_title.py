# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0025_add_instagram_url_to_testimony'),
    ]

    operations = [
        migrations.AlterField(
            model_name='testimony',
            name='author_name',
            field=models.CharField(blank=True, default='', max_length=150, verbose_name='Nome do Autor'),
        ),
        migrations.AddField(
            model_name='testimony',
            name='title',
            field=models.CharField(default='', max_length=255, verbose_name='Título do Testemunho'),
            preserve_default=False,
        ),
        migrations.RemoveField(
            model_name='testimony',
            name='content',
        ),
    ]
