import datetime

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TestCase, TransactionTestCase
from django.urls import reverse

from website.forms.media_organization import MediaSubTeamMembershipForm, MediaTeamServiceForm
from website.models import (
    Event, EventDate, Member, Ministry, MinistryMembership, User,
    MediaSubTeam, MediaSubTeamMembership, MediaTeamActivity, MediaTeamService,
    MediaTeamServiceActivity, MonthlySchedule, ScheduleDay,
)


class MediaTeamConfigurationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ministry, _ = Ministry.objects.get_or_create(code='midia_externa', defaults={'name': 'Mídia Externa'})
        cls.other = Ministry.objects.create(name='Outro ministério')
        cls.user = User.objects.create_user('teams-leader@example.test', password='test')
        cls.member = Member.objects.create(name='Líder', user=cls.user)
        MinistryMembership.objects.create(member=cls.member, ministry=cls.ministry, role='leader')
        cls.team = MediaSubTeam.objects.create(name='Cobertura', ministry=cls.ministry)
        cls.second_team = MediaSubTeam.objects.create(name='Live', ministry=cls.ministry)
        cls.other_team = MediaSubTeam.objects.create(name='Equipe externa', ministry=cls.other)
        cls.membership = MediaSubTeamMembership.objects.create(sub_team=cls.team, member=cls.member)
        cls.activity = MediaTeamActivity.objects.create(sub_team=cls.team, name='Stories', sort_order=2)
        cls.event = Event.objects.create(
            title='Culto de domingo', description='Culto', event_date=datetime.date(2026, 11, 1),
            event_time=datetime.time(19), display_start='2026-01-01', display_end='2026-12-31',
            is_recurring=True, recurrence_pattern='weekly',
        )

    def setUp(self):
        self.client.force_login(self.user)

    def url(self, name, **kwargs):
        return reverse(name, kwargs={'ministry_id': self.ministry.pk, 'pk': self.team.pk, **kwargs})

    def data(self, **overrides):
        return {'regular_services': ['sunday_19'], 'member_count': 2, 'is_active': 'on', **overrides}

    def create_service(self):
        form = MediaTeamServiceForm(self.data(), sub_team=self.team)
        self.assertTrue(form.is_valid(), form.errors)
        return form.save()

    def test_experience_is_pending_and_specific_to_membership(self):
        second = MediaSubTeamMembership.objects.create(sub_team=self.second_team, member=self.member)
        self.assertEqual(self.membership.experience_level, 'pending')
        response = self.client.post(self.url('media_team_experience_edit', membership_id=self.membership.pk), {'experience_level': 'mentor'})
        self.assertEqual(response.status_code, 302)
        self.membership.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(self.membership.experience_level, 'mentor')
        self.assertEqual(second.experience_level, 'pending')
        self.assertEqual(self.membership.history.first().experience_level, 'mentor')
        self.assertEqual(Member.objects.filter(pk=self.member.pk).count(), 1)

    def test_invalid_experience_and_get_do_not_update(self):
        url = self.url('media_team_experience_edit', membership_id=self.membership.pk)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url, {'experience_level': 'invalid'})
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.experience_level, 'pending')

    def test_membership_form_scoped_and_preserves_omitted_classification(self):
        self.assertNotIn('experience_level', MediaSubTeamMembershipForm(sub_team=self.other_team).fields)
        self.membership.experience_level = 'mentor'
        self.membership.save()
        form = MediaSubTeamMembershipForm({'member': self.member.pk, 'is_active': 'on'}, instance=self.membership, sub_team=self.team)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().experience_level, 'mentor')

    def test_activities_create_edit_order_and_deactivate(self):
        self.client.post(self.url('media_team_activity_create'), {'name': 'Fotografia', 'sort_order': 1, 'is_active': 'on'})
        activity = self.team.activities.get(name='Fotografia')
        self.assertEqual(list(self.team.activities.values_list('name', flat=True)), ['Fotografia', 'Stories'])
        response = self.client.post(self.url('media_team_activity_edit', item_id=activity.pk), {'name': 'Fotos', 'description': 'Cobertura', 'sort_order': 3})
        self.assertEqual(response.status_code, 302)
        activity.refresh_from_db()
        self.assertFalse(activity.is_active)
        self.assertIsNone(activity.deleted)
        self.assertEqual(activity.history.count(), 2)

    def test_headcount_is_for_team_and_defaults_to_two(self):
        photo = MediaTeamActivity.objects.create(sub_team=self.team, name='Fotografia')
        self.client.post(self.url('media_team_service_create'), self.data())
        service = self.team.service_defaults.get()
        self.assertEqual(service.member_count, 2)
        self.assertEqual(service.activities.count(), 2)
        self.assertEqual(service.regular_service, 'sunday_19')
        self.assertEqual(service.service_time, datetime.time(19))
        self.assertEqual(MonthlySchedule.objects.count(), 0)
        self.assertEqual(MediaTeamService(sub_team=self.team, regular_service='sunday_19').member_count, 2)

    def test_multiple_regular_services_are_created_without_agenda_events(self):
        form = MediaTeamServiceForm(
            self.data(regular_services=['sunday_09', 'wednesday_20']), sub_team=self.team,
        )
        self.assertTrue(form.is_valid(), form.errors)
        services = form.save()
        self.assertEqual(len(services), 2)
        self.assertEqual(
            set(self.team.service_defaults.values_list('regular_service', flat=True)),
            {'sunday_09', 'wednesday_20'},
        )
        self.assertTrue(all(service.event_id is None for service in services))

    def test_regular_services_use_the_standard_options_instead_of_agenda_events(self):
        response = self.client.get(self.url('media_team_service_create'), HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertContains(response, 'Domingo · 09:00')
        self.assertContains(response, 'Quarta-feira · 20:00')
        self.assertContains(response, 'type="checkbox"', html=False)
        self.assertNotContains(response, self.event.title)
        self.assertNotContains(response, 'Atividades previstas')

    def test_specific_times_and_additional_dates(self):
        first = self.create_service()
        form = MediaTeamServiceForm(self.data(regular_services=['wednesday_20']), sub_team=self.team)
        self.assertTrue(form.is_valid(), form.errors)
        second = form.save()
        self.assertNotEqual(first.service_time, second.service_time)
        self.assertEqual(second.regular_service, 'wednesday_20')
        self.assertIn('20:00', second.service_label)

    def test_duplicate_service_rejected_but_different_teams_allowed(self):
        self.create_service()
        self.assertFalse(MediaTeamServiceForm(self.data(), sub_team=self.team).is_valid())
        MediaTeamService.objects.create(sub_team=self.second_team, regular_service='sunday_19')

    def test_invalid_inputs_and_cross_team_links_rejected(self):
        event = Event.objects.create(title='Sem horário', event_date='2026-11-01', display_start='2026-11-01', display_end='2026-11-01')
        for overrides in ({'regular_services': []}, {'member_count': 0}, {'member_count': -1}):
            self.assertFalse(MediaTeamServiceForm(self.data(**overrides), sub_team=self.team).is_valid())
        foreign = MediaTeamActivity.objects.create(sub_team=self.second_team, name='Live')
        with self.assertRaises(ValidationError):
            MediaTeamService.objects.create(sub_team=self.team, event=event)
        service = self.create_service()
        with self.assertRaises(ValidationError):
            MediaTeamServiceActivity.objects.create(service=service, activity=foreign)
        extra = EventDate.objects.create(event=event, event_date='2026-11-01', event_time='20:00')
        with self.assertRaises(ValidationError):
            MediaTeamService.objects.create(sub_team=self.team, event=self.event, event_date=extra)

    def test_service_edit_preserves_id_and_activity_history(self):
        service = self.create_service()
        photo = MediaTeamActivity.objects.create(sub_team=self.team, name='Fotografia')
        response = self.client.post(self.url('media_team_service_edit', item_id=service.pk), self.data(member_count=3))
        self.assertEqual(response.status_code, 302)
        service.refresh_from_db()
        self.assertEqual(service.member_count, 3)
        self.assertEqual(list(service.activities.values_list('pk', flat=True)), [self.activity.pk])
        self.assertEqual(service.activity_links.count(), 1)
        self.assertEqual(MediaTeamServiceActivity.all_objects.filter(service=service).count(), 1)
        self.assertEqual(service.history.count(), 2)

    def test_inactive_activity_retained_but_not_added_elsewhere(self):
        service = self.create_service()
        self.activity.is_active = False
        self.activity.save()
        form = MediaTeamServiceForm(self.data(), instance=service, sub_team=self.team)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertFalse(MediaTeamServiceForm(self.data(), sub_team=self.team).is_valid())
        self.assertEqual(service.snapshot()['activities'], [])

    def test_existing_schedule_and_snapshot_preserved_after_config_changes(self):
        service = self.create_service()
        schedule = MonthlySchedule.objects.create(ministry=self.ministry, title='Histórica', month=10, year=2026)
        day = ScheduleDay.objects.create(schedule=schedule, date='2026-10-25', description='Stories e fotografia')
        day.members.add(self.member)
        snapshot = service.snapshot()
        self.activity.name = 'Outro nome'
        self.activity.is_active = False
        self.activity.save()
        service.member_count = 4
        service.is_active = False
        service.save()
        day.refresh_from_db()
        self.assertEqual(day.description, 'Stories e fotografia')
        self.assertEqual(list(day.members.values_list('pk', flat=True)), [self.member.pk])
        self.assertEqual(snapshot['member_count'], 2)
        self.assertEqual(snapshot['activities'][0]['name'], 'Stories')
        self.assertIn('19:00', snapshot['label'])

    def test_pages_render_with_classification_and_service_time(self):
        self.create_service()
        for name in ('media_team_activity_create', 'media_team_service_create', 'ministry_team_detail'):
            self.assertEqual(self.client.get(self.url(name)).status_code, 200)
        response = self.client.get(self.url('ministry_team_detail'))
        for text in ('Pendente de classificação', '19:00', 'Atividades da equipe', 'Stories'):
            self.assertContains(response, text)

    def test_activity_forms_can_be_loaded_and_saved_in_the_team_modal(self):
        create_url = self.url('media_team_activity_create')
        response = self.client.get(create_url, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'team-default-modal-form')
        self.assertNotContains(response, 'Secretaria da Mídia')
        self.assertNotContains(response, 'Ordem de exibição')
        self.assertNotContains(response, 'Ativa')

        response = self.client.post(
            create_url,
            {'name': 'Fotografia'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {
            'success': True,
            'redirect_url': reverse('ministry_team_detail', kwargs={
                'ministry_id': self.ministry.pk, 'pk': self.team.pk,
            }),
        })
        self.assertTrue(self.team.activities.filter(name='Fotografia').exists())
        self.assertEqual(self.team.activities.get(name='Fotografia').sort_order, 3)

    def test_modal_validation_returns_the_form_fragment(self):
        response = self.client.post(
            self.url('media_team_activity_create'),
            {'name': '', 'sort_order': 0},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'team-default-modal-form', status_code=400)
        self.assertContains(response, 'text-red-600', status_code=400)

    def test_permissions_and_ministry_scope(self):
        url = self.url('media_team_service_create')
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)
        reader = User.objects.create_user('teams-reader@example.test')
        member = Member.objects.create(name='Integrante', user=reader)
        MinistryMembership.objects.create(ministry=self.ministry, member=member)
        self.client.force_login(reader)
        self.assertEqual(self.client.post(url, self.data()).status_code, 403)
        self.client.force_login(self.user)
        wrong_team_url = reverse('media_team_service_create', kwargs={'ministry_id': self.ministry.pk, 'pk': self.other_team.pk})
        self.assertEqual(self.client.post(wrong_team_url, self.data()).status_code, 404)
        other_url = reverse('media_team_activity_create', kwargs={'ministry_id': self.other.pk, 'pk': self.other_team.pk})
        self.assertEqual(self.client.post(other_url, {'name': 'Inválida', 'sort_order': 0}).status_code, 403)
        self.assertEqual(MediaTeamActivity.objects.filter(sub_team=self.other_team).count(), 0)
        with self.assertRaises(ValidationError):
            MediaTeamActivity.objects.create(sub_team=self.other_team, name='Inválida')

    def test_foreign_ids_cannot_be_edited(self):
        foreign = MediaTeamActivity.objects.create(sub_team=self.second_team, name='Live')
        response = self.client.post(self.url('media_team_activity_edit', item_id=foreign.pk), {'name': 'Inválida', 'sort_order': 0})
        self.assertEqual(response.status_code, 404)

    def test_rejected_edit_preserves_service_and_links(self):
        service = self.create_service()
        response = self.client.post(self.url('media_team_service_edit', item_id=service.pk), self.data(member_count=0, activities=[]))
        self.assertEqual(response.status_code, 200)
        service.refresh_from_db()
        self.assertEqual(service.member_count, 2)
        self.assertEqual(list(service.activities.values_list('pk', flat=True)), [self.activity.pk])

    def test_trainee_and_inactive_service_can_be_updated(self):
        self.client.post(self.url('media_team_experience_edit', membership_id=self.membership.pk), {'experience_level': 'trainee'})
        self.membership.refresh_from_db()
        self.assertEqual(self.membership.experience_level, 'trainee')
        service = self.create_service()
        self.client.post(self.url('media_team_service_edit', item_id=service.pk), self.data(is_active=''))
        service.refresh_from_db()
        self.assertFalse(service.is_active)
        self.assertIsNone(service.deleted)
        self.assertEqual(service.activities.count(), 1)

    def test_agenda_edit_preserves_unchanged_date_links(self):
        from website.services.event_dates import sync_event_dates
        extra = EventDate.objects.create(event=self.event, event_date=datetime.date(2026, 11, 8), event_time=datetime.time(10))
        service = MediaTeamService.objects.create(sub_team=self.team, event=self.event, event_date=extra)
        sync_event_dates(self.event, [{'event_date': '2026-11-08', 'event_time': '10:00'}])
        service.refresh_from_db()
        self.assertEqual(service.event_date_id, extra.pk)
        self.assertIsNone(EventDate.objects.get(pk=extra.pk).deleted)
        sync_event_dates(self.event, [])
        self.assertFalse(MediaTeamService.objects.filter(pk=service.pk).exists())
        self.assertTrue(MediaTeamService.all_objects.filter(pk=service.pk).exists())

    def test_agenda_removal_keeps_existing_deletion_flow(self):
        from safedelete.models import HARD_DELETE
        service = MediaTeamService.objects.create(sub_team=self.team, event=self.event)
        self.event.delete()
        self.assertFalse(MediaTeamService.objects.filter(pk=service.pk).exists())
        self.assertTrue(MediaTeamService.all_objects.filter(pk=service.pk).exists())
        self.event.delete(force_policy=HARD_DELETE)
        self.assertFalse(MediaTeamService.all_objects.filter(pk=service.pk).exists())

    def test_staff_without_member_and_csrf(self):
        staff = User.objects.create_user('teams-staff@example.test', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(self.url('media_team_service_create')).status_code, 200)
        client = Client(enforce_csrf_checks=True)
        client.force_login(staff)
        self.assertEqual(client.post(self.url('media_team_experience_edit', membership_id=self.membership.pk), {'experience_level': 'mentor'}).status_code, 403)


class MediaTeamConfigurationMigrationTests(TransactionTestCase):
    def test_existing_memberships_are_preserved_and_pending(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        previous = ('website', '0119_projectpreviewartifact')
        try:
            executor.migrate([previous])
            apps = executor.loader.project_state([previous]).apps
            ministry, _ = apps.get_model('website', 'Ministry').objects.get_or_create(code='midia_externa', defaults={'name': 'Mídia Externa'})
            member = apps.get_model('website', 'Member').objects.create(name='Integrante antigo')
            team = apps.get_model('website', 'MediaSubTeam').objects.create(name='Equipe antiga', ministry_id=ministry.pk)
            membership = apps.get_model('website', 'MediaSubTeamMembership').objects.create(sub_team_id=team.pk, member_id=member.pk)
            MigrationExecutor(connection).migrate(latest)
            current = MediaSubTeamMembership.objects.get(pk=membership.pk)
            self.assertEqual(current.member_id, member.pk)
            self.assertEqual(current.sub_team_id, team.pk)
            self.assertTrue(current.is_active)
            self.assertEqual(current.experience_level, 'pending')
        finally:
            MigrationExecutor(connection).migrate(latest)
