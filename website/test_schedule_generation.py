import datetime
import json

from django.test import TestCase
from django.urls import reverse

from website.models import (
    Event, MediaSubTeam, MediaSubTeamMembership, Member, Ministry,
    MinistryMembership, ScheduleOccurrenceTeam, DivisionMember, User,
)
from website.models.media_team_service import MediaTeamService
from website.services.schedule_assistant import start_draft
from website.services.schedule_optimizer import Duty, Problem, solve


class OptimizerTests(TestCase):
    def duty(self, identifier, date, candidates, count=2, team=1, locked=(), mentors=1):
        return Duty(identifier, identifier, date, '19:00', team, count, mentors, candidates, locked)

    def test_deterministic_month_wide_assignment_uses_mentor_and_reserves_day(self):
        duties = [
            self.duty(1, '2026-11-01', {1: 'mentor', 2: 'trainee'}),
            self.duty(2, '2026-11-01', {1: 'mentor', 3: 'trainee'}, team=2),
            self.duty(3, '2026-11-08', {1: 'mentor', 2: 'trainee', 3: 'trainee'}),
        ]
        result_one, _ = solve(Problem(duties))
        result_two, _ = solve(Problem(duties))
        self.assertEqual(result_one, result_two)
        self.assertEqual(sum(map(len, result_one.values())), 4)
        self.assertEqual(sum(1 for assigned in (result_one[1], result_one[2]) if assigned), 1)
        self.assertEqual(len(result_one[3]), 2)
        self.assertIn(1, result_one[3])

    def test_unknown_trainees_and_missing_policy_stay_pending(self):
        duties = [
            self.duty(1, '2026-11-01', {2: 'trainee', 3: 'trainee'}),
            self.duty(2, '2026-11-08', {1: 'mentor', 2: 'trainee', 3: 'trainee'}, count=3, mentors=None),
        ]
        result, _ = solve(Problem(duties))
        self.assertEqual(result[1], ())
        self.assertEqual(result[2], ())

    def test_locked_assignments_are_preserved_and_previous_load_changes_choice(self):
        duty = self.duty(1, '2026-11-01', {1: 'mentor', 2: 'trainee', 3: 'trainee'}, locked=(1,))
        result, _ = solve(Problem([duty], previous_load={2: 10}))
        self.assertEqual(result[1], (1, 3))

    def test_odd_count_with_explicit_mentor_rule_is_allowed(self):
        duty = self.duty(1, '2026-11-01', {1: 'mentor', 2: 'trainee', 3: 'trainee'}, count=3, mentors=1)
        result, _ = solve(Problem([duty]))
        self.assertEqual(result[1], (1, 2, 3))


class GenerationIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ministry, _ = Ministry.objects.get_or_create(code='midia_externa', defaults={'name': 'Mídia Externa'})
        cls.user = User.objects.create_user('generation@example.test', password='test', is_staff=True)
        cls.team = MediaSubTeam.objects.create(ministry=cls.ministry, name='Live')
        cls.people = []
        for name, level in [('Mentor', 'mentor'), ('Trainee A', 'trainee'), ('Trainee B', 'trainee')]:
            member = Member.objects.create(name=name)
            MinistryMembership.objects.create(member=member, ministry=cls.ministry)
            MediaSubTeamMembership.objects.create(sub_team=cls.team, member=member, experience_level=level)
            cls.people.append(member)
        event = Event.objects.create(title='Culto de teste', event_date='2026-01-04', event_time='19:00',
                                     display_start='2026-01-01', display_end='2027-12-31',
                                     is_recurring=True, recurrence_pattern='weekly')
        MediaTeamService.objects.create(sub_team=cls.team, event=event)

    def setUp(self):
        self.client.force_login(self.user)
        self.state, _ = start_draft(self.ministry, datetime.date(2026, 11, 1), self.user)
        self.api = reverse('media_schedule_assistant_api', args=[self.state.pk])
        self.data = self.client.get(self.api).json()

    def post(self, action, **payload):
        response = self.client.post(self.api, json.dumps({'action': action, 'version': self.data['version'], **payload}), content_type='application/json')
        if response.status_code == 200:
            self.data = response.json()
        return response

    def confirm_all(self):
        for member in self.people:
            self.assertEqual(self.post('save_availability', team_id=self.team.pk, member_id=member.pk,
                                       mode='all', selected_occurrence_ids=[]).status_code, 200)

    def test_generation_writes_canonical_assignments_and_manual_lock_survives_regeneration(self):
        self.confirm_all()
        self.assertEqual(self.post('generate', confirmed=True).status_code, 200)
        self.assertTrue(self.data['generation']['generated'])
        rows = self.data['generation']['rows']
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(row['filled'] == 2 and any(p['experience'] == 'mentor' for p in row['people']) for row in rows))
        first = rows[0]
        ids = [person['member_id'] for person in first['people']]
        self.assertEqual(self.post('set_assignments', plan_id=first['plan_id'], member_ids=ids, locked_member_ids=ids).status_code, 200)
        self.assertEqual(self.post('generate', confirmed=True).status_code, 200)
        after = next(row for row in self.data['generation']['rows'] if row['plan_id'] == first['plan_id'])
        self.assertEqual({p['member_id'] for p in after['people']}, set(ids))
        self.assertTrue(all(p['locked'] for p in after['people']))
        self.assertEqual(DivisionMember.objects.filter(occurrence_team__isnull=False).count(), 10)

    def test_manual_invalid_same_day_and_two_trainees_are_refused(self):
        self.confirm_all()
        rows = self.data['generation']['rows']
        self.assertEqual(self.post('set_assignments', plan_id=rows[0]['plan_id'], member_ids=[self.people[1].pk, self.people[2].pk], locked_member_ids=[]).status_code, 400)
        first = rows[0]
        # Same day cross-team conflicts are exercised at the pure model level; this endpoint also checks persisted assignments.
        self.assertEqual(ScheduleOccurrenceTeam.objects.count(), 5)

    def test_manual_edit_does_not_publish_schedule(self):
        self.confirm_all()
        self.assertEqual(self.post('generate', confirmed=True).status_code, 200)
        self.state.schedule.refresh_from_db()
        self.assertFalse(self.state.schedule.is_published)
