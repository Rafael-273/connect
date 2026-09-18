from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0106_mediatask_due_offset_days'),
    ]

    operations = [
        migrations.CreateModel(
            name='MediaPlanningTemplateItemStep',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('deleted', models.DateTimeField(db_index=True, editable=False, null=True)),
                ('deleted_by_cascade', models.BooleanField(default=False, editable=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('update_at', models.DateTimeField(auto_now=True)),
                ('title', models.CharField(max_length=200, verbose_name='Papel')),
                ('due_offset_days', models.IntegerField(blank=True, null=True, verbose_name='Prazo (dias em relação ao evento)')),
                ('description', models.TextField(blank=True, verbose_name='Observações')),
                ('sort_order', models.PositiveSmallIntegerField(default=0, verbose_name='Ordem')),
                ('template_item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='steps', to='website.mediaplanningtemplateitem', verbose_name='Demanda padrão')),
            ],
            options={
                'verbose_name': 'Etapa de demanda padrão',
                'verbose_name_plural': 'Etapas de demanda padrão',
                'db_table': 'website_media_planning_template_item_step',
                'ordering': ['sort_order', 'pk'],
            },
        ),
    ]
