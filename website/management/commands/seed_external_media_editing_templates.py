from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from website.models import (
    MediaTemplate,
    MediaTemplateBlock,
    MediaTemplatePlugin,
    MediaTemplateVersion,
    ProxyProfile,
    RenderPreset,
    SubtitleStyle,
)


FILLER_WORDS = ['ah', 'ahn', 'assim', 'eh', 'então', 'hmm', 'hum', 'hã', 'né', 'tipo', 'é']
COMMON_VERSION_SETTINGS = {
    'version': 1,
    'status': MediaTemplateVersion.Status.PUBLISHED,
    'original_language': 'pt',
    'output_languages': ['pt', 'en'],
    'interactive_preview_enabled': True,
    'preview_editable_capabilities': ['CUTS', 'SUBTITLES', 'TRANSFORMS'],
    'preview_confidence_thresholds': {'flag_below': 0.72, 'auto_accept_above': 0.92},
    'subtitles_enabled': True,
    'translated_subtitles_enabled': True,
    'default_settings': {
        'language_mode': 'translated',
        'spoken_languages': ['pt'],
        'translated_language': 'en',
    },
    'allowed_overrides': [],
}
TEMPLATES = (
    {
        'name': 'Anúncio Mensal',
        'slug': 'anuncio-mensal',
        'description': 'Monta os avisos na ordem definida e gera legenda em português.',
        'blocks': (
            ('introducao-testemunho', 'Introdução Testemunho', 1, True, True, 1, 0, True, False),
            ('testemunho', 'Testemunho', 2, False, True, 0, 0, False, True),
            ('introducao-anuncio', 'Introdução Anúncio', 3, True, True, 1, 0, True, False),
            ('abertura', 'Abertura', 4, True, True, 1, 0, False, False),
            ('cultos-regulares', 'Cultos Regulares', 5, True, True, 1, 0, False, False),
            ('culto-da-colheita', 'Culto da Colheita', 6, True, True, 1, 0, False, False),
            ('mentoria-das-mulheres', 'Mentoria das Mulheres', 7, True, True, 1, 0, False, False),
            ('encontro-dos-homens', 'Encontro dos Homens', 8, True, True, 1, 0, False, False),
            ('cantina', 'Cantina', 9, True, True, 1, 0, False, False),
            ('qr-code-spotify', 'QR Code / Spotify', 10, True, True, 1, 0, False, False),
            ('erros-de-gravacao', 'Erros de Gravação', 11, False, True, 1, 0, False, False),
            ('encerramento', 'Encerramento', 12, False, True, 1, 0, True, False),
        ),
        'plugins': (
            ('subtitle_pt', 1, {}),
            ('silence_removal', 1, {'profile': 'balanced', 'filler_words': FILLER_WORDS, 'word_safety_margin_ms': 100}),
            ('filler_removal', 2, {'profile': 'balanced', 'filler_words': FILLER_WORDS, 'word_safety_margin_ms': 100}),
            ('auto_tracking', 3, {'priority': 'face', 'smoothing': 0.18, 'top_margin': 0.02, 'safe_margin': 0.18, 'interval_frames': 10, 'horizontal_smoothing': 0.34}),
        ),
        'proxy_code': None,
    },
    {
        'name': 'Moderação',
        'slug': 'moderacao',
        'description': '',
        'blocks': (
            ('introducao', 'Introdução', 1, True, True, 1, 0, False, False),
            ('missao-eufabetizo', 'Missão Eufabetizo', 2, True, True, 1, 0, False, False),
            ('missao-gramachinhos', 'Missão Gramachinhos', 3, True, True, 1, 0, False, False),
            ('missao-haiti', 'Missão Haiti', 4, True, True, 1, 0, False, False),
            ('missao-angola', 'Missão Angola', 5, True, True, 1, 0, False, False),
            ('conclusao', 'Conclusão', 6, True, True, 1, 0, False, False),
            ('fechamento', 'Fechamento', 7, True, True, 1, 0, False, False),
        ),
        'plugins': (
            ('silence_removal', 1, {'profile': 'balanced', 'filler_words': FILLER_WORDS, 'word_safety_margin_ms': 100}),
            ('filler_removal', 2, {'profile': 'balanced', 'filler_words': FILLER_WORDS, 'word_safety_margin_ms': 100}),
            ('auto_tracking', 3, {'priority': 'face', 'smoothing': 0.18, 'top_margin': 0.02, 'safe_margin': 0.18, 'static_zoom': 1.0, 'interval_frames': 10, 'horizontal_smoothing': 0.34}),
        ),
        'proxy_code': 'community-1',
    },
)


