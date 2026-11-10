from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('website', '0117_alter_mediacontent_status')]

    operations = [
        migrations.AddField(
            model_name='externalmediaproject', name='editorial_version',
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.CreateModel(
            name='PreviewOperation',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('operation_id', models.UUIDField()),
                ('request_hash', models.CharField(max_length=64)),
                ('response', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='website.member')),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='preview_operations', to='website.externalmediaproject')),
            ],
        ),
        migrations.AddConstraint(
            model_name='previewoperation',
            constraint=models.UniqueConstraint(fields=('project', 'operation_id'), name='unique_preview_operation'),
        ),
    ]
