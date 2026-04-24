"""
Management command to migrate legacy Member.ministry (M2M) data
to the new MinistryMembership model. Safe to run multiple times.

Usage:
    python manage.py migrate_legacy_ministries
    python manage.py migrate_legacy_ministries --dry-run
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from website.models import Member, MinistryMembership


class Command(BaseCommand):
    help = 'Migrate legacy Member.ministry M2M data to MinistryMembership records'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be migrated without making changes',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be saved'))

        members = Member.objects.prefetch_related('ministry').filter(
            ministry__isnull=False
        ).distinct()

        created_count = 0
        skipped_count = 0

        with transaction.atomic():
            for member in members:
                for ministry in member.ministry.all():
                    already_exists = MinistryMembership.objects.filter(
                        member=member,
                        ministry=ministry,
                    ).exists()

                    if already_exists:
                        skipped_count += 1
                        self.stdout.write(
                            f'  SKIP  {member.name} → {ministry.name} (already in new system)'
                        )
                    else:
                        created_count += 1
                        self.stdout.write(
                            f'  CREATE {member.name} → {ministry.name}'
                        )
                        if not dry_run:
                            MinistryMembership.objects.create(
                                member=member,
                                ministry=ministry,
                                role='member',
                                is_active=True,
                            )

            if dry_run:
                raise transaction.TransactionManagementError('Dry run — rolling back')

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(f'Created: {created_count}'))
        self.stdout.write(self.style.WARNING(f'Skipped: {skipped_count}'))

        if not dry_run and created_count > 0:
            self.stdout.write('')
            self.stdout.write(
                self.style.SUCCESS(
                    'Migration complete. You can now clear the legacy M2M with:\n'
                    '  Member.ministry.through.objects.all().delete()'
                )
            )
