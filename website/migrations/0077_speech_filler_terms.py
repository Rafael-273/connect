from django.db import migrations, models


DEFAULT_FILLER_TERMS = (
    'eh', 'é', 'hum', 'hmm', 'ahn', 'ah', 'hã', 'tipo', 'né', 'então', 'assim',
)


def seed_default_filler_terms(apps, schema_editor):
    SpeechFillerTerm = apps.get_model('website', 'SpeechFillerTerm')
    MediaTemplateVersion = apps.get_model('website', 'MediaTemplateVersion')
    term_ids = []
    for text in DEFAULT_FILLER_TERMS:
        term, _ = SpeechFillerTerm.objects.get_or_create(
            language='pt', text=text,
            defaults={'is_active': True},
        )
        term_ids.append(term.pk)
    for version in MediaTemplateVersion.objects.all():
        version.filler_terms.add(*term_ids)


class Migration(migrations.Migration):

    dependencies = [('website', '0076_enable_dialogue_focused_audio_mixing')]

    operations = [
        migrations.CreateModel(
            name='SpeechFillerTerm',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('update_at', models.DateTimeField(auto_now=True)),
                ('deleted', models.DateTimeField(db_index=True, editable=False, null=True)),
                ('deleted_by_cascade', models.BooleanField(default=False, editable=False)),
                ('language', models.CharField(
                    choices=[
                        ('pt', 'Português'), ('en', 'Inglês'), ('es', 'Espanhol'),
                        ('fr', 'Francês'), ('it', 'Italiano'),
                    ],
                    default='pt', max_length=10,
                )),
                ('text', models.CharField(max_length=80, verbose_name='Termo')),
                ('is_active', models.BooleanField(default=True, verbose_name='Disponível para templates')),
            ],
            options={
                'verbose_name': 'Vocabulário de vício de fala',
                'verbose_name_plural': 'Vocabulários de vícios de fala',
                'ordering': ['language', 'text'],
            },
        ),
        migrations.AddField(
            model_name='mediatemplateversion',
            name='filler_terms',
            field=models.ManyToManyField(
                blank=True,
                related_name='template_versions',
                to='website.speechfillerterm',
                verbose_name='Vícios de fala ativos',
            ),
        ),
        migrations.RunPython(seed_default_filler_terms, migrations.RunPython.noop),
    ]
