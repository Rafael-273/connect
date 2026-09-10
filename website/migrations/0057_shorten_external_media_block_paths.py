from django.db import migrations, models

import website.models.external_media


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0056_seed_media_templates'),
    ]

    operations = [
        migrations.AlterField(
            model_name='projectblockmedia',
            name='file',
            field=models.FileField(
                max_length=255,
                storage=website.models.external_media.get_external_media_storage,
                upload_to=website.models.external_media.external_media_project_upload_path,
            ),
        ),
        migrations.AlterField(
            model_name='projectblockmedia',
            name='thumbnail',
            field=models.ImageField(
                blank=True,
                max_length=255,
                storage=website.models.external_media.get_external_media_storage,
                upload_to=website.models.external_media.external_media_project_upload_path,
            ),
        ),
    ]
