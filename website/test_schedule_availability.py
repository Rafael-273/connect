import datetime
import json

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from website.models import (
    User, Member, Ministry, MinistryMembership, MediaSubTeam, MediaSubTeamMembership,
    Event, MediaTeamService, MonthlySchedule, ScheduleDay, ScheduleAssistantState,
    ScheduleOccurrence, ScheduleOccurrenceTeam, ScheduleAvailability, ScheduleAvailabilityOccurrence, DivisionMember,
)
from website.services.schedule_assistant import start_draft
from website.services.schedule_availability import (
    serialize_availability, available_candidates, validate_daily_service_limit, AVAILABLE, UNKNOWN,
)


class ScheduleAvailabilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ministry, _ = Ministry.objects.get_or_create(code='midia_externa', defaults={'name': 'Mídia Externa'})
        cls.user = User.objects.create_user('availability-secretary@example.test', password='test', is_staff=True)
        cls.coverage = MediaSubTeam.objects.create(ministry=cls.ministry, name='Cobertura')
        cls.live = MediaSubTeam.objects.create(ministry=cls.ministry, name='Live')
        cls.people = {}
        for name in ('Maria', 'João', 'Ana', 'Pedro'):
            member = Member.objects.create(name=name)
            MinistryMembership.objects.create(member=member, ministry=cls.ministry)
            cls.people[name] = member
        cls.maria = cls.people['Maria']; cls.joao = cls.people['João']; cls.ana = cls.people['Ana']; cls.pedro = cls.people['Pedro']
        MediaSubTeamMembership.objects.create(sub_team=cls.coverage, member=cls.maria, experience_level='mentor')
        MediaSubTeamMembership.objects.create(sub_team=cls.coverage, member=cls.joao, experience_level='trainee')
        MediaSubTeamMembership.objects.create(sub_team=cls.coverage, member=cls.ana)
        MediaSubTeamMembership.objects.create(sub_team=cls.live, member=cls.maria, experience_level='trainee')
        MediaSubTeamMembership.objects.create(sub_team=cls.live, member=cls.pedro, experience_level='mentor')
        for title, hour in [('Culto da manhã', '10:00'), ('Culto da noite', '19:00')]:
            event = Event.objects.create(title=title, event_date='2026-01-04', event_time=hour,
                                         display_start='2026-01-01', display_end='2027-12-31',
                                         is_recurring=True, recurrence_pattern='weekly')
            MediaTeamService.objects.create(sub_team=cls.coverage, event=event)
            MediaTeamService.objects.create(sub_team=cls.live, event=event)

    def setUp(self):
        self.client.force_login(self.user)
        self.state, _ = start_draft(self.ministry, datetime.date(2026, 11, 1), self.user)
        self.api = reverse('media_schedule_assistant_api', args=[self.state.pk])
        self.data = self.client.get(self.api).json()

    def post(self, action, **payload):
        version = payload.pop('version', self.data['version'])
        response = self.client.post(self.api, json.dumps({'action': action, 'version': version, **payload}), content_type='application/json')
        if response.status_code == 200 and not response.json().get('discarded'):
            self.data = response.json()
        return response

    def save(self, mode='all', selected=None, team=None, member=None, **kwargs):
        return self.post('save_availability', mode=mode, team_id=(team or self.coverage).pk,
                         member_id=(member or self.maria).pk, selected_occurrence_ids=selected or [], **kwargs)

    def person(self, team=None, member=None):
        return next(person for person in self.data['availability']['roster'] if person['team_id'] == (team or self.coverage).pk and person['member_id'] == (member or self.maria).pk)

    def relevant(self):
        return self.data['occurrences']

    def plan_edit(self, item, **changes):
        return {'occurrence_id': item['id'], 'name': item['name'], 'date': item['date'], 'time': item['time'], 'notes': item['notes'],
                'teams': [{'team_id': team['team_id'], 'member_count': team['member_count'], 'activity_ids': [activity['id'] for activity in team['activities']], 'notes': team['notes']} for team in item['teams']], **changes}

    def test_unreported_is_unknown_not_available_and_no_members_are_created(self):
        count = Member.objects.count()
        self.assertTrue(all(cell['status'] == 'unknown' for person in self.data['availability']['roster'] for cell in person['occurrences']))
        summary = self.data['availability']['summary']
        self.assertEqual(summary['total_members'], 4)
        self.assertEqual(summary['recorded_members'], 0)
        self.assertEqual(summary['pending_members'], 4)
        self.assertTrue(all(row['available'] == 0 for row in summary['rows']))
        self.assertEqual(Member.objects.count(), count)
        self.assertEqual(ScheduleAvailability.objects.count(), 0)

    def test_all_confirms_current_occurrences_and_preserves_mode(self):
        self.assertEqual(self.save().status_code, 200)
        person = self.person(); self.assertEqual(person['record']['mode'], 'all')
        self.assertEqual(person['record']['selected_occurrence_ids'], [])
        self.assertTrue(all(cell['status'] == 'available' for cell in person['occurrences']))
        record = ScheduleAvailability.objects.get()
        self.assertEqual(record.recorded_by, self.user)
        self.assertEqual(record.confirmations.count(), 10)
        self.assertEqual(DivisionMember.objects.count(), 0)
        self.assertFalse(any(day.members.exists() for day in self.state.schedule.days.all()))

    def test_only_distinguishes_two_cults_on_same_day(self):
        rows = [item for item in self.relevant() if item['date'] == '2026-11-01']
        morning = next(item for item in rows if item['time'] == '10:00')
        evening = next(item for item in rows if item['time'] == '19:00')
        self.assertEqual(self.save('only', [morning['id']]).status_code, 200)
        statuses = {cell['occurrence_id']: cell['status'] for cell in self.person()['occurrences']}
        self.assertEqual(statuses[morning['id']], 'available')
        self.assertEqual(statuses[evening['id']], 'unavailable')
        self.assertEqual(sum(value == 'available' for value in statuses.values()), 1)

    def test_except_preserves_exceptions_and_confirms_other_occurrences(self):
        excluded = [item['id'] for item in self.relevant() if item['date'] == '2026-11-15']
        self.assertEqual(self.save('except', excluded).status_code, 200)
        self.assertEqual(self.person()['record']['selected_occurrence_ids'], excluded)
        statuses = {cell['occurrence_id']: cell['status'] for cell in self.person()['occurrences']}
        self.assertTrue(all(statuses[identifier] == 'unavailable' for identifier in excluded))
        self.assertEqual(sum(value == 'available' for value in statuses.values()), 8)

    def test_empty_only_means_confirmed_unavailable_not_unknown(self):
        self.assertEqual(self.save('only').status_code, 200)
        self.assertTrue(all(cell['status'] == 'unavailable' for cell in self.person()['occurrences']))
        self.assertFalse(self.person()['needs_review'])

    def test_empty_except_means_all_confirmed(self):
        self.assertEqual(self.save('except').status_code, 200)
        self.assertTrue(all(cell['status'] == 'available' for cell in self.person()['occurrences']))

    def test_edits_restore_mode_selection_notes_and_keep_identity(self):
        selected = [self.relevant()[0]['id']]
        self.save('only', selected, notes='Recebido pelo WhatsApp')
        record = self.person()['record']; identifier = record['id']
        self.data = self.client.get(self.api).json()
        self.assertEqual(self.person()['record']['notes'], 'Recebido pelo WhatsApp')
        self.save('except', selected, notes='Atualizado')
        self.assertEqual(self.person()['record']['id'], identifier)
        self.assertEqual(ScheduleAvailability.objects.count(), 1)
        self.assertEqual(ScheduleAvailability.objects.get().history.count(), 2)
        self.assertEqual(self.person()['record']['mode'], 'except')

    def test_other_teams_visible_but_availability_is_not_copied_between_teams(self):
        self.save()
        self.assertEqual(self.person()['other_teams'], [{'id': self.live.pk, 'name': 'Live'}])
        other = self.person(self.live)
        self.assertIsNone(other['record'])
        self.assertTrue(all(cell['status'] == 'unknown' for cell in other['occurrences']))
        self.assertEqual(other['experience_level'], 'trainee')
        self.assertEqual(self.person()['experience_level'], 'mentor')

    def test_previous_month_is_not_reused(self):
        self.save()
        next_state, _ = start_draft(self.ministry, datetime.date(2026, 12, 1), self.user)
        next_data = self.client.get(reverse('media_schedule_assistant_api', args=[next_state.pk])).json()
        self.assertTrue(all(person['record'] is None for person in next_data['availability']['roster']))
        self.assertEqual(next_data['availability']['summary']['recorded_members'], 0)

    def test_new_occurrence_is_unknown_even_when_mode_was_all(self):
        self.save()
        self.post('add_manual', name='Extra', date='2026-11-02', time='20:00')
        extra = next(item for item in self.data['occurrences'] if item['name'] == 'Extra')
        teams = [{'team_id': self.coverage.pk, 'member_count': 2, 'activity_ids': [], 'notes': ''}]
        self.post('save_occurrence', **self.plan_edit(extra, teams=teams))
        person = self.person(); self.assertTrue(person['needs_review'])
        statuses = {cell['occurrence_id']: cell['status'] for cell in person['occurrences']}
        self.assertEqual(statuses[extra['id']], 'unknown')
        self.assertEqual(sum(value == 'available' for value in statuses.values()), 10)
        self.assertEqual(self.save().status_code, 400)
        self.assertEqual(self.save(confirm_current_plan=True).status_code, 200)
        self.assertTrue(all(cell['status'] == 'available' for cell in self.person()['occurrences']))
        self.assertFalse(self.person()['needs_review'])
        self.assertEqual(self.data['availability_review'], [])

    def test_changed_time_preserves_valid_confirmations_and_requires_review(self):
        self.save()
        original = self.relevant()[0]; unchanged = self.relevant()[1]
        record = ScheduleAvailability.objects.get(); confirmation = record.confirmations.get(occurrence_id=original['id'])
        self.post('save_occurrence', **self.plan_edit(original, time='09:30'))
        statuses = {cell['occurrence_id']: cell['status'] for cell in self.person()['occurrences']}
        self.assertEqual(statuses[original['id']], 'unknown')
        self.assertEqual(statuses[unchanged['id']], 'available')
        confirmation.refresh_from_db(); self.assertTrue(confirmation.is_available)
        self.assertEqual(confirmation.start_time, datetime.time(10))
        self.assertEqual(self.save().status_code, 400)
        self.save(confirm_current_plan=True)
        confirmation.refresh_from_db(); self.assertEqual(confirmation.start_time, datetime.time(9, 30))
        self.assertEqual(self.person()['record']['id'], record.pk)

    def test_notes_and_quantities_do_not_invalidate_confirmed_time_windows(self):
        self.save(); item = self.relevant()[0]
        edit = self.plan_edit(item, notes='Orientação adicional')
        edit['teams'][0]['member_count'] = 4
        self.post('save_occurrence', **edit)
        self.assertFalse(self.person()['needs_review'])
        self.assertTrue(all(cell['status'] == 'available' for cell in self.person()['occurrences']))

    def test_removed_occurrence_keeps_original_exception_without_affecting_other_data(self):
        item = self.relevant()[0]; self.save('except', [item['id']])
        record = ScheduleAvailability.objects.get()
        self.post('remove_occurrence', occurrence_id=item['id'])
        record.refresh_from_db()
        self.assertEqual(record.selected_occurrence_ids, [item['id']])
        self.assertTrue(record.confirmations.filter(occurrence_id=item['id']).exists())
        self.assertEqual(self.person()['removed_selections'][0]['id'], item['id'])
        self.assertEqual(self.save('except', [item['id']]).status_code, 200)
        self.post('add_source', source_key=item['source_key'])
        status = next(cell['status'] for cell in self.person()['occurrences'] if cell['occurrence_id'] == item['id'])
        self.assertEqual(status, 'unavailable')

    def test_restored_occurrence_is_unknown_if_its_old_confirmation_conflicts_with_new_mode(self):
        item = self.relevant()[0]
        self.save('except', [item['id']])
        self.post('remove_occurrence', occurrence_id=item['id'])
        self.assertEqual(self.save('all').status_code, 200)
        self.post('add_source', source_key=item['source_key'])
        self.assertEqual(next(cell['status'] for cell in self.person()['occurrences'] if cell['occurrence_id'] == item['id']), 'unknown')
        plan = ScheduleOccurrenceTeam.objects.get(occurrence_id=item['id'], sub_team=self.coverage)
        self.assertEqual(available_candidates(self.state, plan), [])
        self.assertTrue(self.person()['needs_review'])
        self.assertEqual(self.save(confirm_current_plan=True).status_code, 200)
        self.assertEqual(available_candidates(self.state, plan), [self.maria])

    def test_removed_and_readded_team_requires_confirmation_for_new_participation(self):
        self.save(); item = self.relevant()[0]
        edit = self.plan_edit(item); original = edit['teams']
        edit['teams'] = [team for team in original if team['team_id'] != self.coverage.pk]
        self.post('save_occurrence', **edit)
        self.post('save_occurrence', **self.plan_edit(item))
        self.assertTrue(self.person()['needs_review'])
        self.assertEqual(next(cell['status'] for cell in self.person()['occurrences'] if cell['occurrence_id'] == item['id']), 'unknown')

    def test_clear_is_local_unknown_and_resave_reuses_declaration(self):
        self.save(); first = self.person()['record']['id']
        self.save(team=self.live)
        self.post('clear_availability', team_id=self.coverage.pk, member_id=self.maria.pk)
        self.assertIsNone(self.person()['record'])
        self.assertIsNotNone(self.person(self.live)['record'])
        self.assertTrue(all(cell['status'] == 'unknown' for cell in self.person()['occurrences']))
        self.save('only')
        self.assertEqual(self.person()['record']['id'], first)
        self.assertEqual(ScheduleAvailability.objects.count(), 2)

    def test_invalid_modes_selected_ids_and_all_exceptions_are_rejected_atomically(self):
        for mode, ids in [('invalid', []), ('all', [self.relevant()[0]['id']]), ('only', [999999])]:
            self.assertEqual(self.save(mode, ids).status_code, 400)
        self.assertEqual(ScheduleAvailability.objects.count(), 0)
        self.assertEqual(ScheduleAvailabilityOccurrence.objects.count(), 0)

    def test_other_draft_and_other_team_occurrences_are_rejected(self):
        other, _ = start_draft(self.ministry, datetime.date(2026, 12, 1), self.user)
        foreign = ScheduleOccurrence.objects.filter(schedule_day__schedule=other.schedule).first()
        self.assertEqual(self.save('only', [foreign.pk]).status_code, 400)
        team = MediaSubTeam.objects.create(ministry=self.ministry, name='Somente um culto')
        MediaSubTeamMembership.objects.create(sub_team=team, member=self.maria)
        plan = ScheduleOccurrenceTeam.objects.create(occurrence_id=self.relevant()[0]['id'], sub_team=team, team_name=team.name, color='#2563EB')
        self.assertEqual(self.save('only', [self.relevant()[1]['id']], team=team).status_code, 400)
        self.assertEqual(self.save('only', [plan.occurrence_id], team=team).status_code, 200)

    def test_member_must_be_active_in_team_and_ministry(self):
        for member, team in [(self.pedro, self.coverage), (self.ana, self.live)]:
            self.assertEqual(self.save(member=member, team=team).status_code, 404)
        membership = MediaSubTeamMembership.objects.get(member=self.maria, sub_team=self.coverage)
        membership.is_active = False; membership.save()
        self.assertEqual(self.save().status_code, 404)
        self.assertFalse(ScheduleAvailability.objects.exists())

    def test_inactive_members_and_memberships_are_excluded_from_roster_and_counts(self):
        self.maria.is_active = False; self.maria.save()
        data = self.client.get(self.api).json()['availability']
        self.assertFalse(any(person['member_id'] == self.maria.pk for person in data['roster']))
        self.assertEqual(data['summary']['total_members'], 3)

    def test_team_without_occurrences_cannot_create_empty_confirmation(self):
        team = MediaSubTeam.objects.create(ministry=self.ministry, name='Sem atuação')
        MediaSubTeamMembership.objects.create(sub_team=team, member=self.maria)
        self.assertEqual(self.save(team=team).status_code, 400)

    def test_summary_counts_unique_members_and_mentor_level_by_team(self):
        self.save(); self.save(member=self.joao)
        summary = self.data['availability']['summary']
        coverage = next(row for row in summary['rows'] if row['team_id'] == self.coverage.pk)
        self.assertEqual(coverage['available'], 2); self.assertEqual(coverage['unknown'], 1)
        self.assertEqual(coverage['mentors_available'], 1); self.assertEqual(coverage['shortage'], 0)
        self.save(team=self.live)
        live = next(row for row in self.data['availability']['summary']['rows'] if row['team_id'] == self.live.pk)
        self.assertEqual(live['available'], 1); self.assertEqual(live['mentors_available'], 0)
        self.assertTrue(live['mentor_shortage'])
        self.assertEqual(self.data['availability']['summary']['recorded_members'], 2)
        self.assertEqual(self.data['availability']['summary']['informed_members'], 2)

    def test_unknown_experience_is_not_counted_as_mentor(self):
        self.save(member=self.ana)
        coverage = next(row for row in self.data['availability']['summary']['rows'] if row['team_id'] == self.coverage.pk)
        self.assertEqual(coverage['available'], 1); self.assertEqual(coverage['mentors_available'], 0)
        self.assertEqual(coverage['pending_experience_available'], 1)
        self.assertTrue(any('classificação de experiência' in warning for warning in self.data['availability']['summary']['warnings']))

    def test_daily_summary_does_not_double_count_same_volunteer_at_two_cults(self):
        self.save(); self.save(team=self.live)
        sunday = next(row for row in self.data['availability']['summary']['daily'] if row['date'] == '2026-11-01')
        self.assertEqual(sunday['unique_available'], 1)
        self.assertEqual(sunday['minimum_unique_people'], 4)
        self.assertEqual(sunday['shortage'], 3)
        self.assertEqual(sunday['unique_mentors'], 1)
        self.assertEqual(sunday['mentor_shortage'], 1)
        self.assertEqual(sunday['members_with_multiple_cults'], 1)

    def test_availability_can_include_two_cults_but_assignment_contract_limits_one_daily(self):
        self.save()
        plans = list(ScheduleOccurrenceTeam.objects.filter(occurrence__schedule_day__schedule=self.state.schedule, sub_team=self.coverage, occurrence__schedule_day__date='2026-11-01').select_related('occurrence__schedule_day'))
        self.assertEqual(available_candidates(self.state, plans[0]), [self.maria])
        self.assertEqual(available_candidates(self.state, plans[1], {self.maria.pk: [plans[0].occurrence]}), [])
        with self.assertRaises(ValidationError):
            validate_daily_service_limit(plans[1].occurrence, [plans[0].occurrence])
        validate_daily_service_limit(plans[0].occurrence, [plans[0].occurrence])
        other_day = ScheduleOccurrence.objects.filter(schedule_day__schedule=self.state.schedule).exclude(schedule_day__date='2026-11-01').first()
        validate_daily_service_limit(other_day, [plans[0].occurrence])

    def test_generator_candidates_exclude_unknown_and_unavailable(self):
        plan = ScheduleOccurrenceTeam.objects.filter(occurrence__schedule_day__schedule=self.state.schedule, sub_team=self.coverage).first()
        self.assertEqual(available_candidates(self.state, plan), [])
        self.save('only', [])
        self.assertEqual(available_candidates(self.state, plan), [])
        self.save()
        self.assertEqual(available_candidates(self.state, plan), [self.maria])
        occurrence = plan.occurrence; occurrence.start_time = datetime.time(9, 30); occurrence.save()
        plan.refresh_from_db()
        self.assertEqual(available_candidates(self.state, plan), [])

    def test_canceled_days_do_not_count_or_receive_new_confirmations(self):
        day = ScheduleDay.objects.get(schedule=self.state.schedule, date='2026-11-01')
        day.is_cancelled = True; day.save(); self.data = self.client.get(self.api).json()
        self.save()
        self.assertEqual(len(self.person()['occurrences']), 8)
        self.assertFalse(ScheduleAvailabilityOccurrence.objects.filter(occurrence__schedule_day=day).exists())

    def test_stale_version_cannot_overwrite_declaration(self):
        token = self.data['version']; self.save()
        response = self.save('only', [], version=token)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(ScheduleAvailability.objects.get().mode, 'all')

    def test_draft_discard_soft_deletes_availability_without_deleting_agenda(self):
        self.save(); self.post('discard', confirmed=True)
        self.assertFalse(ScheduleAvailability.objects.exists())
        self.assertTrue(ScheduleAvailability.all_objects.exists())
        self.assertEqual(Event.objects.count(), 2)
        self.assertEqual(self.client.post(self.api, '{}', content_type='application/json').status_code, 404)

    def test_published_workflow_rejects_availability_edits(self):
        self.save()
        self.state.status = 'published'; self.state.save()
        self.state.schedule.is_published = True; self.state.schedule.save()
        self.assertEqual(self.save('only').status_code, 404)
        self.assertEqual(ScheduleAvailability.objects.get().mode, 'all')

    def test_volunteers_outsiders_and_csrf_cannot_write(self):
        volunteer = User.objects.create_user('volunteer-availability@example.test')
        member = Member.objects.create(user=volunteer, name='Voluntário')
        MinistryMembership.objects.create(member=member, ministry=self.ministry)
        self.client.force_login(volunteer)
        self.assertEqual(self.save().status_code, 403)
        self.client.force_login(self.user)
        strict = Client(enforce_csrf_checks=True); strict.force_login(self.user)
        self.assertEqual(strict.post(self.api, '{}', content_type='application/json').status_code, 403)
        self.assertFalse(ScheduleAvailability.objects.exists())

    def test_foreign_ministry_not_affected(self):
        ministry = Ministry.objects.create(name='Outro ministério')
        team = MediaSubTeam.objects.create(ministry=ministry, name='Equipe externa')
        member = Member.objects.create(name='Outro membro')
        MinistryMembership.objects.create(member=member, ministry=ministry)
        MediaSubTeamMembership.objects.create(sub_team=team, member=member)
        self.assertEqual(self.save(team=team, member=member).status_code, 404)
        self.assertEqual(ScheduleAvailability.objects.count(), 0)

    def test_can_proceed_with_pending_information_without_confirming_it(self):
        self.assertTrue(self.data['availability']['summary']['warnings'])
        self.assertEqual(self.post('navigate', step=3).status_code, 200)
        self.assertFalse(ScheduleAvailability.objects.exists())
        self.assertEqual(self.data['availability']['summary']['recorded_members'], 0)
        self.state.schedule.refresh_from_db(); self.assertFalse(self.state.schedule.is_published)

    def test_availability_serialization_query_count_is_bounded(self):
        with CaptureQueriesContext(connection) as initial:
            serialize_availability(self.state)
        for number in range(12):
            member = Member.objects.create(name=f'Pessoa {number}')
            MinistryMembership.objects.create(member=member, ministry=self.ministry)
            MediaSubTeamMembership.objects.create(sub_team=self.coverage, member=member)
        with CaptureQueriesContext(connection) as enlarged:
            serialize_availability(self.state)
        self.assertEqual(len(initial), len(enlarged))

    def test_existing_big_member_and_occurrence_identifiers_are_supported(self):
        member = Member.objects.create(id=50000, name='Identificador existente')
        MinistryMembership.objects.create(member=member, ministry=self.ministry)
        MediaSubTeamMembership.objects.create(sub_team=self.coverage, member=member)
        occurrence = ScheduleOccurrence.objects.create(id=60000, schedule_day=self.state.schedule.days.first(), source_key='manual:big-id', name='Culto adicional', start_time='21:00')
        ScheduleOccurrenceTeam.objects.create(occurrence=occurrence, sub_team=self.coverage, team_name='Cobertura', color='#2563EB')
        self.data = self.client.get(self.api).json()
        self.assertEqual(self.save('only', [occurrence.pk], member=member).status_code, 200)
        self.assertEqual(self.person(member=member)['record']['selected_occurrence_ids'], [60000])

    def test_page_contains_availability_ui_and_safe_serialized_notes(self):
        self.save(notes='<img src=x onerror=alert(1)>')
        response = self.client.get(reverse('media_schedule_assistant', args=[self.state.pk]))
        self.assertContains(response, 'Resumo antes de salvar')
        self.assertContains(response, 'schedule_availability.js')
        self.assertNotContains(response, '<img src=x onerror=alert(1)>')


