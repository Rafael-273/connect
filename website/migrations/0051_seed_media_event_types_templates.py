"""Seed default event types and sample media templates."""

from django.db import migrations


DEFAULT_TYPES = [
    ('Conferência', 1),
    ('Batismo', 2),
    ('Culto Especial', 3),
    ('Congresso', 4),
    ('Encontro', 5),
    ('Retiro', 6),
    ('Santa Ceia', 7),
    ('Evento Infantil', 8),
    ('Evento de Jovens', 9),
]

CONFERENCIA_ITEMS = [
    ('Identidade visual', 'artwork', -30, -21, 'instagram'),
    ('Arte principal', 'artwork', -21, -14, 'instagram'),
    ('Banner do site', 'website_banner', -21, -14, 'website'),
    ('Reel de anúncio', 'reel', -21, -14, 'instagram', True, True),
    ('Reel convite', 'reel', -14, -7, 'instagram', True, True),
    ('Stories de divulgação', 'story', -14, -7, 'instagram_stories'),
    ('Contagem regressiva', 'story', -7, -1, 'instagram_stories'),
    ('Cobertura fotográfica', 'photography', 0, 3, 'instagram', True, False),
    ('Cobertura em stories', 'story', 0, 1, 'instagram_stories', True, False),
    ('Vídeo pós-evento', 'video', 3, 7, 'youtube', True, True),
]

BATISMO_ITEMS = [
    ('Arte de divulgação', 'artwork', -14, -7, 'instagram'),
    ('Stories', 'story', -7, 0, 'instagram_stories'),
    ('Fotografia', 'photography', 0, 3, 'instagram', True, False),
    ('Captação de vídeo', 'video', 0, 0, '', True, False),
    ('Reel pós-batismo', 'reel', 3, 7, 'instagram', True, True),
]


def seed(apps, schema_editor):
    MediaEventType = apps.get_model('website', 'MediaEventType')
    MediaPlanningTemplate = apps.get_model('website', 'MediaPlanningTemplate')
    MediaPlanningTemplateItem = apps.get_model('website', 'MediaPlanningTemplateItem')

    type_map = {}
    for name, order in DEFAULT_TYPES:
        et, _ = MediaEventType.objects.get_or_create(
            name=name,
            defaults={'sort_order': order, 'is_active': True},
        )
        type_map[name] = et

    def create_template(type_name, template_name, items):
        et = type_map.get(type_name)
        if not et:
            return
        tpl, _ = MediaPlanningTemplate.objects.get_or_create(
            event_type=et,
            defaults={
                'name': template_name,
                'description': f'Template padrão para eventos do tipo {type_name}.',
                'is_active': True,
            },
        )
        if tpl.items.exists():
            return
        for idx, row in enumerate(items):
            title, ctype = row[0], row[1]
            pub_offset = row[2] if len(row) > 2 else None
            due_offset = row[3] if len(row) > 3 else None
            channel = row[4] if len(row) > 4 else ''
            recording = row[5] if len(row) > 5 else False
            editing = row[6] if len(row) > 6 else False
            MediaPlanningTemplateItem.objects.create(
                template=tpl,
                title=title,
                content_type=ctype,
                publication_offset_days=pub_offset,
                due_offset_days=due_offset,
                publication_channel=channel,
                requires_recording=recording,
                requires_editing=editing,
                sort_order=idx,
            )

    create_template('Conferência', 'Template de Mídia — Conferência', CONFERENCIA_ITEMS)
    create_template('Batismo', 'Template de Mídia — Batismo', BATISMO_ITEMS)


def reverse_seed(apps, schema_editor):
    MediaEventType = apps.get_model('website', 'MediaEventType')
    names = [t[0] for t in DEFAULT_TYPES]
    MediaEventType.objects.filter(name__in=names).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('website', '0050_media_expansion'),
    ]

    operations = [
        migrations.RunPython(seed, reverse_seed),
    ]
