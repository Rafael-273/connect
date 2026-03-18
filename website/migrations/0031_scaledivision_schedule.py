from django.db import migrations, models
import django.db.models.deletion


def clear_division_data(apps, schema_editor):
    """Remove all existing division data since the FK target is changing."""
    DivisionMember = apps.get_model('website', 'DivisionMember')
    ScaleDivision = apps.get_model('website', 'ScaleDivision')
    # Use raw SQL to bypass safedelete triggers
    schema_editor.execute('DELETE FROM website_divisionmember')
    schema_editor.execute('DELETE FROM website_scaledivision')


class Migration(migrations.Migration):

    atomic = False  # Needed to avoid PostgreSQL "pending trigger events" error

    dependencies = [
        ('website', '0030_scaledivision_divisionmember'),
    ]

    operations = [
        # 1. Remove old unique_together constraint first (before touching data)
        migrations.AlterUniqueTogether(
            name='scaledivision',
            unique_together=set(),
        ),

        # 2. Clear existing data via raw SQL (bypass safedelete triggers)
        migrations.RunPython(clear_division_data, migrations.RunPython.noop),

        # 3. Remove the old ministry FK
        migrations.RemoveField(
            model_name='scaledivision',
            name='ministry',
        ),

        # 4. Add the new schedule FK
        migrations.AddField(
            model_name='scaledivision',
            name='schedule',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='divisions',
                to='website.monthlyschedule',
                verbose_name='Escala',
            ),
        ),

        # 5. Re-add unique_together with new fields
        migrations.AlterUniqueTogether(
            name='scaledivision',
            unique_together={('schedule', 'name', 'parent')},
        ),

        # 6. Update ordering in Meta
        migrations.AlterModelOptions(
            name='scaledivision',
            options={
                'ordering': ['schedule', 'order', 'name'],
                'verbose_name': 'Divisão de Escala',
                'verbose_name_plural': 'Divisões de Escala',
            },
        ),
    ]
