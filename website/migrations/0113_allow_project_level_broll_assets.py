from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0112_mediatemplateversion_intro_outro_enabled'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='projectbrollasset',
            name='broll_asset_exactly_one_block',
        ),
        migrations.AddConstraint(
            model_name='projectbrollasset',
            constraint=models.CheckConstraint(
                check=(
                    models.Q(block__isnull=False, custom_block__isnull=True)
                    | models.Q(block__isnull=True, custom_block__isnull=False)
                    | models.Q(block__isnull=True, custom_block__isnull=True)
                ),
                name='broll_asset_at_most_one_block',
            ),
        ),
    ]
