from django.db import migrations, models
import django.db.models.deletion
import simple_history.models


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0023_music'),
    ]

    operations = [
        migrations.CreateModel(
            name='ScaleDivision',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('deleted', models.DateTimeField(db_index=True, editable=False, null=True)),
                ('deleted_by_cascade', models.BooleanField(default=False, editable=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('update_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=100, verbose_name='Nome da Divisão')),
                ('order', models.IntegerField(default=0, help_text='Ordem de exibição (menor = primeiro)', verbose_name='Ordem')),
                ('is_active', models.BooleanField(default=True, verbose_name='Ativa')),
                ('ministry', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='divisions', to='website.ministry', verbose_name='Ministério')),
                ('parent', models.ForeignKey(blank=True, help_text='Deixe vazio para divisão de nível raiz', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='children', to='website.scaledivision', verbose_name='Divisão Pai')),
            ],
            options={
                'verbose_name': 'Divisão de Escala',
                'verbose_name_plural': 'Divisões de Escala',
                'ordering': ['ministry', 'order', 'name'],
                'unique_together': {('ministry', 'name', 'parent')},
            },
        ),
        migrations.CreateModel(
            name='DivisionMember',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('deleted', models.DateTimeField(db_index=True, editable=False, null=True)),
                ('deleted_by_cascade', models.BooleanField(default=False, editable=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('update_at', models.DateTimeField(auto_now=True)),
                ('division', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='division_members', to='website.scaledivision', verbose_name='Divisão')),
                ('member', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='division_assignments', to='website.member', verbose_name='Membro')),
                ('schedule_day', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='division_assignments', to='website.scheduleday', verbose_name='Dia de Escala')),
            ],
            options={
                'verbose_name': 'Membro da Divisão',
                'verbose_name_plural': 'Membros das Divisões',
                'ordering': ['division__order', 'member__name'],
                'unique_together': {('division', 'schedule_day', 'member')},
            },
        ),
        migrations.CreateModel(
            name='HistoricalScaleDivision',
            fields=[
                ('id', models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name='ID')),
                ('deleted', models.DateTimeField(db_index=True, editable=False, null=True)),
                ('deleted_by_cascade', models.BooleanField(default=False, editable=False)),
                ('created_at', models.DateTimeField(blank=True, editable=False)),
                ('update_at', models.DateTimeField(blank=True, editable=False)),
                ('name', models.CharField(max_length=100, verbose_name='Nome da Divisão')),
                ('order', models.IntegerField(default=0, help_text='Ordem de exibição (menor = primeiro)', verbose_name='Ordem')),
                ('is_active', models.BooleanField(default=True, verbose_name='Ativa')),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField(db_index=True)),
                ('history_change_reason', models.CharField(max_length=100, null=True)),
                ('history_type', models.CharField(choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
                ('history_user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='website.user')),
                ('ministry', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='website.ministry')),
                ('parent', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='website.scaledivision')),
            ],
            options={
                'verbose_name': 'historical Divisão de Escala',
                'verbose_name_plural': 'historical Divisões de Escala',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': ('history_date', 'history_id'),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.CreateModel(
            name='HistoricalDivisionMember',
            fields=[
                ('id', models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name='ID')),
                ('deleted', models.DateTimeField(db_index=True, editable=False, null=True)),
                ('deleted_by_cascade', models.BooleanField(default=False, editable=False)),
                ('created_at', models.DateTimeField(blank=True, editable=False)),
                ('update_at', models.DateTimeField(blank=True, editable=False)),
                ('history_id', models.AutoField(primary_key=True, serialize=False)),
                ('history_date', models.DateTimeField(db_index=True)),
                ('history_change_reason', models.CharField(max_length=100, null=True)),
                ('history_type', models.CharField(choices=[('+', 'Created'), ('~', 'Changed'), ('-', 'Deleted')], max_length=1)),
                ('division', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='website.scaledivision')),
                ('history_user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to='website.user')),
                ('member', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='website.member')),
                ('schedule_day', models.ForeignKey(blank=True, db_constraint=False, null=True, on_delete=django.db.models.deletion.DO_NOTHING, related_name='+', to='website.scheduleday')),
            ],
            options={
                'verbose_name': 'historical Membro da Divisão',
                'verbose_name_plural': 'historical Membros das Divisões',
                'ordering': ('-history_date', '-history_id'),
                'get_latest_by': ('history_date', 'history_id'),
            },
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
    ]