class Command(BaseCommand):
    help = 'Cria ou atualiza os templates de edição Anúncio Mensal e Moderação.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Valida os pré-requisitos sem gravar alterações.')

    def handle(self, *args, **options):
        if options['dry_run']:
            self._validate_prerequisites()
            self.stdout.write(self.style.WARNING(
                'Dry-run concluído: os templates Anúncio Mensal e Moderação seriam criados ou sincronizados.'
            ))
            return

        with transaction.atomic():
            source_style, translated_style = self._validate_prerequisites()
            preset, _ = RenderPreset.objects.update_or_create(
                code='telao',
                defaults={
                    'name': 'Telão', 'width': 3840, 'height': 1200,
                    'video_codec': 'libx264', 'audio_codec': 'copy', 'video_crf': 23,
                    'extra_ffmpeg_args': [], 'is_active': True,
                },
            )
            proxy, _ = ProxyProfile.objects.update_or_create(
                code='community-1',
                defaults={
                    'name': 'Community 1', 'max_width': 960, 'fps': 30,
                    'video_crf': 27, 'audio_bitrate_kbps': 96, 'is_default': True, 'is_active': True,
                },
            )
            for definition in TEMPLATES:
                self._upsert_template(definition, preset, proxy, source_style, translated_style)

        self.stdout.write(self.style.SUCCESS('Templates Anúncio Mensal e Moderação sincronizados.'))

    def _validate_prerequisites(self):
        source_style = SubtitleStyle.objects.filter(name='Filadélfia Telão').first()
        translated_style = SubtitleStyle.objects.filter(name='Filadélfia Telão Traduzida').first()
        if not source_style or not translated_style:
            raise CommandError(
                'Os estilos de legenda não existem. Execute primeiro '
                '`python manage.py seed_external_media_subtitle_styles`.'
            )
        return source_style, translated_style

    def _upsert_template(self, definition, preset, proxy, source_style, translated_style):
        template, created = MediaTemplate.objects.update_or_create(
            slug=definition['slug'],
            defaults={
                'name': definition['name'],
                'category': MediaTemplate.Category.OTHER,
                'description': definition['description'],
                'is_active': True,
            },
        )
        version_defaults = {
            **COMMON_VERSION_SETTINGS,
            'preset': preset,
            'subtitle_style': source_style,
            'translated_subtitle_style': translated_style,
            'preview_proxy_profile': proxy if definition['proxy_code'] else None,
        }
        version, version_created = MediaTemplateVersion.objects.update_or_create(
            template=template,
            version=1,
            defaults=version_defaults,
        )
        if version.status == MediaTemplateVersion.Status.PUBLISHED and not version.published_at:
            version.published_at = timezone.now()
            version.save(update_fields=['published_at', 'update_at'])

        for block in definition['blocks']:
            key, name, order, required, multiple, minimum, maximum, skip_processing, remove_background_voice = block
            MediaTemplateBlock.objects.update_or_create(
                version=version,
                key=key,
                defaults={
                    'name': name, 'description': '', 'order': order, 'is_required': required,
                    'allows_multiple': multiple, 'min_occurrences': minimum, 'max_occurrences': maximum,
                    'skip_extra_processing': skip_processing,
                    'remove_background_voice': remove_background_voice,
                    'overlay_definitions': [],
                },
            )
        for code, order, configuration in definition['plugins']:
            MediaTemplatePlugin.objects.update_or_create(
                version=version,
                code=code,
                defaults={
                    'order': order, 'is_enabled': True, 'user_can_override': False,
                    'configuration': configuration,
                },
            )
        action = 'Criado' if created else 'Atualizado'
        version_action = 'criada' if version_created else 'atualizada'
        self.stdout.write(f'{action}: {template.name}; versão 1 {version_action}.')
