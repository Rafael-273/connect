from django.db import migrations


def create_community_profile(apps, schema_editor):
    ProxyProfile = apps.get_model('website', 'ProxyProfile')
    ProxyProfile.objects.get_or_create(
        code='community-1',
        defaults={
            'name': 'Community 1',
            'max_width': 960,
            'fps': 30,
            'video_crf': 27,
            'audio_bitrate_kbps': 96,
            'is_default': True,
            'is_active': True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [('website', '0088_previewsession_proxyprofile_and_more')]
    operations = [migrations.RunPython(create_community_profile, migrations.RunPython.noop)]
