from collections import defaultdict

from django.db import migrations, models


def backfill_task_sort_order(apps, schema_editor):
    MediaTask = apps.get_model('website', 'MediaTask')
    by_content = defaultdict(list)
    for task in MediaTask.objects.order_by('content_id', 'pk').values('pk', 'content_id'):
        by_content[task['content_id']].append(task['pk'])
    for task_ids in by_content.values():
        for order, task_id in enumerate(task_ids):
            MediaTask.objects.filter(pk=task_id).update(sort_order=order)


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0104_media_event_deadlines'),
    ]

    operations = [
        migrations.AddField(
            model_name='mediatask',
            name='sort_order',
            field=models.PositiveSmallIntegerField(default=0, verbose_name='Ordem'),
        ),
        migrations.RunPython(backfill_task_sort_order, migrations.RunPython.noop),
    ]
