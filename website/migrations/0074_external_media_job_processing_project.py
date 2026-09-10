from django.db import migrations, models
import django.db.models.deletion


def link_current_render_jobs(apps, schema_editor):
    Project = apps.get_model('website', 'ExternalMediaProject')
    Job = apps.get_model('website', 'ExternalMediaJob')
    for project in Project.objects.exclude(render_job_id=None).iterator():
        Job.objects.filter(
            pk=project.render_job_id,
            processing_project_id__isnull=True,
        ).update(processing_project_id=project.pk)


class Migration(migrations.Migration):
    dependencies = [('website', '0073_external_media_project_export')]

    operations = [
        migrations.AddField(
            model_name='externalmediajob',
            name='processing_project',
            field=models.ForeignKey(
                blank=True,
                help_text='Projeto que originou este processamento.',
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='processing_history',
                to='website.externalmediaproject',
            ),
        ),
        migrations.RunPython(link_current_render_jobs, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name='externalmediajob',
            index=models.Index(fields=['processing_project', '-created_at'], name='ext_job_project_created_idx'),
        ),
    ]
