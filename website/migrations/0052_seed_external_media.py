from django.db import migrations


def seed_external_media(apps, schema_editor):
    Ministry = apps.get_model('website', 'Ministry')
    SubtitleStyle = apps.get_model('website', 'SubtitleStyle')
    RenderPreset = apps.get_model('website', 'RenderPreset')
    GlossaryTerm = apps.get_model('website', 'GlossaryTerm')

    ministry = Ministry.objects.filter(name__iexact='Mídia Externa').first()
    if ministry:
        ministry.code = 'midia_externa'
        ministry.is_active = True
        ministry.save(update_fields=['code', 'is_active'])
    else:
        Ministry.objects.create(
            name='Mídia Externa',
            code='midia_externa',
            description='Ministério responsável pela produção e distribuição de mídia externa.',
            color='#C90905',
            is_active=True,
        )

    SubtitleStyle.objects.get_or_create(
        name='Filadélfia Padrão',
        defaults={
            'font_name': 'Arial', 'font_size': 48,
            'primary_color': '#FFFFFF', 'outline_color': '#000000',
            'outline_width': 3, 'shadow': 1, 'margin_bottom': 60,
            'alignment': 2, 'max_lines': 2, 'max_characters': 42,
        },
    )

    presets = [
        ('Original', 'original', None, None),
        ('YouTube', 'youtube', 1920, 1080),
        ('Instagram Reels', 'instagram-reels', 1080, 1920),
        ('Instagram Stories', 'instagram-stories', 1080, 1920),
        ('Instagram Feed', 'instagram-feed', 1080, 1350),
        ('TikTok', 'tiktok', 1080, 1920),
        ('Telão', 'telao', 1920, 1080),
    ]
    for name, code, width, height in presets:
        RenderPreset.objects.get_or_create(
            code=code,
            defaults={'name': name, 'width': width, 'height': height},
        )

    glossary = [
        ('Espírito Santo', 'Holy Spirit'),
        ('Culto', 'Worship Service'),
        ('Ceia', 'Communion Service'),
        ('Evangelismo', 'Outreach'),
        ('Imersão Sobrenatural', 'Supernatural Immersion'),
        ('Igreja Filadélfia', 'Philadelphia Church'),
    ]
    for source, translated in glossary:
        GlossaryTerm.objects.get_or_create(
            source_language='pt', target_language='en', source_text=source,
            defaults={'translated_text': translated, 'is_active': True},
        )


def unseed_external_media(apps, schema_editor):
    Ministry = apps.get_model('website', 'Ministry')
    Ministry.objects.filter(code='midia_externa').update(code=None)


class Migration(migrations.Migration):
    dependencies = [('website', '0051_externalmediajob_glossaryterm_renderpreset_and_more')]
    operations = [migrations.RunPython(seed_external_media, unseed_external_media)]
