from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('website', '0090_projectblockmedia_multicam_fields')]

    operations = [
        migrations.AddField(
            model_name='projectblockmedia',
            name='camera_key',
            field=models.CharField(db_index=True, default='primary', max_length=80),
        ),
    ]
