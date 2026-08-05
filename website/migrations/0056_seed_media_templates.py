from django.db import migrations
from django.utils import timezone


def seed_media_templates(apps, schema_editor):
    MediaTemplate = apps.get_model('website', 'MediaTemplate')
    MediaTemplateVersion = apps.get_model('website', 'MediaTemplateVersion')
    MediaTemplateBlock = apps.get_model('website', 'MediaTemplateBlock')
    MediaTemplatePlugin = apps.get_model('website', 'MediaTemplatePlugin')
    RenderPreset = apps.get_model('website', 'RenderPreset')
    SubtitleStyle = apps.get_model('website', 'SubtitleStyle')

    style = SubtitleStyle.objects.filter(is_active=True).first()
    presets = {item.code: item for item in RenderPreset.objects.all()}
    definitions = [
        {
            'name': 'Tradução de Vídeo', 'slug': 'traducao-video', 'category': 'TRANSLATION',
            'description': 'Transcrição, legendas em português e tradução natural para inglês.',
            'preset': 'original',
            'blocks': [('video', 'Vídeo', True, False, 1, 1)],
            'plugins': ['subtitle_pt', 'translation_en'],
            'languages': ['pt', 'en'],
        },
        {
            'name': 'Stories da Pregação', 'slug': 'stories-pregacao', 'category': 'STORIES',
            'description': 'Vídeo vertical com legenda em português.',
            'preset': 'instagram-stories',
            'blocks': [('video', 'Pregação', True, False, 1, 1)],
            'plugins': ['subtitle_pt'],
            'languages': ['pt'],
        },
        {
            'name': 'Reels', 'slug': 'reels', 'category': 'REELS',
            'description': 'Reel vertical com legenda em português.',
            'preset': 'instagram-reels',
            'blocks': [('video', 'Vídeo principal', True, False, 1, 1)],
            'plugins': ['subtitle_pt'],
            'languages': ['pt'],
        },
        {
            'name': 'Anúncio Mensal', 'slug': 'anuncio-mensal', 'category': 'ANNOUNCEMENT',
            'description': 'Monta os avisos na ordem definida e gera legenda em português.',
            'preset': 'youtube',
            'blocks': [
                ('introducao', 'Introdução', True, False, 1, 1),
                ('testemunho', 'Testemunho', False, False, 0, 1),
                ('aviso', 'Avisos', True, True, 1, 8),
                ('encerramento', 'Encerramento', True, False, 1, 1),
            ],
            'plugins': ['subtitle_pt'],
            'languages': ['pt'],
        },
    ]
    for definition in definitions:
        preset = presets.get(definition['preset']) or next(iter(presets.values()), None)
        if not preset or not style:
            continue
        template, _ = MediaTemplate.objects.get_or_create(
            slug=definition['slug'],
            defaults={
                'name': definition['name'], 'category': definition['category'],
                'description': definition['description'], 'is_active': True,
            },
        )
        version, created = MediaTemplateVersion.objects.get_or_create(
            template=template, version=1,
            defaults={
                'status': 'PUBLISHED', 'preset': preset, 'subtitle_style': style,
                'original_language': 'pt', 'output_languages': definition['languages'],
                'published_at': timezone.now(),
            },
        )
        if not created:
            continue
        for order, (key, name, required, multiple, minimum, maximum) in enumerate(definition['blocks'], 1):
            MediaTemplateBlock.objects.create(
                version=version, key=key, name=name, order=order, is_required=required,
                allows_multiple=multiple, min_occurrences=minimum, max_occurrences=maximum,
            )
        for order, code in enumerate(definition['plugins'], 1):
            MediaTemplatePlugin.objects.create(version=version, code=code, order=order)


def unseed_media_templates(apps, schema_editor):
    MediaTemplate = apps.get_model('website', 'MediaTemplate')
    MediaTemplate.objects.filter(
        slug__in=['traducao-video', 'stories-pregacao', 'reels', 'anuncio-mensal'],
    ).delete()


class Migration(migrations.Migration):
    dependencies = [('website', '0055_externalmediaproject_mediatemplate_and_more')]
    operations = [migrations.RunPython(seed_media_templates, unseed_media_templates)]
