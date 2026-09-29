from django.db import migrations


def make_instagram_mastering_more_transparent(apps, schema_editor):
    """Relax only the untouched seeded Instagram profile.

    -14 LUFS with a 9 LU range is needlessly dense for speech-led Reels once
    ducking and dialogue processing have already run. Keep administrators'
    custom tuning intact, but make the stock preset preserve vocal body.
    """
    MasteringProfile = apps.get_model('website', 'MasteringProfile')
    MasteringProfile.objects.filter(
        code='instagram_stories',
        target_lufs=-14.0,
        true_peak_db=-1.0,
        dynamic_range_target=9.0,
        max_gain_db=12.0,
        max_limiter_reduction_db=4.0,
    ).update(
        target_lufs=-16.0,
        true_peak_db=-1.5,
        dynamic_range_target=11.0,
        max_gain_db=8.0,
        max_limiter_reduction_db=2.0,
    )


class Migration(migrations.Migration):
    dependencies = [('website', '0110_alter_mediatask_options')]

    operations = [
        migrations.RunPython(make_instagram_mastering_more_transparent, migrations.RunPython.noop),
    ]