class ScheduleAvailabilityMigrationTests(TransactionTestCase):
    def test_migration_keeps_existing_draft_unknown_without_copying_members(self):
        executor = MigrationExecutor(connection); latest = executor.loader.graph.leaf_nodes()
        previous = ('website', '0121_schedule_assistant_planning')
        try:
            executor.migrate([previous]); apps = executor.loader.project_state([previous]).apps
            ministry, _ = apps.get_model('website', 'Ministry').objects.get_or_create(code='midia_externa', defaults={'name': 'Mídia Externa'})
            schedule = apps.get_model('website', 'MonthlySchedule').objects.create(ministry_id=ministry.pk, title='Rascunho existente', month=11, year=2026)
            state = apps.get_model('website', 'ScheduleAssistantState').objects.create(schedule_id=schedule.pk, ministry_id=ministry.pk, period='2026-11-01')
            MigrationExecutor(connection).migrate(latest)
            self.assertTrue(ScheduleAssistantState.objects.filter(pk=state.pk, schedule_id=schedule.pk).exists())
            self.assertFalse(ScheduleAvailability.objects.exists())
            self.assertFalse(ScheduleAvailabilityOccurrence.objects.exists())
        finally:
            MigrationExecutor(connection).migrate(latest)


class ScheduleAvailabilityConcurrencyTests(TransactionTestCase):
    def test_same_version_cannot_create_or_overwrite_two_declarations(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Bloqueios concorrentes requerem PostgreSQL.')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import connections
        from website.services.schedule_assistant import mutate, serialize, PlanningConflict
        ministry, _ = Ministry.objects.get_or_create(code='midia_externa', defaults={'name': 'Mídia Externa'})
        user = User.objects.create_user('availability-concurrent@example.test', is_staff=True)
        member = Member.objects.create(name='Maria')
        MinistryMembership.objects.create(member=member, ministry=ministry)
        team = MediaSubTeam.objects.create(ministry=ministry, name='Cobertura')
        MediaSubTeamMembership.objects.create(member=member, sub_team=team)
        event = Event.objects.create(title='Culto', event_date='2026-01-04', event_time='19:00', display_start='2026-01-01', display_end='2027-12-31', is_recurring=True, recurrence_pattern='weekly')
        MediaTeamService.objects.create(sub_team=team, event=event)
        state, _ = start_draft(ministry, datetime.date(2026, 11, 1), user)
        token = serialize(state)['version']; barrier = Barrier(2)
        def save(mode):
            try:
                barrier.wait(timeout=10)
                try:
                    mutate(state.pk, ministry, user, {'action': 'save_availability', 'version': token, 'team_id': team.pk, 'member_id': member.pk, 'mode': mode, 'selected_occurrence_ids': []})
                    return 200
                except PlanningConflict:
                    return 409
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(save, ['all', 'only'])), [200, 409])
        self.assertEqual(ScheduleAvailability.objects.count(), 1)
        state.refresh_from_db(); self.assertEqual(state.revision, 2)
