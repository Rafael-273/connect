from django.core.management.base import BaseCommand, CommandError

from website.models import MediaTemplate, MediaTemplateVersion, SubtitleStyle


SUBTITLE_STYLES = (
    {
        'name': 'Filadélfia Telão',
        'font_name': 'Montserrat',
        'font_weight': 700,
        'font_size': 57,
        'primary_color': '#ffa800',
        'primary_opacity': 100,
        'background_enabled': True,
        'background_color': '#000000',
        'background_opacity': 35,
        'background_padding_x': 0,
        'background_padding_y': 0,
        'background_height_percent': 62,
        'background_radius': 3,
        'outline_color': '#000000',
        'outline_width': 2,
        'shadow': 8,
        'shadow_angle': 30,
        'shadow_size': 2,
        'shadow_blur': 3,
        'shadow_opacity': 100,
        'margin_bottom': 121,
        'alignment': SubtitleStyle.Alignment.BOTTOM_CENTER,
        'max_lines': 1,
        'max_characters': 40,
        'is_active': True,
    },
    {
        'name': 'Filadélfia Telão Traduzida',
        'font_name': 'Montserrat',
        'font_weight': 600,
        'font_size': 52,
        'primary_color': '#ffffff',
        'primary_opacity': 100,
        'background_enabled': True,
        'background_color': '#000000',
        'background_opacity': 35,
        'background_padding_x': 0,
        'background_padding_y': 0,
        'background_height_percent': 62,
        'background_radius': 3,
        'outline_color': '#000000',
        'outline_width': 2,
        'shadow': 8,
        'shadow_angle': 30,
        'shadow_size': 2,
        'shadow_blur': 3,
        'shadow_opacity': 100,
        'margin_bottom': 30,
        'alignment': SubtitleStyle.Alignment.BOTTOM_CENTER,
        'max_lines': 1,
        'max_characters': 40,
        'is_active': True,
    },
)

DEFAULT_TEMPLATE_SLUGS = ('anuncio-mensal', 'moderacao')


class Command(BaseCommand):
    help = 'Cria os estilos de legenda do ambiente local e os vincula aos templates de edição.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--template-slug',
            action='append',
            dest='template_slugs',
            help='Slug de um template a configurar. Pode ser informado mais de uma vez.',
        )
        parser.add_argument('--dry-run', action='store_true', help='Exibe as alterações sem gravá-las.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        template_slugs = options['template_slugs'] or DEFAULT_TEMPLATE_SLUGS
        styles = {}

        for values in SUBTITLE_STYLES:
            values = dict(values)
            name = values.pop('name')
            existing = SubtitleStyle.objects.filter(name=name).first()
            changed = not existing or any(getattr(existing, field) != value for field, value in values.items())
            if dry_run:
                action = 'criaria' if not existing else ('atualizaria' if changed else 'manteria')
                self.stdout.write(f'{action.capitalize()} o estilo "{name}".')
                styles[name] = existing
                continue
            style, created = SubtitleStyle.objects.update_or_create(name=name, defaults=values)
            styles[name] = style
            action = 'Criado' if created else ('Atualizado' if changed else 'Mantido')
            self.stdout.write(f'{action}: estilo "{name}".')

        if dry_run:
            for slug in template_slugs:
                try:
                    template = MediaTemplate.objects.get(slug=slug)
                except MediaTemplate.DoesNotExist as error:
                    raise CommandError(f'Template com slug "{slug}" não foi encontrado.') from error
                version = template.published_version or template.current_version
                if not version:
                    raise CommandError(f'O template "{template.name}" não possui versão para configurar.')
                configured = (
                    version.subtitle_style and version.subtitle_style.name == 'Filadélfia Telão'
                    and version.translated_subtitle_style
                    and version.translated_subtitle_style.name == 'Filadélfia Telão Traduzida'
                    and version.original_language == 'pt'
                    and version.output_languages == ['pt', 'en']
                    and version.subtitles_enabled
                    and version.translated_subtitles_enabled
                )
                action = 'manteria' if configured else 'configuraria'
                self.stdout.write(
                    f'{action.capitalize()} a versão {version.version} de "{template.name}" para legendas PT e EN.'
                )
            self.stdout.write(self.style.WARNING('Dry-run concluído: nenhuma alteração foi gravada.'))
            return

        source_style = styles['Filadélfia Telão']
        translated_style = styles['Filadélfia Telão Traduzida']
        for slug in template_slugs:
            try:
                template = MediaTemplate.objects.get(slug=slug)
            except MediaTemplate.DoesNotExist as error:
                raise CommandError(f'Template com slug "{slug}" não foi encontrado.') from error

            version = template.published_version or template.current_version
            if not version:
                raise CommandError(f'O template "{template.name}" não possui versão para configurar.')

            desired = {
                'subtitle_style': source_style,
                'translated_subtitle_style': translated_style,
                'original_language': 'pt',
                'output_languages': ['pt', 'en'],
                'subtitles_enabled': True,
                'translated_subtitles_enabled': True,
            }
            changed_fields = [field for field, value in desired.items() if getattr(version, field) != value]
            if changed_fields:
                for field, value in desired.items():
                    setattr(version, field, value)
                version.save(update_fields=changed_fields + ['update_at'])
                self.stdout.write(self.style.SUCCESS(
                    f'Versão {version.version} de "{template.name}" configurada para legendas PT e EN.'
                ))
            else:
                self.stdout.write(f'Versão {version.version} de "{template.name}" já estava configurada.')
