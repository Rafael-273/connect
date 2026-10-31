import datetime
import json

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from website.models import (
    User, Member, Ministry, MinistryMembership, Event, EventDate, MonthlySchedule, ScheduleDay,
    MediaSubTeam, MediaTeamActivity, MediaTeamService, MediaTeamServiceActivity,
    ScheduleAssistantState, ScheduleOccurrence, ScheduleOccurrenceTeam, DivisionMember,
)
from website.services.schedule_agenda import agenda_occurrences, team_color
from website.services.schedule_assistant import start_draft, serialize
from website.services.schedule_visibility import member_visible_schedules


class ScheduleAssistantTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ministry, _ = Ministry.objects.get_or_create(code='midia_externa', defaults={'name': 'Mídia Externa'})
        cls.user = User.objects.create_user('assistant-leader@example.test', password='test')
        cls.member = Member.objects.create(user=cls.user, name='Secretaria')
        MinistryMembership.objects.create(member=cls.member, ministry=cls.ministry, role='leader')
        cls.team = MediaSubTeam.objects.create(ministry=cls.ministry, name='Cobertura')
        cls.live = MediaSubTeam.objects.create(ministry=cls.ministry, name='Live')
        cls.stories = MediaTeamActivity.objects.create(sub_team=cls.team, name='Stories')
        cls.photo = MediaTeamActivity.objects.create(sub_team=cls.team, name='Fotografia', sort_order=1)
        cls.live_activity = MediaTeamActivity.objects.create(sub_team=cls.live, name='Transmissão')
        cls.evening = cls.event('Culto domingo à noite', '2025-01-05', '19:00', True)
        cls.morning = cls.event('Culto domingo de manhã', '2025-01-05', '10:00', True)
        cls.wednesday = cls.event('Culto de quarta', '2025-01-01', '20:00', True)
        cls.special = cls.event('Conferência', '2026-11-13', '19:30')
        cls.extra = EventDate.objects.create(event=cls.special, event_date='2026-11-14', event_time='09:00')
        cls.service = MediaTeamService.objects.create(sub_team=cls.team, event=cls.evening)
        for activity in (cls.stories, cls.photo):
            MediaTeamServiceActivity.objects.create(service=cls.service, activity=activity)
        live_service = MediaTeamService.objects.create(sub_team=cls.live, event=cls.evening, member_count=3)
        MediaTeamServiceActivity.objects.create(service=live_service, activity=cls.live_activity)
        MediaTeamService.objects.create(sub_team=cls.live, event=cls.wednesday)

    @staticmethod
    def event(name, date, time=None, recurring=False, pattern='weekly'):
        return Event.objects.create(title=name, description='Agenda', event_date=date, event_time=time,
                                    display_start='2025-01-01', display_end='2028-12-31',
                                    is_recurring=recurring, recurrence_pattern=pattern if recurring else None)

    def setUp(self):
        self.client.force_login(self.user)
        self.state, _ = start_draft(self.ministry, datetime.date(2026, 11, 1), self.user)
        self.api = reverse('media_schedule_assistant_api', kwargs={'pk': self.state.pk})
        self.data = self.client.get(self.api).json()

    def post(self, action, version=None, **payload):
        response = self.client.post(self.api, json.dumps({'action': action, 'version': version or self.data['version'], **payload}), content_type='application/json')
        if response.status_code == 200 and not response.json().get('discarded'):
            self.data = response.json()
        return response

    def first_evening(self):
        return next(item for item in self.data['occurrences'] if item['source_key'].startswith('regular:sunday_19@'))

    def edit(self, item, **overrides):
        return {'occurrence_id': item['id'], 'name': item['name'], 'date': item['date'], 'time': item['time'], 'notes': item['notes'],
                'teams': [{'team_id': team['team_id'], 'member_count': team['member_count'], 'activity_ids': [activity['id'] for activity in team['activities']], 'notes': team['notes']} for team in item['teams']], **overrides}

    def test_start_reuses_same_draft_and_canonical_schedule(self):
        response = self.client.post(reverse('media_schedule_assistant_start'), {'month': 11, 'year': 2026})
        self.assertRedirects(response, reverse('media_schedule_assistant', kwargs={'pk': self.state.pk}))
        self.assertEqual(ScheduleAssistantState.objects.count(), 1)
        self.assertEqual(MonthlySchedule.objects.count(), 1)
        self.assertEqual(self.state.schedule.ministry_id, self.ministry.pk)
        self.assertFalse(self.state.schedule.is_published)
        self.assertEqual(ScheduleDay.objects.filter(schedule=self.state.schedule).count(), 11)
        self.assertEqual(DivisionMember.objects.count(), 0)

    def test_same_day_contains_independent_cults_and_multiple_teams(self):
        sunday = [item for item in self.data['occurrences'] if item['date'] == '2026-11-01']
        self.assertEqual({item['time'] for item in sunday}, {'09:00', '10:00', '19:00'})
        self.assertGreaterEqual(len(self.data['occurrences']), 18)
        evening = next(item for item in sunday if item['time'] == '19:00')
        self.assertEqual({team['name']: team['member_count'] for team in evening['teams']}, {'Cobertura': 2, 'Live': 3})
        coverage = next(team for team in evening['teams'] if team['team_id'] == self.team.pk)
        self.assertEqual([activity['name'] for activity in coverage['activities']], ['Stories', 'Fotografia'])
        self.assertEqual(coverage['color'], team_color(self.team.pk))

    def test_standard_regular_service_seeds_without_an_agenda_event(self):
        MediaTeamService.objects.create(sub_team=self.team, regular_service='sunday_09')
        draft, _ = start_draft(self.ministry, datetime.date(2027, 1, 1), self.user)
        rows = ScheduleOccurrence.objects.filter(
            schedule_day__schedule=draft.schedule, source_key__startswith='regular:sunday_09@',
        )
        self.assertEqual(rows.count(), 5)
        self.assertTrue(all(row.source_event_id is None for row in rows))
        self.assertTrue(all(row.team_plans.filter(sub_team=self.team).exists() for row in rows))

    def test_resuming_legacy_empty_draft_backfills_missing_regular_services(self):
        period = datetime.date(2027, 2, 1)
        schedule = MonthlySchedule.objects.create(
            ministry=self.ministry, schedule_type='monthly', month=period.month, year=period.year,
            title='Rascunho legado', is_published=False,
        )
        legacy = ScheduleAssistantState.objects.create(
            schedule=schedule, ministry=self.ministry, period=period,
            created_by=self.user, updated_by=self.user,
        )

        response = self.client.get(reverse('media_schedule_assistant_api', kwargs={'pk': legacy.pk}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ScheduleOccurrence.objects.filter(
            schedule_day__schedule=schedule, source_key__startswith='regular:sunday_09@',
        ).count(), 4)
        self.assertEqual(ScheduleOccurrence.objects.filter(
            schedule_day__schedule=schedule, source_key__startswith='regular:sunday_19@',
        ).count(), 4)
        self.assertEqual(ScheduleOccurrence.objects.filter(
            schedule_day__schedule=schedule, source_key__startswith='regular:wednesday_20@',
        ).count(), 4)

    def test_standard_regular_service_prevents_duplicate_matching_agenda_cult(self):
        MediaTeamService.objects.create(sub_team=self.team, regular_service='sunday_19')
        draft, _ = start_draft(self.ministry, datetime.date(2027, 1, 1), self.user)
        evening_rows = ScheduleOccurrence.objects.filter(
            schedule_day__schedule=draft.schedule, start_time='19:00',
            schedule_day__date__week_day=1,
        )
        self.assertEqual(evening_rows.count(), 5)
        self.assertTrue(all(row.source_key.startswith('regular:sunday_19@') for row in evening_rows))

    def test_recurring_teams_and_activity_snapshots_do_not_refresh_on_resume(self):
        before = self.data['occurrences']
        self.stories.name = 'Stories atualizados'; self.stories.save()
        self.service.member_count = 9; self.service.save()
        self.evening.event_time = '21:00'; self.evening.save()
        restored = self.client.get(self.api).json()
        self.assertEqual(restored['occurrences'], before)
        self.assertEqual(ScheduleAssistantState.objects.count(), 1)
        self.assertEqual(restored['month'], 11)

    def test_add_agenda_event_and_extra_date_without_copying_event(self):
        initial_count = Event.objects.count()
        for source in [item for item in self.data['sources'] if item['event_id'] == self.special.pk]:
            self.assertEqual(self.post('add_source', source_key=source['key']).status_code, 200)
        rows = [item for item in self.data['occurrences'] if item['name'] == 'Conferência']
        self.assertEqual({(item['date'], item['time']) for item in rows}, {('2026-11-13', '19:30'), ('2026-11-14', '09:00')})
        self.assertEqual(Event.objects.count(), initial_count)

    def test_recurring_extra_dates_seed_automatically_and_inherit_team_defaults(self):
        extra = EventDate.objects.create(event=self.evening, event_date='2026-12-03', event_time='18:00')
        draft, _ = start_draft(self.ministry, datetime.date(2026, 12, 1), self.user)
        occurrence = ScheduleOccurrence.objects.get(schedule_day__schedule=draft.schedule, source_event_date=extra)
        self.assertEqual(occurrence.team_plans.count(), 2)

    def test_specific_extra_date_defaults_override_root_and_can_disable_a_team(self):
        extra = EventDate.objects.create(event=self.evening, event_date='2026-11-02', event_time='18:00')
        MediaTeamService.objects.create(sub_team=self.team, event=self.evening, event_date=extra, member_count=4)
        MediaTeamService.objects.create(sub_team=self.live, event=self.evening, event_date=extra, is_active=False)
        source = next(row for row in self.client.get(self.api).json()['sources'] if row['event_date_id'] == extra.pk)
        self.assertEqual(self.post('add_source', source_key=source['key']).status_code, 200)
        item = next(row for row in self.data['occurrences'] if row['source_key'] == source['key'])
        self.assertEqual([(team['team_id'], team['member_count']) for team in item['teams']], [(self.team.pk, 4)])

    def test_repeated_agenda_import_is_idempotent_after_local_time_edit(self):
        item = self.first_evening(); initial_count = len(self.data['occurrences'])
        self.assertEqual(self.post('save_occurrence', **self.edit(item, time='18:30')).status_code, 200)
        self.post('add_source', source_key=item['source_key'])
        self.assertEqual(len(self.data['occurrences']), initial_count)
        self.assertEqual(next(row for row in self.data['occurrences'] if row['id'] == item['id'])['time'], '18:30')

    def test_local_extra_and_repeat_click_do_not_create_events_or_duplicates(self):
        count = Event.objects.count()
        for _ in range(2):
            self.assertEqual(self.post('add_manual', name='Culto extraordinário', date='2026-11-02', time='19:45', notes='Somente neste mês').status_code, 200)
        self.assertEqual(len([row for row in self.data['occurrences'] if row['name'] == 'Culto extraordinário']), 1)
        self.assertEqual(Event.objects.count(), count)
        for date in ('2026-12-01', 'invalida'):
            self.assertEqual(self.post('add_manual', name='Inválido', date=date, time='20:00').status_code, 400)

    def test_extra_occurrence_can_reference_existing_agenda_event_and_load_its_teams(self):
        count = Event.objects.count()
        response = self.post('add_manual', name='Culto extraordinário', date='2026-11-02', time='20:00', event_id=self.evening.pk)
        self.assertEqual(response.status_code, 200)
        item = next(row for row in self.data['occurrences'] if row['name'] == 'Culto extraordinário')
        occurrence = ScheduleOccurrence.objects.get(pk=item['id'])
        self.assertEqual(occurrence.source_event_id, self.evening.pk)
        self.assertEqual(len(item['teams']), 2)
        self.assertEqual(Event.objects.count(), count)

    def test_local_team_changes_do_not_change_recurring_defaults(self):
        item = self.first_evening()
        teams = [{'team_id': self.team.pk, 'member_count': 4, 'activity_ids': [self.photo.pk], 'notes': 'Levar equipamento'}]
        response = self.post('save_occurrence', **self.edit(item, teams=teams, notes='Culto especial'))
        self.assertEqual(response.status_code, 200)
        self.service.refresh_from_db(); self.assertEqual(self.service.member_count, 2)
        self.assertEqual(self.service.activities.count(), 2)
        changed = next(row for row in self.data['occurrences'] if row['id'] == item['id'])
        self.assertEqual(changed['teams'][0]['member_count'], 4)
        self.assertEqual(changed['teams'][0]['notes'], 'Levar equipamento')
        other = next(row for row in self.data['occurrences'] if row['source_key'].startswith('regular:sunday_19@') and row['id'] != item['id'])
        self.assertEqual(len(other['teams']), 2)
        self.assertEqual(other['notes'], '')

    def test_batch_changes_only_composition_preserving_dates_times_and_other_occurrences(self):
        first, second, third = [row for row in self.data['occurrences'] if row['source_key'].startswith('regular:sunday_19@')][:3]
        teams = [{'team_id': self.team.pk, 'member_count': 5, 'activity_ids': [self.stories.pk], 'notes': 'Em conjunto'}]
        self.assertEqual(self.post('save_occurrence', **self.edit(first, time='18:00', teams=teams, apply_to=[second['id']])).status_code, 200)
        updated = {row['id']: row for row in self.data['occurrences']}
        self.assertEqual(updated[second['id']]['date'], second['date'])
        self.assertEqual(updated[second['id']]['time'], second['time'])
        self.assertEqual(updated[second['id']]['teams'][0]['member_count'], 5)
        self.assertEqual(updated[third['id']], third)

    def test_invalid_team_activity_or_batch_id_rolls_back_every_change(self):
        item = self.first_evening(); original = self.data['occurrences']
        bad_cases = [
            {'teams': [{'team_id': self.team.pk, 'member_count': 2, 'activity_ids': [self.live_activity.pk]}]},
            {'teams': [{'team_id': self.team.pk, 'member_count': 0, 'activity_ids': []}]},
            {'apply_to': [999999]},
        ]
        for bad in bad_cases:
            response = self.post('save_occurrence', **self.edit(item, time='17:00', **bad))
            self.assertIn(response.status_code, (400, 404))
            self.assertEqual(self.client.get(self.api).json()['occurrences'], original)

    def test_optimistic_concurrency_rejects_stale_write_without_overwrite(self):
        token = self.data['version']; item = self.first_evening()
        self.assertEqual(self.post('save_occurrence', **self.edit(item, time='18:00')).status_code, 200)
        response = self.post('save_occurrence', version=token, **self.edit(item, time='17:00'))
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json()['conflict'])
        self.assertEqual(ScheduleOccurrence.objects.get(pk=item['id']).start_time, datetime.time(18))

    def test_administrative_metadata_or_day_edit_invalidates_version(self):
        original = self.data['version']
        self.state.schedule.title = 'Título ajustado'; self.state.schedule.save()
        self.assertEqual(self.post('navigate', version=original, step=2).status_code, 409)
        self.data = self.client.get(self.api).json(); original = self.data['version']
        day = self.state.schedule.days.first(); day.notes = 'Edição administrativa'; day.save()
        self.assertEqual(self.post('navigate', version=original, step=2).status_code, 409)

    def test_remove_and_restore_does_not_delete_agenda_or_duplicate_occurrence(self):
        item = self.first_evening(); event_id = ScheduleOccurrence.objects.get(pk=item['id']).source_event_id
        self.assertEqual(self.post('remove_occurrence', occurrence_id=item['id']).status_code, 200)
        self.assertIsNone(event_id)
        self.assertFalse(any(row['source_key'] == item['source_key'] for row in self.data['occurrences']))

    def test_restoring_an_administratively_deleted_day_does_not_restore_other_occurrences(self):
        item = self.first_evening()
        day = ScheduleOccurrence.objects.get(pk=item['id']).schedule_day
        removed_ids = list(day.occurrences.values_list('pk', flat=True))
        day.delete()
        self.data = self.client.get(self.api).json()
        response = self.post('add_manual', name='Novo culto', date=item['date'], time='18:45')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(ScheduleOccurrence.objects.filter(pk__in=removed_ids).exists())

    def test_discard_requires_confirmation_and_only_discards_own_canonical_draft(self):
        self.assertEqual(self.post('discard').status_code, 400)
        self.assertEqual(self.post('discard', confirmed=True).status_code, 200)
        self.assertFalse(MonthlySchedule.objects.filter(pk=self.state.schedule_id).exists())
        old = ScheduleAssistantState.all_objects.get(pk=self.state.pk)
        self.assertEqual(old.status, 'discarded'); self.assertIsNotNone(old.deleted)
        self.assertTrue(Event.objects.filter(pk=self.evening.pk).exists())
        fresh, created = start_draft(self.ministry, datetime.date(2026, 11, 1), self.user)
        self.assertTrue(created); self.assertNotEqual(fresh.schedule_id, self.state.schedule_id)

    def test_navigation_persists_but_never_generates_or_publishes(self):
        for step in (2, 3, 1):
            self.assertEqual(self.post('navigate', step=step).json()['current_step'], step)
            self.assertEqual(self.client.get(self.api).json()['current_step'], step)
        self.assertEqual(self.post('publish').status_code, 400)
        self.assertEqual(self.post('generate').status_code, 400)
        self.state.schedule.refresh_from_db(); self.assertFalse(self.state.schedule.is_published)
        self.assertEqual(DivisionMember.objects.count(), 0)
        self.assertFalse(any(day.members.exists() for day in self.state.schedule.days.all()))

    def test_availability_review_preserves_unaffected_data_and_team_identity(self):
        self.state.availability_revision = self.state.revision; self.state.save()
        self.data = self.client.get(self.api).json()
        first, second = [row for row in self.data['occurrences'] if row['source_key'].startswith('regular:sunday_19@')][:2]
        original_team_ids = [team['id'] for team in first['teams']]
        self.post('save_occurrence', **self.edit(first, time='18:00'))
        updated = {row['id']: row for row in self.data['occurrences']}
        self.assertTrue(updated[first['id']]['availability_review_required'])
        self.assertEqual([team['id'] for team in updated[first['id']]['teams']], original_team_ids)
        self.assertEqual(updated[second['id']], second)
        self.assertEqual(self.data['availability_review'][0]['occurrence_id'], first['id'])
        self.state.refresh_from_db(); self.assertIsNotNone(self.state.availability_revision)

    def test_draft_hidden_from_member_queries_and_direct_access_but_legacy_visible(self):
        legacy = MonthlySchedule.objects.create(ministry=self.ministry, title='Legada', month=11, year=2026)
        self.assertEqual(list(member_visible_schedules(MonthlySchedule.objects.all()).values_list('pk', flat=True)), [legacy.pk])
        reader = User.objects.create_user('assistant-reader@example.test')
        member = Member.objects.create(user=reader, name='Integrante')
        MinistryMembership.objects.create(member=member, ministry=self.ministry)
        self.client.force_login(reader)
        self.assertEqual(self.client.get(reverse('member_schedule_detail', args=[self.state.schedule_id])).status_code, 404)
        self.assertEqual(self.client.get(reverse('schedule_print', args=[self.state.schedule_id])).status_code, 404)
        self.assertEqual(self.client.get(reverse('member_schedule_detail', args=[legacy.pk])).status_code, 200)
        self.assertEqual(self.client.get(self.api).status_code, 403)

    def test_permissions_staff_without_member_and_inactive_leader(self):
        self.client.logout(); self.assertEqual(self.client.get(self.api).status_code, 302)
        staff = User.objects.create_user('assistant-staff@example.test', is_staff=True)
        self.client.force_login(staff); self.assertEqual(self.client.get(self.api).status_code, 200)
        self.client.force_login(self.user)
        membership = MinistryMembership.objects.get(member=self.member, ministry=self.ministry)
        membership.is_active = False; membership.save()
        self.assertEqual(self.client.get(self.api).status_code, 403)

    def test_other_ministry_draft_and_team_are_rejected(self):
        other = Ministry.objects.create(name='Louvor')
        foreign_team = MediaSubTeam.objects.create(ministry=other, name='Outra equipe')
        item = self.first_evening()
        response = self.post('save_occurrence', **self.edit(item, teams=[{'team_id': foreign_team.pk, 'member_count': 2, 'activity_ids': []}]))
        self.assertEqual(response.status_code, 400)
        foreign, _ = start_draft(other, datetime.date(2026, 11, 1), self.user)
        self.assertEqual(self.client.get(reverse('media_schedule_assistant_api', args=[foreign.pk])).status_code, 404)

    def test_csrf_invalid_json_invalid_step_and_get_are_safe(self):
        csrf = Client(enforce_csrf_checks=True); csrf.force_login(self.user)
        self.assertEqual(csrf.post(self.api, '{}', content_type='application/json').status_code, 403)
        for raw in ('{', '[]', 'null'):
            self.assertEqual(self.client.post(self.api, raw, content_type='application/json').status_code, 400)
        self.assertEqual(self.post('navigate', step=4).status_code, 400)
        self.assertEqual(self.client.get(reverse('media_schedule_assistant_start')).status_code, 405)
        self.assertEqual(ScheduleAssistantState.objects.count(), 1)

    def test_pages_render_safe_data_and_secretariat_links(self):
        for route in ('media_secretariat', 'media_schedule_list'):
            response = self.client.get(reverse(route)); self.assertEqual(response.status_code, 200)
        self.post('add_manual', name='<img src=x onerror=alert(1)>', date='2026-11-03', time='18:00')
        response = self.client.get(reverse('media_schedule_assistant', args=[self.state.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Planejamento mensal')
        self.assertContains(response, 'schedule_assistant.js')
        self.assertNotContains(response, '<img src=x onerror=alert(1)>')
        self.assertContains(self.client.get(reverse('media_schedule_list')), 'Continuar rascunho')

    def test_list_filters_search_period_and_draft(self):
        MonthlySchedule.objects.create(ministry=self.ministry, title='Gravação semanal', month=10, year=2026)
        url = reverse('media_schedule_list')
        response = self.client.get(url, {'q': 'Gravação', 'month': '10', 'year': '2026', 'status': 'registered'})
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        self.assertContains(response, 'Gravação semanal')
        self.assertNotContains(response, 'Continuar rascunho')
        response = self.client.get(url, {'status': 'draft'})
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        self.assertContains(response, 'Continuar rascunho')

    def test_list_has_search_and_creation_dialog_without_floating_navigation(self):
        response = self.client.get(reverse('media_schedule_list'), {'q': 'sem resultado'})
        self.assertContains(response, 'Nenhuma escala encontrada')
        self.assertContains(response, 'schedule-create-dialog')
        self.assertContains(response, 'Pesquisar e filtrar escalas')
        self.assertNotContains(response, 'member-bottom-nav')


class AgendaExpansionTests(TestCase):
    def event(self, date, pattern):
        return Event.objects.create(title='Culto', event_date=date, event_time='19:00',
                                    display_start=date, display_end='2030-12-31',
                                    is_recurring=True, recurrence_pattern=pattern)

    def test_biweekly_uses_anchor_not_every_week(self):
        self.event('2026-11-01', 'biweekly')
        rows, _ = agenda_occurrences(datetime.date(2026, 11, 1))
        self.assertEqual([row['date'] for row in rows], ['2026-11-01', '2026-11-15', '2026-11-29'])

    def test_monthly_skips_missing_day_and_honors_leap_year(self):
        self.event('2026-01-31', 'monthly')
        self.assertEqual(agenda_occurrences(datetime.date(2026, 2, 1))[0], [])
        self.event('2026-01-29', 'monthly')
        rows, _ = agenda_occurrences(datetime.date(2028, 2, 1))
        self.assertEqual([row['date'] for row in rows], ['2028-02-29'])

    def test_custom_and_missing_time_are_warnings_not_guessed_occurrences(self):
        self.event('2026-01-01', 'custom')
        event = self.event('2026-01-04', 'weekly'); event.event_time = None; event.save()
        rows, warnings = agenda_occurrences(datetime.date(2026, 11, 1))
        self.assertEqual(rows, []); self.assertEqual(len(warnings), 2)

    def test_reference_date_is_not_a_start_bound_and_identical_extra_is_not_duplicated(self):
        event = self.event('2026-11-15', 'weekly')
        EventDate.objects.create(event=event, event_date='2026-11-15', event_time='19:00')
        rows, _ = agenda_occurrences(datetime.date(2026, 11, 1))
        self.assertEqual([row['date'] for row in rows], ['2026-11-01', '2026-11-08', '2026-11-15', '2026-11-22', '2026-11-29'])


class ScheduleAssistantMigrationTests(TransactionTestCase):
    def test_legacy_schedules_and_days_survive_additive_migration(self):
        executor = MigrationExecutor(connection); latest = executor.loader.graph.leaf_nodes()
        previous = ('website', '0120_media_team_configuration')
        try:
            executor.migrate([previous]); apps = executor.loader.project_state([previous]).apps
            ministry, _ = apps.get_model('website', 'Ministry').objects.get_or_create(code='midia_externa', defaults={'name': 'Mídia Externa'})
            schedule = apps.get_model('website', 'MonthlySchedule').objects.create(ministry_id=ministry.pk, title='Histórica', month=9, year=2026)
            day = apps.get_model('website', 'ScheduleDay').objects.create(schedule_id=schedule.pk, date='2026-09-06')
            MigrationExecutor(connection).migrate(latest)
            self.assertTrue(MonthlySchedule.objects.filter(pk=schedule.pk).exists())
            self.assertTrue(ScheduleDay.objects.filter(pk=day.pk, schedule_id=schedule.pk).exists())
            self.assertTrue(member_visible_schedules(MonthlySchedule.objects.filter(pk=schedule.pk)).exists())
            self.assertFalse(ScheduleAssistantState.objects.filter(schedule_id=schedule.pk).exists())
        finally:
            MigrationExecutor(connection).migrate(latest)


class ScheduleAssistantConcurrencyTests(TransactionTestCase):
    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Verificação de bloqueios requer PostgreSQL.')
        self.ministry, _ = Ministry.objects.get_or_create(code='midia_externa', defaults={'name': 'Mídia Externa'})
        self.user = User.objects.create_user('concurrent-assistant@example.test', is_staff=True)

    def run_concurrently(self, operation):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import connections
        barrier = Barrier(2)
        def worker(index):
            try:
                barrier.wait(timeout=10)
                return operation(index)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(worker, range(2)))

    def test_concurrent_start_creates_only_one_schedule(self):
        def create(index):
            state, created = start_draft(self.ministry, datetime.date(2026, 11, 1), self.user)
            return state.pk, created
        results = self.run_concurrently(create)
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(sorted(created for _, created in results), [False, True])
        self.assertEqual(MonthlySchedule.objects.count(), 1)

    def test_concurrent_mutations_cannot_overwrite_silently(self):
        from website.services.schedule_assistant import mutate, PlanningConflict
        state, _ = start_draft(self.ministry, datetime.date(2026, 11, 1), self.user)
        token = serialize(state)['version']
        def update(index):
            try:
                mutate(state.pk, self.ministry, self.user, {'action': 'navigate', 'step': index + 2, 'version': token})
                return 200
            except PlanningConflict:
                return 409
        self.assertEqual(sorted(self.run_concurrently(update)), [200, 409])
        state.refresh_from_db(); self.assertEqual(state.revision, 2)
