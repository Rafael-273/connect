from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def bind_legacy_teams(apps, schema_editor):
    Team = apps.get_model('website', 'MediaSubTeam')
    Ministry = apps.get_model('website', 'Ministry')
    alias = schema_editor.connection.alias
    teams = Team.objects.using(alias).filter(ministry__isnull=True)
    if not teams.exists():
        return
    by_code = Ministry.objects.using(alias).filter(code='midia_externa').first()
    by_name = Ministry.objects.using(alias).filter(name__iexact='Mídia Externa').first()
    if by_code and by_name and by_code.pk != by_name.pk:
        raise RuntimeError('Há dois ministérios identificados como Mídia Externa. Corrija o código/nome antes de migrar as equipes.')
    ministry = by_code or by_name
    if ministry is None:
        raise RuntimeError('As equipes legadas não têm ministério identificável. Defina o código midia_externa no ministério de origem e execute a migração novamente.')
    teams.update(ministry_id=ministry.pk)


class Migration(migrations.Migration):
    dependencies = [
        ('website', '0102_alter_mediacontent_content_type_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='mediasubteam', name='ministry',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE,
                                    related_name='sub_teams', to='website.ministry', verbose_name='Ministério'),
        ),
        migrations.RunPython(bind_legacy_teams, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='mediasubteam', name='ministry',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                    related_name='sub_teams', to='website.ministry', verbose_name='Ministério'),
        ),
        migrations.AlterField(
            model_name='mediasubteam', name='name',
            field=models.CharField(max_length=100, verbose_name='Nome'),
        ),
        migrations.AlterUniqueTogether(name='mediasubteam', unique_together={('ministry', 'name')}),
        migrations.AlterModelOptions(
            name='mediasubteam', options={'ordering': ['name'], 'verbose_name': 'Subequipe do Ministério',
                                         'verbose_name_plural': 'Subequipes dos Ministérios'},
        ),
        migrations.CreateModel(
            name='MinistryManual',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('deleted', models.DateTimeField(db_index=True, editable=False, null=True)),
                ('deleted_by_cascade', models.BooleanField(default=False, editable=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('update_at', models.DateTimeField(auto_now=True)),
                ('title', models.CharField(max_length=200, verbose_name='Título')),
                ('summary', models.TextField(blank=True, verbose_name='Resumo')),
                ('content', models.TextField(verbose_name='Conteúdo')),
                ('is_active', models.BooleanField(default=True, verbose_name='Ativo')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('ministry', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='manuals', to='website.ministry')),
                ('sub_team', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='manuals', to='website.mediasubteam', verbose_name='Equipe')),
            ],
            options={'ordering': ['title'], 'verbose_name': 'Manual do Ministério', 'verbose_name_plural': 'Manuais dos Ministérios'},
        ),
    ]
