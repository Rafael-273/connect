from datetime import date, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from website.models import Member, Ministry, MinistryMembership, MonthlySchedule, ScheduleDay, Team, User
from website.models.schedule import DivisionMember, ScaleDivision
from website.services.member_schedules import upcoming_member_schedules


class HomeMemberSchedulesTests(TestCase):
    today = date(2026, 10, 18)

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('home-schedules@example.test', password='test')
        cls.member = Member.objects.create(user=cls.user, name='Voluntário')
        cls.ministry = Ministry.objects.create(name='Ministério das escalas')
        cls.membership = MinistryMembership.objects.create(member=cls.member, ministry=cls.ministry)
        cls.schedule = MonthlySchedule.objects.create(
            ministry=cls.ministry, title='Recepção', month=10, year=2026, is_published=True,
        )

    def day(self, offset=1, *, schedule=None, **kwargs):
        return ScheduleDay.objects.create(
            schedule=schedule or self.schedule, date=self.today + timedelta(days=offset), **kwargs,
        )

    def entries(self, **kwargs):
        return upcoming_member_schedules(self.member, today=self.today, **kwargs)

    def test_nearest_assigned_day_is_first_and_includes_today(self):
        later = self.day(4)
        later.members.add(self.member)
        nearest = self.day(0)
        nearest.members.add(self.member)
        self.assertEqual([entry['day'] for entry in self.entries()], [nearest, later])
        self.assertEqual(self.entries()[0]['relative_date'], 'Hoje')

    def test_ignores_days_without_assignment_past_cancelled_and_drafts(self):
        self.day(1)
        self.day(-1).members.add(self.member)
        self.day(2, is_cancelled=True).members.add(self.member)
        draft = MonthlySchedule.objects.create(
            ministry=self.ministry, title='Rascunho', month=10, year=2026,
        )
        self.day(3, schedule=draft).members.add(self.member)
        self.assertEqual(self.entries(), [])

    def test_active_team_assignment_is_included_once(self):
        team = Team.objects.create(name='Equipe A', ministry=self.ministry)
        team.members.add(self.member)
        day = self.day(team=team)
        day.members.add(self.member)
        self.assertEqual(len(self.entries()), 1)
        day.members.clear()
        team.is_active = False
        team.save()
        self.assertEqual(self.entries(), [])

    def test_shift_membership_ignores_stale_non_shift_assignments(self):
        day = self.day(has_shifts=True)
        day.members.add(self.member)
        self.assertEqual(self.entries(), [])
        day.members_morning.add(self.member)
        self.assertEqual(self.entries()[0]['shift'], 'Manhã')
        day.members_evening.add(self.member)
        self.assertEqual(self.entries()[0]['shift'], 'Manhã e Noite')

    def test_division_assignment_and_shift_are_included(self):
        day = self.day(has_shifts=True)
        division = ScaleDivision.objects.create(schedule=self.schedule, name='Fotografia')
        assignment = DivisionMember.objects.create(
            schedule_day=day, division=division, member=self.member, shift='evening',
        )
        entry = self.entries()[0]
        self.assertEqual(entry['shift'], 'Noite')
        self.assertEqual(entry['divisions'], 'Fotografia')
        assignment.delete()
        self.assertEqual(self.entries(), [])

    def test_inactive_or_missing_ministry_membership_is_excluded(self):
        self.day().members.add(self.member)
        self.membership.is_active = False
        self.membership.save()
        self.assertEqual(self.entries(), [])

    def test_aggregates_different_ministries_with_bounded_queries(self):
        other = Ministry.objects.create(name='Outro ministério das escalas')
        MinistryMembership.objects.create(member=self.member, ministry=other)
        schedule = MonthlySchedule.objects.create(
            ministry=other, title='Louvor', month=10, year=2026, is_published=True,
        )
        self.day(2).members.add(self.member)
        self.day(1, schedule=schedule).members.add(self.member)
        with self.assertNumQueries(2):
            entries = self.entries()
        self.assertEqual([entry['schedule'].ministry for entry in entries], [other, self.ministry])

    def test_weekly_uses_recorded_days_and_respects_validity(self):
        schedule = MonthlySchedule.objects.create(
            ministry=self.ministry, title='Semanal', schedule_type='weekly',
            start_date=self.today, end_date=self.today + timedelta(days=7),
            days_of_week='0', is_published=True,
        )
        day = self.day(1, schedule=schedule)
        day.members.add(self.member)
        self.day(8, schedule=schedule).members.add(self.member)
        self.assertEqual([entry['day'] for entry in self.entries()], [day])
        self.client.force_login(self.user)
        with patch('website.services.member_schedules.timezone.localdate', return_value=self.today):
            response = self.client.get(reverse('member_schedule_detail', args=[schedule.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Escala semanal')
        self.assertEqual(len(response.context['days']), 1)

    def test_home_shows_next_day_and_links_to_original_schedule(self):
        self.day(1).members.add(self.member)
        self.client.force_login(self.user)
        with patch('website.services.member_schedules.timezone.localdate', return_value=self.today):
            response = self.client.get(reverse('member_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sua próxima escala')
        self.assertContains(response, 'Amanhã')
        self.assertContains(response, self.ministry.name)
        self.assertContains(response, reverse('member_schedule_detail', args=[self.schedule.pk]))

    def test_no_assignment_has_a_useful_empty_state(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('member_dashboard'))
        self.assertContains(response, 'Você ainda não tem próximos dias de escala publicados.')
        self.assertNotContains(response, 'Sua próxima escala')
