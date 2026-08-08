from django.db import migrations


def fix_brand_glossary(apps, schema_editor):
    GlossaryTerm = apps.get_model('website', 'GlossaryTerm')
    for source_text in ('Igreja Filadélfia', 'Filadélfia'):
        GlossaryTerm.objects.update_or_create(
            source_language='pt',
            target_language='en',
            source_text=source_text,
            defaults={
                'translated_text': source_text,
                'is_active': True,
                'notes': 'Marca/nome próprio. Não traduzir.',
            },
        )


class Migration(migrations.Migration):
    dependencies = [('website', '0063_projectblockmedia_trim_points')]
    operations = [migrations.RunPython(fix_brand_glossary, migrations.RunPython.noop)]
