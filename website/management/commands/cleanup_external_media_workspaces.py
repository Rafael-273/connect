from django.core.management.base import BaseCommand

from website.external_media.workspace import MediaWorkspaceGarbageCollector


class Command(BaseCommand):
    help = 'Remove workspaces temporarios de midia abandonados.'

    def add_arguments(self, parser):
        parser.add_argument('--max-age-hours', type=int, default=None)

    def handle(self, *args, **options):
        result = MediaWorkspaceGarbageCollector.collect(max_age_hours=options['max_age_hours'])
        self.stdout.write(self.style.SUCCESS(
            f"{result['removed']} workspace(s) removido(s), "
            f"{result['bytes_removed']} bytes liberados, {result['skipped']} mantido(s)."
        ))
