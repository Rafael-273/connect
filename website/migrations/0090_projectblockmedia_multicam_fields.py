from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('website', '0089_seed_community_1_proxy_profile')]

    operations = [
        migrations.AddField(
            model_name='projectblockmedia', name='camera_hint',
            field=models.CharField(
                choices=[('AUTO', 'Automático'), ('SPEAKER_A', 'Participante 1'), ('SPEAKER_B', 'Participante 2'), ('WIDE', 'Plano geral'), ('OTHER', 'Outro')],
                default='AUTO', max_length=16,
            ),
        ),
        migrations.AddField(
            model_name='projectblockmedia', name='camera_label',
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name='projectblockmedia', name='camera_order',
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='projectblockmedia', name='camera_role',
            field=models.CharField(choices=[('PRIMARY', 'Câmera principal'), ('SECONDARY', 'Câmera extra')], default='PRIMARY', max_length=16),
        ),
        migrations.AddField(
            model_name='projectblockmedia', name='media_role',
            field=models.CharField(choices=[('CAMERA', 'Câmera')], default='CAMERA', max_length=16),
        ),
        migrations.RemoveConstraint(model_name='projectblockmedia', name='unique_project_block_media_position'),
        migrations.AddConstraint(
            model_name='projectblockmedia',
            constraint=models.UniqueConstraint(
                fields=('project', 'block', 'position', 'camera_order'),
                name='unique_project_block_media_camera_position',
            ),
        ),
    ]
