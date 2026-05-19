from django.core.management.base import BaseCommand
from website.models.neighborhood import Neighborhood


class Command(BaseCommand):
    help = 'Update neighborhoods with Maricá neighborhoods list'

    NEIGHBORHOODS = [
        'Centro',
        'Flamengo',
        'Mumbuca',
        'Itapeba',
        'Parque Nanci',
        'Ponta Grossa',
        'São José do Imbassaí',
        'Araçatiba',
        'Jacaroá',
        'Barra de Maricá',
        'Zacarias',
        'Restinga de Maricá',
        'Retiro',
        'Camburi',
        'Pindobas',
        'Caxito',
        'Ubatiba',
        'Pilar',
        'Lagarto',
        'Silvado',
        'Condado de Maricá',
        'Marquês de Maricá',
        'Ponta Negra',
        'Jaconé',
        'Cordeirinho',
        'Guaratiba',
        'Jardim Interlagos',
        'Jardim Balneário Bambuí',
        'Pindobal',
        'Cajú',
        'Manoel Ribeiro',
        'Espraiado',
        'Vale da Figueira',
        'Bananal',
        'Inoã',
        'Chácaras de Inoã',
        'Calaboca',
        'SPAR',
        'Santa Paula',
        'Cassorotiba',
        'Recanto de Itaipuaçu',
        'Praia de Itaipuaçu',
        'Morada das Águias',
        'Rincão Mimoso',
        'Barroco',
        'Jardim Atlântico Oeste',
        'Jardim Atlântico Central',
        'Jardim Atlântico Leste',
        'Cajueiros',
        'Itaocaia Valley',
    ]

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear all existing neighborhoods before adding new ones',
        )

    def handle(self, *args, **options):
        clear = options.get('clear', False)

        if clear:
            count, _ = Neighborhood.objects.filter(parent__isnull=True).delete()
            self.stdout.write(
                self.style.WARNING(f'Deleted {count} existing neighborhoods')
            )

        created_count = 0
        updated_count = 0

        for name in self.NEIGHBORHOODS:
            neighborhood, created = Neighborhood.objects.get_or_create(
                name=name,
                defaults={'parent': None}
            )

            if created:
                created_count += 1
                self.stdout.write(
                    self.style.SUCCESS(f'✓ Created: {name}')
                )
            else:
                updated_count += 1
                self.stdout.write(
                    self.style.WARNING(f'→ Already exists: {name}')
                )

        self.stdout.write(
            self.style.SUCCESS(
                f'\n✅ Done! Created: {created_count}, Already existed: {updated_count}'
            )
        )
