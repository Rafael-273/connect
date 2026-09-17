from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('website', '0081_projectblockmedia_preview_proxy')]

    operations = [
        migrations.CreateModel(
            name='ProjectCustomBlock',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('deleted', models.DateTimeField(db_index=True, editable=False, null=True)),
                ('deleted_by_cascade', models.BooleanField(default=False, editable=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('update_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=120)),
                ('description', models.TextField(blank=True)),
                ('position', models.PositiveSmallIntegerField(default=1)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='custom_blocks', to='website.externalmediaproject')),
            ],
            options={'ordering': ['position', 'pk']},
        ),
        migrations.AddConstraint(model_name='projectcustomblock', constraint=models.UniqueConstraint(fields=('project', 'position'), name='unique_project_custom_block_position')),
        migrations.AlterField(model_name='projectblockmedia', name='block', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='project_media', to='website.mediatemplateblock')),
        migrations.AddField(model_name='projectblockmedia', name='custom_block', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='media', to='website.projectcustomblock')),
    ]
