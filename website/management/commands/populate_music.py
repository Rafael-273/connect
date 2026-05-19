"""
Script para popular o banco de dados com as músicas do repertório.

Uso:
    python manage.py populate_music
    python manage.py populate_music --dry-run     # simula sem gravar
    python manage.py populate_music --clear       # apaga tudo e recria
"""

from django.core.management.base import BaseCommand

from website.models.music import Music

MUSICAS_LENTAS = [
    "1 bilhão",
    "Agnus Dei",
    "Águas purificadoras",
    "Alfa e Omega",
    "Apenas um Toque",
    "Até que o Senhor venha",
    "Believe for it",
    "Boa Parte",
    "Canção de Maria",
    "Canção de Simeão",
    "Closer",
    "Coroa de Glória (Diego)",
    "Corpo e família",
    "Cria em mim",
    "De nada eu tenho falta",
    "Deus está aqui",
    "Deus tu és Santo",
    "Deixa a glória descer",
    "Digno",
    "Digno de louvor",
    "Digno de tudo",
    "É de coração",
    "É Ele",
    "E se eu cair",
    "Em espírito e em verdade",
    "Em tua presença",
    "Emanuel",
    "Enche-me",
    "Eu e minha casa",
    "Eu só quero sua presença",
    "Eu sou Teu Pai",
    "Eu também (100 Bilhões)",
    "Firmado na Rocha (Yahweh)",
    "Furioso oceano",
    "Getsêmani",
    "Grande é o Senhor",
    "Imensurável",
    "Isaías 53",
    "Jesus é o centro",
    "Jesus his name",
    "João 20 + Pra Sempre",
    "Lugar de Habitação",
    "Manancial",
    "Me atraiu",
    "Minha casa e eu",
    "Minh'alma engrandece ao Senhor",
    "Meu filho e Salvador",
    "Nada além do sangue",
    "Nada mais",
    "Ninguém explica Deus",
    "Nunca é tarde - acredito",
    "O cordeiro",
    "O dom supremo",
    "O encontro",
    "O Escudo",
    "O Rei",
    "Ó Noite Santa",
    "Oh quão lindo",
    "Oh vem Emanuel",
    "Pode morar aqui",
    "Poder da oração",
    "Poderoso Deus",
    "Plano Perfeito",
    "Promises",
    "Quebro meu vaso",
    "Quero conhecer Jesus / Tudo que eu mais quero é te ver",
    "Quero mais",
    "Santidade (Aline Barros)",
    "Se eu apenas te tocar",
    "Se Eu Não Te Ouvir",
    "Sinto Fluir",
    "Sublime",
    "Ta chorando por quê",
    "Te agradeço",
    "Te conhecer",
    "Tua é a glória",
    "Tu és + Águas purificadoras",
    "Tu és formoso",
    "Tudo é teu",
    "Último os dias",
    "Um milhão de anos",
    "Único",
    "Vendavais",
    "Vem para o Altar",
    "Vitorioso És",
    "Yahweh se manifestará",
]

MUSICAS_MEDIA = [
    "A batalha é do Senhor",
    "A benção",
    "A coroa",
    "A Igreja Vem",
    "A Resposta",
    "A vitória da Cruz",
    "Amigo de Deus",
    "Até chegar em Sião",
    "Bendito é o Rei",
    "Calvário (Kades)",
    "Calvário (Purples)",
    "Canção de Natal",
    "Clamo Jesus",
    "Conheci um grande amigo",
    "Danzando",
    "Deus de Israel",
    "Deus está no controle",
    "Debaixo do meu pé",
    "Desenvolvendo amor",
    "Dias de Paz",
    "Dom supremo",
    "Dupla honra",
    "Ele Vive",
    "Emaús",
    "Escape",
    "Estamos de pé",
    "Eu canto pra Ti",
    "Eu quero é Deus",
    "Eu sou Teu Pai",
    "Eu vou Clamar",
    "Eu vejo a Glória",
    "Fidelidade",
    "Galileu",
    "Hoje me alegrei",
    "Infinitamente mais",
    "Infinitamente mais (Fernandinho)",
    "Isaías 9",
    "Jardim da inocência",
    "Jeová Jireh",
    "Jesus Meu Guia é",
    "Jesus nasceu",
    "Maravilhosa graça",
    "Maria tu sabias",
    "Medley Aline Barros",
    "Medley João Viu / Além do Rio Azul / Dias de Elias",
    "Medley no meio dos louvores",
    "Mudou",
    "Noite Feliz",
    "O chão vai tremer",
    "O medo não vai me parar",
    "O Rei",
    "O Senhor é bom (Fernandinho)",
    "O teu amor",
    "Onde o fogo não apaga",
    "Pai das luzes",
    "Phenomena",
    "Perfume",
    "Precioso amigo",
    "Profetiza",
    "Quem é Esse",
    "Romanos 16",
    "Romanos 8:26 (Fernanda Brum)",
    "Rompendo em fé",
    "Salvo pela graça",
    "Santo para sempre (Holy Forever)",
    "Se tú Quiseres Crer",
    "Tempo de Festa",
    "Teu amor não falha",
    "Tocou-me",
    "Tu és o nosso Deus",
    "Tua Alegria",
    "Via Dolorosa",
    "Vida aos sepulcros",
    "Vim falar com Deus",
    "Virada",
    "Yheowa",
]


class Command(BaseCommand):
    help = "Popula o banco de dados com as músicas do repertório"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simula a operação sem gravar no banco",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Apaga todas as músicas existentes antes de inserir",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        clear = options["clear"]

        if dry_run:
            self.stdout.write(self.style.WARNING("Modo dry-run: nenhuma alteração será gravada.\n"))

        if clear and not dry_run:
            count = Music.objects.count()
            Music.objects.all().delete()
            self.stdout.write(self.style.WARNING(f"{count} música(s) removida(s).\n"))

        all_musics = (
            [(name, "lenta") for name in MUSICAS_LENTAS]
            + [(name, "media") for name in MUSICAS_MEDIA]
        )

        created_count = 0
        skipped_count = 0

        for name, tempo in all_musics:
            if dry_run:
                exists = Music.objects.filter(name=name).exists()
                if exists:
                    skipped_count += 1
                    self.stdout.write(f"  [SKIP]  {name}")
                else:
                    created_count += 1
                    self.stdout.write(f"  [NEW]   {name} ({tempo})")
            else:
                _, created = Music.objects.get_or_create(
                    name=name,
                    defaults={"singer": "", "tempo": tempo},
                )
                if created:
                    created_count += 1
                    self.stdout.write(f"  ✓  {name} ({tempo})")
                else:
                    skipped_count += 1
                    self.stdout.write(self.style.WARNING(f"  –  {name} (já existe, pulada)"))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Concluído: {created_count} criada(s), {skipped_count} ignorada(s)."
        ))
