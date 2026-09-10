import django.core.serializers.json
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('website', '0095_projectblockmedia_trim_ranges')]

    operations = [
        migrations.AlterField(
            model_name='externalmediaproject', name='configuration',
            field=models.JSONField(blank=True, default=dict, encoder=django.core.serializers.json.DjangoJSONEncoder),
        ),
    ]
