"""
Popula escalas de teste no ambiente local.

Uso:
    python manage.py populate_schedules
    python manage.py populate_schedules --month 6 --year 2026
    python manage.py populate_schedules --dry-run
"""

from calendar import monthrange
from datetime import date, datetime

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from website.models import Member, Ministry, MinistryMembership, MonthlySchedule, ScheduleDay, Team
from website.models.schedule import DivisionMember, ScaleDivision

User = get_user_model()

PRIMARY_EMAIL = 'rafaelpinheiro@gmail.com'

OTHER_MEMBER_EMAILS = [
    'maria.santos@filadelfia.com',
    'pedro.oliveira@filadelfia.com',
    'ana.costa@filadelfia.com',
    'carlos.souza@filadelfia.com',
    'juliana.lima@filadelfia.com',
]


def _sundays_and_wednesdays(year, month):
    _, last_day = monthrange(year, month)
    dates = []
    for day in range(1, last_day + 1):
        d = date(year, month, day)
        if d.weekday() in (2, 6):  # qua, dom
            dates.append(d)
    return dates


class Command(BaseCommand):
    help = 'Popula escalas mensais e semanais para testes locais'

    def add_arguments(self, parser):
        parser.add_argument('--month', type=int, default=timezone.now().month)
        parser.add_argument('--year', type=int, default=timezone.now().year)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        month = options['month']
        year = options['year']
        dry_run = options['dry_run']

        rafael = Member.objects.filter(user__email=PRIMARY_EMAIL, is_active=True).first()
        if not rafael:
            self.stderr.write(self.style.ERROR(f'Membro não encontrado: {PRIMARY_EMAIL}'))
            return

        others = []
        for email in OTHER_MEMBER_EMAILS:
            member = Member.objects.filter(user__email=email, is_active=True).first()
            if member:
                others.append(member)
            else:
                self.stdout.write(self.style.WARNING(f'Membro ignorado (não encontrado): {email}'))

        all_members = [rafael] + others
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — nada será gravado'))
            self.stdout.write(f'Rafael: {rafael.name} | Outros: {[m.name for m in others]}')
            self.stdout.write(f'Período: {month:02d}/{year}')
            return

        self._ensure_ministry_memberships(rafael, others)
        louvor = Ministry.objects.get(name='Louvor')
        boas_vindas = Ministry.objects.get(name='Boas Vindas')
        ministeracao = Ministry.objects.get(name='Ministração')
        moderacao = Ministry.objects.get(name='Moderação')
        midia_externa = Ministry.objects.get(name='Mídia Externa')

        team_alpha, _ = Team.objects.get_or_create(
            ministry=louvor,
            name='Equipe Alpha',
            defaults={'color': '#C90905', 'leader': rafael},
        )
        team_alpha.members.add(rafael)
        team_beta, _ = Team.objects.get_or_create(
            ministry=louvor,
            name='Equipe Beta',
            defaults={'color': '#260200'},
        )
        for m in others[:3]:
            team_beta.members.add(m)

        midia_team, _ = Team.objects.get_or_create(
            ministry=midia_externa,
            name='Equipe Gravação',
            defaults={'color': '#C90905', 'leader': rafael},
        )
        midia_team.members.set([rafael, others[1] if len(others) > 1 else rafael])

        cult_dates = _sundays_and_wednesdays(year, month)
        published_at = timezone.now()

        self._create_louvor_schedule(
            louvor, month, year, cult_dates, rafael, others,
            team_alpha, team_beta, published_at,
        )
        self._create_boas_vindas_schedule(
            boas_vindas, month, year, cult_dates, rafael, others, published_at,
        )
        self._create_ministeracao_schedule(
            ministeracao, month, year, cult_dates, rafael, others, published_at,
        )
        self._create_moderacao_schedule(
            moderacao, month, year, cult_dates, rafael, others, published_at,
        )
        self._create_midia_weekly_schedule(
            midia_externa, year, month, rafael, others, midia_team, published_at,
        )

        self.stdout.write(self.style.SUCCESS(
            f'Escalas de teste criadas/atualizadas para {month:02d}/{year}. '
            f'Rafael ({PRIMARY_EMAIL}) escalado em todas.'
        ))

    def _ensure_ministry_memberships(self, rafael, others):
        ministry_names = ['Louvor', 'Boas Vindas', 'Ministração', 'Moderação', 'Mídia Externa']
        ministries = Ministry.objects.filter(name__in=ministry_names)
        for ministry in ministries:
            for member in [rafael] + others:
                MinistryMembership.objects.get_or_create(
                    member=member,
                    ministry=ministry,
                    defaults={'is_active': True},
                )

    def _upsert_monthly(self, ministry, title, month, year, **defaults):
        schedule, created = MonthlySchedule.objects.get_or_create(
            ministry=ministry,
            title=title,
            schedule_type=MonthlySchedule.TYPE_MONTHLY,
            month=month,
            year=year,
            defaults=defaults,
        )
        if not created:
            for key, value in defaults.items():
                setattr(schedule, key, value)
            schedule.save()
        return schedule, created

    def _create_louvor_schedule(self, ministry, month, year, cult_dates, rafael, others, team_alpha, team_beta, published_at):
        schedule, _ = self._upsert_monthly(
            ministry,
            f'Escala de Louvor - {month:02d}/{year}',
            month,
            year,
            use_team_rotation=False,
            guidelines='Chegar 30 min antes do culto. Confirmar presença no grupo.',
            is_published=True,
            published_at=published_at,
        )

        div_ministro, _ = ScaleDivision.objects.get_or_create(
            schedule=schedule, name='Ministro', parent=None, defaults={'order': 0},
        )
        div_back, _ = ScaleDivision.objects.get_or_create(
            schedule=schedule, name='Back Vocal', parent=None, defaults={'order': 1},
        )
        div_musicos, _ = ScaleDivision.objects.get_or_create(
            schedule=schedule, name='Músicos', parent=None, defaults={'order': 2},
        )

        pool = [rafael] + others
        for i, cult_date in enumerate(cult_dates):
            is_sunday = cult_date.weekday() == 6
            desc = 'Culto de Domingo - Manhã 9h' if is_sunday else 'Culto de Quarta - Noite 19h30'
            day, _ = ScheduleDay.objects.get_or_create(
                schedule=schedule,
                date=cult_date,
                defaults={'description': desc},
            )
            day.description = desc
            day.is_cancelled = False
            day.has_shifts = False
            day.team = team_alpha if i % 2 == 0 else team_beta
            day.save()

            day.members.set([pool[i % len(pool)], pool[(i + 1) % len(pool)]])

            DivisionMember.objects.filter(schedule_day=day).delete()
            DivisionMember.objects.create(
                division=div_ministro, schedule_day=day, member=pool[i % len(pool)],
            )
            if len(pool) > 1:
                DivisionMember.objects.create(
                    division=div_back, schedule_day=day, member=pool[(i + 1) % len(pool)],
                )
            if len(pool) > 2:
                DivisionMember.objects.create(
                    division=div_musicos, schedule_day=day, member=pool[(i + 2) % len(pool)],
                )

        self.stdout.write(f'  Louvor: {schedule.days.count()} dias')

    def _create_boas_vindas_schedule(self, ministry, month, year, cult_dates, rafael, others, published_at):
        schedule, _ = self._upsert_monthly(
            ministry,
            f'Portaria - {month:02d}/{year}',
            month,
            year,
            use_team_rotation=False,
            guidelines='Uniforme completo. Receber visitantes com um sorriso.',
            is_published=True,
            published_at=published_at,
        )

        pool = [rafael] + others
        sundays = [d for d in cult_dates if d.weekday() == 6]
        for i, cult_date in enumerate(sundays):
            day, _ = ScheduleDay.objects.get_or_create(
                schedule=schedule,
                date=cult_date,
                defaults={'description': 'Recepção - Culto Domingo'},
            )
            day.description = 'Recepção - Culto Domingo'
            day.has_shifts = True
            day.team = None
            day.save()
            day.members.clear()

            morning = [pool[i % len(pool)], pool[(i + 2) % len(pool)]]
            evening = [pool[(i + 1) % len(pool)], pool[(i + 3) % len(pool)]]
            day.members_morning.set(morning)
            day.members_evening.set(evening)

        self.stdout.write(f'  Boas Vindas: {schedule.days.count()} dias (com turnos)')

    def _create_ministeracao_schedule(self, ministry, month, year, cult_dates, rafael, others, published_at):
        schedule, _ = self._upsert_monthly(
            ministry,
            f'Pregação - {month:02d}/{year}',
            month,
            year,
            use_team_rotation=False,
            guidelines='Enviar esboço até quinta-feira.',
            is_published=True,
            published_at=published_at,
        )

        wednesdays = [d for d in cult_dates if d.weekday() == 2]
        pool = [rafael] + others
        for i, cult_date in enumerate(wednesdays):
            day, _ = ScheduleDay.objects.get_or_create(
                schedule=schedule,
                date=cult_date,
                defaults={'description': 'Ministração da Palavra'},
            )
            day.description = 'Ministração da Palavra'
            day.has_shifts = False
            day.team = None
            day.save()
            day.members.set([pool[i % len(pool)]])

        self.stdout.write(f'  Ministração: {schedule.days.count()} dias')

    def _create_moderacao_schedule(self, ministry, month, year, cult_dates, rafael, others, published_at):
        schedule, _ = self._upsert_monthly(
            ministry,
            f'Moderação Cultos - {month:02d}/{year}',
            month,
            year,
            use_team_rotation=False,
            is_published=True,
            published_at=published_at,
        )

        pool = [rafael] + others
        for i, cult_date in enumerate(cult_dates):
            day, _ = ScheduleDay.objects.get_or_create(
                schedule=schedule,
                date=cult_date,
                defaults={'description': 'Moderação do culto'},
            )
            day.description = 'Moderação do culto'
            day.has_shifts = False
            day.save()
            day.members.set([pool[i % len(pool)], pool[(i + 1) % len(pool)]])

        self.stdout.write(f'  Moderação: {schedule.days.count()} dias')

    def _create_midia_weekly_schedule(self, ministry, year, month, rafael, others, midia_team, published_at):
        start = date(year, month, 1)
        _, last = monthrange(year, month)
        end = date(year, month, last)

        schedule, _ = MonthlySchedule.objects.get_or_create(
            ministry=ministry,
            title='Gravação Semanal',
            schedule_type=MonthlySchedule.TYPE_WEEKLY,
            defaults={
                'start_date': start,
                'end_date': end,
                'use_team_rotation': True,
                'guidelines': 'Conferir equipamentos antes de cada gravação.',
                'is_published': True,
                'published_at': published_at,
            },
        )
        schedule.start_date = start
        schedule.end_date = end
        schedule.use_team_rotation = True
        schedule.is_published = True
        schedule.published_at = published_at
        schedule.set_days_of_week([2, 6])  # qua, dom
        schedule.save()

        current = start
        pool = [rafael] + others
        idx = 0
        while current <= end:
            if current.weekday() in (2, 6):
                day, _ = ScheduleDay.objects.get_or_create(
                    schedule=schedule,
                    date=current,
                    defaults={
                        'day_of_week': current.weekday(),
                        'description': 'Gravação do culto',
                    },
                )
                day.description = 'Gravação do culto'
                day.day_of_week = current.weekday()
                day.team = midia_team
                day.save()
                day.members.set([pool[idx % len(pool)]])
                idx += 1
            current = date.fromordinal(current.toordinal() + 1)

        self.stdout.write(f'  Mídia Externa (semanal): {schedule.days.count()} dias')
