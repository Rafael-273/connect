from django.core.management.base import BaseCommand

from website.models import GlossaryTerm


GLOSSARY_TERMS = (
    ('Ceia do Senhor', 'Communion'),
    ('Culto da Colheita', 'Harvest Service'),
    ('Culto de Celebração', 'Celebration Service'),
    ('Culto de Cura e Libertação', 'Healing and Deliverance Service'),
    ('Encontro dos Homens', 'Men’s Gathering'),
    ('Filadélfia', 'Filadélfia'),
    ('Forja', 'Forge'),
    ('Mentoria de Mulheres', 'Women’s Mentorship'),
    ('Ministério de Intercessão', 'Intercessory Ministry'),
    ('Pix', 'Pix'),
    ('Quarta Viva', 'Wednesday Night'),
    ('Reino de Deus', 'God’s Kingdom'),
)


class Command(BaseCommand):
    help = 'Cria ou atualiza o glossário PT→EN de mídia externa do ambiente local.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Exibe as alterações sem gravá-las.')

    def handle(self, *args, **options):
        created = updated = unchanged = 0
        dry_run = options['dry_run']

        for source_text, translated_text in GLOSSARY_TERMS:
            lookup = {
                'source_language': 'pt',
                'target_language': 'en',
                'source_text': source_text,
            }
            defaults = {'translated_text': translated_text, 'is_active': True}
            term = GlossaryTerm.objects.filter(**lookup).first()
            changed = not term or any(getattr(term, field) != value for field, value in defaults.items())
            if dry_run:
                created += not bool(term)
                updated += bool(term and changed)
                unchanged += bool(term and not changed)
                continue
            _, was_created = GlossaryTerm.objects.update_or_create(**lookup, defaults=defaults)
            if was_created:
                created += 1
            elif changed:
                updated += 1
            else:
                unchanged += 1

        label = 'Simulação concluída' if dry_run else 'Concluído'
        self.stdout.write(self.style.SUCCESS(
            f'{label}: {created} criado(s), {updated} atualizado(s), {unchanged} sem alteração.'
        ))
