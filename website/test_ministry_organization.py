from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from website.forms.media_organization import MediaSubTeamMembershipForm
from website.forms.ministry_manual import MinistryManualForm
from website.models import (
    Event, Member, Ministry, MinistryManual, MinistryMembership, MediaContent,
    MediaSubTeam, MediaSubTeamMembership, MediaTask, Team, User,
)
from website.services.demands_hub import build_free_detail, sync_content_assignments


class MinistryOrganizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ministry, _ = Ministry.objects.get_or_create(
            code='midia_externa',
            defaults={'name': 'Ministry Org Test Media'},
        )
        cls.other = Ministry.objects.create(name='Louvor Org Test')
        cls.user = User.objects.create_user('leader@example.test', password='test')
        cls.member = Member.objects.create(name='Líder', user=cls.user)
        cls.membership = MinistryMembership.objects.create(ministry=cls.ministry, member=cls.member, role='leader')
        cls.reader_user = User.objects.create_user('reader@example.test', password='test')
        cls.reader = Member.objects.create(name='Participante', user=cls.reader_user)
        MinistryMembership.objects.create(ministry=cls.ministry, member=cls.reader)
        cls.outsider_user = User.objects.create_user('outsider@example.test', password='test')
        cls.outsider = Member.objects.create(name='Outro ministério', user=cls.outsider_user)
        MinistryMembership.objects.create(ministry=cls.other, member=cls.outsider, role='leader')
        cls.team = MediaSubTeam.objects.create(name='Recepção', ministry=cls.ministry)
        cls.other_team = MediaSubTeam.objects.create(name='Recepção', ministry=cls.other)
        cls.participation = MediaSubTeamMembership.objects.create(sub_team=cls.team, member=cls.member)
        cls.manual = MinistryManual.objects.create(
            title='Receber visitantes', content='<h2>Preparação</h2><p>Receba com atenção.</p>',
            ministry=cls.ministry, created_by=cls.user,
        )

    def url(self, name, **kwargs):
        return reverse(name, kwargs={'ministry_id': self.ministry.pk, **kwargs})

    def test_team_names_are_unique_within_ministry(self):
        self.assertEqual(MediaSubTeam.objects.filter(name='Recepção').count(), 2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MediaSubTeam.objects.create(name='Recepção', ministry=self.ministry)

    def test_team_must_have_ministry(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            MediaSubTeam.objects.create(name='Sem ministério')

    def test_team_cannot_move_ministries(self):
        self.team.ministry = self.other
        with self.assertRaises(ValidationError):
            self.team.save()

    def test_team_leader_must_belong_to_ministry(self):
        self.team.leader = self.outsider
        with self.assertRaises(ValidationError):
            self.team.save()

    def test_member_can_join_multiple_teams(self):
        second = MediaSubTeam.objects.create(name='Apoio', ministry=self.ministry)
        MediaSubTeamMembership.objects.create(sub_team=second, member=self.member)
        self.assertEqual(self.member.media_subteam_memberships.count(), 2)

    def test_member_from_other_ministry_rejected_by_model(self):
        with self.assertRaises(ValidationError):
            MediaSubTeamMembership.objects.create(sub_team=self.team, member=self.outsider)

    def test_member_from_other_ministry_rejected_by_form(self):
        form = MediaSubTeamMembershipForm({'member': self.outsider.pk, 'is_active': 'on'}, sub_team=self.team)
        self.assertFalse(form.is_valid())
        self.assertIn('member', form.errors)

    def test_departure_deactivates_team_memberships_and_clears_leadership(self):
        self.team.leader = self.member
        self.team.save()
        self.membership.is_active = False
        self.membership.save()
        self.participation.refresh_from_db()
        self.team.refresh_from_db()
        self.assertFalse(self.participation.is_active)
        self.assertIsNone(self.team.leader)
        self.assertIsNone(self.participation.deleted)

    def test_soft_delete_ministry_membership_cleans_teams(self):
        self.membership.delete()
        self.participation.refresh_from_db()
        self.assertFalse(self.participation.is_active)
        self.assertTrue(MinistryMembership.all_objects.filter(pk=self.membership.pk).exists())

    def test_rejoining_ministry_does_not_silently_rejoin_teams(self):
        self.membership.is_active = False
        self.membership.save()
        self.membership.is_active = True
        self.membership.save()
        self.participation.refresh_from_db()
        self.assertFalse(self.participation.is_active)

    def test_manual_with_ministry_only(self):
        self.assertIsNone(self.manual.sub_team_id)
        self.assertEqual(self.manual.ministry, self.ministry)

    def test_manual_with_team(self):
        self.manual.sub_team = self.team
        self.manual.save()
        self.assertIn(self.manual, self.team.manuals.all())

    def test_manual_cross_ministry_team_rejected(self):
        self.manual.sub_team = self.other_team
        with self.assertRaises(ValidationError):
            self.manual.save()

    def test_manual_html_is_sanitized(self):
        self.manual.content = '<h2>Guia</h2><script>alert(1)</script><a href="javascript:alert(1)" onclick="alert(2)">Link</a><p><strong>Seguro</strong></p>'
        self.manual.save()
        self.assertNotIn('<script', self.manual.safe_content)
        self.assertNotIn('javascript:', self.manual.safe_content)
        self.assertNotIn('onclick', self.manual.safe_content)
        self.assertIn('<strong>Seguro</strong>', self.manual.safe_content)

    def test_empty_manual_rejected(self):
        self.manual.content = '<p><br></p>'
        with self.assertRaises(ValidationError):
            self.manual.save()

    def test_manual_form_restricts_team_choices(self):
        form = MinistryManualForm(ministry=self.ministry)
        self.assertQuerySetEqual(form.fields['sub_team'].queryset, [self.team])

    def test_non_leader_member_cannot_access_organization(self):
        self.client.force_login(self.reader_user)
        for name, kwargs in [('ministry_team_list', {}), ('ministry_team_detail', {'pk': self.team.pk}), ('ministry_manual_detail', {'pk': self.manual.pk})]:
            self.assertEqual(self.client.get(self.url(name, **kwargs)).status_code, 403)
        self.assertEqual(self.client.get(self.url('ministry_team_create')).status_code, 403)
        self.assertEqual(self.client.post(self.url('ministry_manual_archive', pk=self.manual.pk)).status_code, 403)

    def test_other_ministry_leader_cannot_access(self):
        self.client.force_login(self.outsider_user)
        self.assertEqual(self.client.get(self.url('ministry_manual_detail', pk=self.manual.pk)).status_code, 403)
        self.assertEqual(self.client.post(self.url('ministry_team_create'), {'name': 'Ataque'}).status_code, 403)

    def test_objects_are_scoped_to_url_ministry(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url('ministry_team_detail', pk=self.other_team.pk)).status_code, 404)
        self.assertEqual(self.client.post(self.url('ministry_manual_create'), {
            'title': 'Guia', 'content': '<p>Texto</p>', 'sub_team': self.other_team.pk, 'is_active': 'on',
        }).status_code, 200)
        self.assertEqual(MinistryManual.objects.count(), 1)

    def test_staff_without_member_can_manage(self):
        staff = User.objects.create_user('staff@example.test', is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(self.url('ministry_team_create'))
        self.assertRedirects(response, f"{self.url('ministry_team_list')}?create=1")

    def test_anonymous_is_redirected_to_login(self):
        self.assertEqual(self.client.get(self.url('ministry_manual_list')).status_code, 302)

    def test_leader_creates_team(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url('ministry_team_create'), {'name': 'Apoio', 'is_active': 'on'})
        team = MediaSubTeam.objects.get(name='Apoio')
        self.assertEqual(team.ministry, self.ministry)
        self.assertRedirects(response, self.url('ministry_team_detail', pk=team.pk))

    def test_duplicate_team_name_shows_form_error(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url('ministry_team_create'), {'name': self.team.name, 'is_active': 'on'})
        self.assertContains(response, 'Já existe uma equipe')
        self.assertEqual(MediaSubTeam.objects.filter(ministry=self.ministry, name=self.team.name).count(), 1)

    def test_leader_creates_and_edits_manual(self):
        self.client.force_login(self.user)
        response = self.client.post(self.url('ministry_manual_create'), {
            'title': 'Novo guia', 'content': '<p>Olá</p>', 'sub_team': self.team.pk, 'is_active': 'on',
        })
        manual = MinistryManual.objects.get(title='Novo guia')
        self.assertEqual(manual.created_by, self.user)
        self.assertRedirects(response, self.url('ministry_manual_detail', pk=manual.pk))
        self.client.post(self.url('ministry_manual_edit', pk=manual.pk), {
            'title': 'Guia atualizado', 'content': '<ol><li>Etapa 1</li></ol>', 'is_active': 'on',
        })
        manual.refresh_from_db()
        self.assertEqual(manual.title, 'Guia atualizado')
        self.assertIsNone(manual.sub_team)
        self.assertEqual(manual.created_by, self.user)

    def test_soft_deleted_participation_can_be_restored(self):
        self.participation.delete()
        self.client.force_login(self.user)
        response = self.client.post(self.url('ministry_team_member_add', pk=self.team.pk), {'member': self.member.pk, 'is_active': 'on'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(MediaSubTeamMembership.objects.get(sub_team=self.team, member=self.member).pk, self.participation.pk)

    def test_add_member_from_teams_modal_redirects_to_list(self):
        self.client.force_login(self.user)
        prefix = f'add-member-{self.team.pk}'
        response = self.client.post(
            self.url('ministry_team_member_add', pk=self.team.pk),
            {
                'return_to': 'teams',
                f'{prefix}-is_active': 'on',
                f'{prefix}-member': self.reader.pk,
            },
        )
        self.assertRedirects(response, self.url('ministry_team_list'))
        participation = MediaSubTeamMembership.objects.get(sub_team=self.team, member=self.reader)
        self.assertTrue(participation.is_active)
        response = self.client.get(self.url('ministry_team_list'))
        self.assertContains(response, self.reader.name)

    def test_add_member_from_teams_modal_invalid_reopens_modal(self):
        self.client.force_login(self.user)
        prefix = f'add-member-{self.team.pk}'
        response = self.client.post(
            self.url('ministry_team_member_add', pk=self.team.pk),
            {'return_to': 'teams', f'{prefix}-is_active': 'on', f'{prefix}-member': ''},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'id="org-modal-add-member-{self.team.pk}"')
        self.assertContains(response, 'org-modal-add-member-' + str(self.team.pk) + '" class="demands-modal org-team-modal active"')

    def test_manual_filters_and_team_detail_use_same_records(self):
        self.manual.sub_team = self.team
        self.manual.save()
        self.client.force_login(self.user)
        response = self.client.get(self.url('ministry_manual_list'), {'team': self.team.pk, 'q': 'visitantes'})
        self.assertContains(response, self.manual.title)
        self.assertContains(self.client.get(self.url('ministry_team_detail', pk=self.team.pk)), self.manual.title)
        self.assertNotContains(self.client.get(self.url('ministry_manual_list'), {'q': 'inexistente'}), self.manual.title)

    def test_manual_filter_hidden_without_teams(self):
        MinistryManual.objects.filter(ministry=self.ministry).delete()
        MediaSubTeam.objects.filter(ministry=self.ministry).delete()
        self.client.force_login(self.user)
        response = self.client.get(reverse('ministry_manual_list', kwargs={'ministry_id': self.ministry.pk}))
        self.assertNotContains(response, 'id="manual-team"')
        self.assertContains(response, 'Nenhum manual encontrado')

    def test_non_media_ministry_member_cannot_access_organization(self):
        ministry = Ministry.objects.create(name='Sem equipes Org Test')
        MinistryMembership.objects.create(ministry=ministry, member=self.reader)
        self.client.force_login(self.reader_user)
        response = self.client.get(reverse('ministry_manual_list', kwargs={'ministry_id': ministry.pk}))
        self.assertEqual(response.status_code, 403)

    def test_archive_is_post_only_and_private_to_managers(self):
        self.client.force_login(self.user)
        url = self.url('ministry_manual_archive', pk=self.manual.pk)
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url)
        self.manual.refresh_from_db()
        self.assertFalse(self.manual.is_active)
        self.assertIsNone(self.manual.deleted)
        self.assertContains(self.client.get(self.url('ministry_manual_detail', pk=self.manual.pk)), 'Arquivado')
        self.client.force_login(self.reader_user)
        self.assertEqual(self.client.get(self.url('ministry_manual_detail', pk=self.manual.pk)).status_code, 403)

    def test_scale_teams_remain_compatible(self):
        team = Team.objects.create(name='Escala A', ministry=self.ministry)
        team.members.add(self.member)
        self.assertEqual(team.members.count(), 1)
        self.assertFalse(MediaSubTeam.objects.filter(name='Escala A').exists())

    def test_team_list_queries_do_not_grow_with_team_count(self):
        self.client.force_login(self.user)
        from django.test.utils import CaptureQueriesContext
        url = self.url('ministry_team_list')
        with CaptureQueriesContext(connection) as first:
            self.client.get(url)
        for number in range(8):
            MediaSubTeam.objects.create(name=f'Equipe {number}', ministry=self.ministry)
        with CaptureQueriesContext(connection) as second:
            self.client.get(url)
        self.assertEqual(len(first), len(second))

    def test_manual_markdown_formatting_and_preview_permissions(self):
        self.client.force_login(self.user)
        content = '## Preparação\n\n1. Abra o OBS.\n2. Verifique o áudio.\n\n**Pronto**\n\n> Confira tudo.'
        response = self.client.post(self.url('ministry_manual_preview'), {'content': content})
        self.assertEqual(response.status_code, 200)
        html = response.json()['html']
        self.assertIn('<h2>Preparação</h2>', html)
        self.assertIn('<ol>', html)
        self.assertIn('<strong>Pronto</strong>', html)
        self.assertIn('<blockquote>', html)
        self.client.force_login(self.reader_user)
        self.assertEqual(self.client.post(self.url('ministry_manual_preview'), {'content': content}).status_code, 403)

    def test_member_without_user_can_join_team(self):
        member = Member.objects.create(name='Sem conta')
        MinistryMembership.objects.create(ministry=self.ministry, member=member)
        MediaSubTeamMembership.objects.create(sub_team=self.team, member=member)
        self.assertTrue(self.team.memberships.filter(member=member).exists())

    def test_departure_does_not_change_other_ministry_participation(self):
        MinistryMembership.objects.create(ministry=self.other, member=self.member)
        other_participation = MediaSubTeamMembership.objects.create(sub_team=self.other_team, member=self.member)
        self.membership.delete()
        other_participation.refresh_from_db()
        self.assertTrue(other_participation.is_active)

    def test_removal_endpoint_preserves_ministry_membership_and_requires_post(self):
        staff = User.objects.create_user('remove@example.test', is_staff=True)
        self.client.force_login(staff)
        url = reverse('ministry_remove_member', args=[self.ministry.pk, self.member.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertTrue(MinistryMembership.deleted_objects.filter(pk=self.membership.pk).exists())
        self.participation.refresh_from_db()
        self.assertFalse(self.participation.is_active)



class MinistryDemandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ministry, _ = Ministry.objects.get_or_create(name='Mídia Externa', defaults={'code': 'midia_externa'})
        cls.user = User.objects.create_user('media@example.test', password='test')
        cls.member = Member.objects.create(name='Mídia', user=cls.user)
        cls.membership = MinistryMembership.objects.create(ministry=cls.ministry, member=cls.member, role='leader')
        cls.other_user = User.objects.create_user('other@example.test', password='test')
        cls.other_member = Member.objects.create(name='Outro', user=cls.other_user)
        MinistryMembership.objects.create(ministry=cls.ministry, member=cls.other_member)
        cls.team = MediaSubTeam.objects.create(name='Audiovisual', ministry=cls.ministry)
        MediaSubTeamMembership.objects.create(sub_team=cls.team, member=cls.member)
        cls.other_ministry = Ministry.objects.create(name='Outro ministério')
        cls.foreign_team = MediaSubTeam.objects.create(name='Equipe externa', ministry=cls.other_ministry)

    def payload(self, user_id=None, prefix='demand'):
        return {
            f'{prefix}-title': 'Transmissão', f'{prefix}-content_type': 'livestream',
            f'{prefix}-sub_team': self.team.pk,
            f'{prefix}-assignment_role': ['Operador'],
            f'{prefix}-assignment_user': [str(user_id or self.user.pk)],
            f'{prefix}-assignment_due_days': ['7'],
            f'{prefix}-assignment_due_relation': ['before'],
        }

    def test_demand_without_team_remains_supported(self):
        content = MediaContent.objects.create(title='Legado', content_type='artwork', responsible=self.other_user)
        self.assertIsNone(content.sub_team_id)
        self.assertEqual(content.responsible, self.other_user)

    def test_model_accepts_responsible_from_ministry_outside_team(self):
        content = MediaContent.objects.create(
            title='Live', content_type='livestream', sub_team=self.team, responsible=self.other_user,
        )
        self.assertEqual(content.responsible, self.other_user)

    def test_model_accepts_task_from_ministry_outside_team(self):
        content = MediaContent.objects.create(title='Live', content_type='livestream', sub_team=self.team)
        task = MediaTask.objects.create(title='Operador', content=content, assigned_to=self.other_user)
        self.assertEqual(task.assigned_to, self.other_user)

    def test_sync_accepts_ministry_member_outside_team(self):
        content = MediaContent.objects.create(title='Live', content_type='livestream', sub_team=self.team)
        task = MediaTask.objects.create(title='Operador', content=content, assigned_to=self.user)
        sync_content_assignments(content, [{'task_id': task.pk, 'role': 'Editor', 'user_id': self.other_user.pk}])
        task.refresh_from_db()
        self.assertEqual(task.assigned_to, self.other_user)

    def test_sync_rejects_user_outside_ministry_without_deleting_existing_tasks(self):
        foreign_user = User.objects.create_user('foreign@example.test')
        Member.objects.create(name='Fora', user=foreign_user)
        content = MediaContent.objects.create(title='Live', content_type='livestream', sub_team=self.team)
        task = MediaTask.objects.create(title='Operador', content=content, assigned_to=self.user)
        with self.assertRaises(ValidationError):
            sync_content_assignments(content, [{'role': 'Editor', 'user_id': foreign_user.pk}])
        self.assertTrue(MediaTask.objects.filter(pk=task.pk).exists())

    def test_quick_create_assigns_team_and_steps(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('media_demand_quick_create'), self.payload())
        self.assertEqual(response.status_code, 302)
        content = MediaContent.objects.get(title='Transmissão')
        self.assertEqual(content.sub_team, self.team)
        self.assertEqual(content.responsible, self.user)
        self.assertEqual(content.tasks.get().assigned_to, self.user)

    def test_quick_create_links_demand_to_event(self):
        import datetime
        event = Event.objects.create(
            title='Culto especial',
            description='Evento',
            event_date=datetime.date(2026, 11, 20),
            display_start=datetime.date(2026, 11, 1),
            display_end=datetime.date(2026, 11, 20),
        )
        self.client.force_login(self.user)
        data = self.payload()
        data['demand-event'] = str(event.pk)
        response = self.client.post(reverse('media_demand_quick_create'), data)
        self.assertEqual(response.status_code, 302)
        self.assertIn(f'selected=event-{event.pk}', response.url)
        content = MediaContent.objects.get(title='Transmissão')
        self.assertEqual(content.event_id, event.pk)
        task = content.tasks.get()
        self.assertEqual(task.due_date.date(), datetime.date(2026, 11, 13))

    def test_task_steps_keep_form_order_not_due_date(self):
        self.client.force_login(self.user)
        data = self.payload()
        data['demand-assignment_role'] = ['Designer', 'Publicador']
        data['demand-assignment_user'] = [str(self.user.pk), str(self.other_user.pk)]
        data['demand-assignment_due_days'] = ['14', '7']
        data['demand-assignment_due_relation'] = ['before', 'before']
        self.client.post(reverse('media_demand_quick_create'), data)
        content = MediaContent.objects.get(title='Transmissão')
        titles = list(content.tasks.order_by('sort_order', 'pk').values_list('title', flat=True))
        self.assertEqual(titles, ['Designer', 'Publicador'])

    def test_quick_create_accepts_ministry_member_outside_team(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('media_demand_quick_create'), self.payload(self.other_user.pk))
        self.assertEqual(response.status_code, 302)
        content = MediaContent.objects.get(title='Transmissão')
        self.assertEqual(content.responsible, self.other_user)

    def test_invalid_quick_create_rejects_user_outside_ministry(self):
        foreign_user = User.objects.create_user('foreign@example.test')
        Member.objects.create(name='Fora', user=foreign_user)
        self.client.force_login(self.user)
        response = self.client.post(reverse('media_demand_quick_create'), self.payload(foreign_user.pk))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'Transmissão', status_code=400)
        self.assertContains(response, 'Mídia Externa', status_code=400)
        self.assertFalse(MediaContent.objects.exists())
        self.assertFalse(MediaTask.objects.exists())

    def test_quick_form_rejects_team_from_other_ministry(self):
        self.client.force_login(self.user)
        data = self.payload()
        data['demand-sub_team'] = self.foreign_team.pk
        self.assertEqual(self.client.post(reverse('media_demand_quick_create'), data).status_code, 400)
        self.assertFalse(MediaContent.objects.exists())

    def test_legacy_routes_redirect_to_same_generic_team(self):
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get(reverse('media_subteam_detail', args=[self.team.pk])), reverse('ministry_team_detail', kwargs={'ministry_id': self.ministry.pk, 'pk': self.team.pk}))
        self.assertEqual(self.client.get(reverse('media_subteam_detail', args=[self.foreign_team.pk])).status_code, 404)

    def test_leaving_team_preserves_history_and_shows_warning(self):
        content = MediaContent.objects.create(title='Live', content_type='livestream', sub_team=self.team, responsible=self.user)
        self.membership.is_active = False
        self.membership.save()
        content.refresh_from_db()
        self.assertEqual(content.responsible, self.user)
        self.assertTrue(build_free_detail(content.pk)['content'].assignment_warning)

    def test_hub_does_not_offer_other_ministry_teams(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('media_content_list'))
        self.assertContains(response, self.team.name)
        self.assertNotContains(response, self.foreign_team.name)

    def test_quick_form_accepts_standard_dictionary_data(self):
        from website.forms.media_planning import MediaDemandQuickForm
        form = MediaDemandQuickForm({'title': 'Livre', 'content_type': 'artwork'})
        self.assertTrue(form.is_valid(), form.errors)

    def test_invalid_assignment_date_is_not_silently_discarded(self):
        self.client.force_login(self.user)
        data = self.payload()
        data['demand-assignment_due_days'] = ['-1']
        data['demand-assignment_due_relation'] = ['before']
        response = self.client.post(reverse('media_demand_quick_create'), data)
        self.assertContains(response, 'quantidade válida', status_code=400)
        self.assertFalse(MediaContent.objects.exists())

    def test_update_can_change_team_and_reassign_existing_steps(self):
        self.client.force_login(self.user)
        self.client.post(reverse('media_demand_quick_create'), self.payload())
        content = MediaContent.objects.get(title='Transmissão')
        task = content.tasks.get()
        new_team = MediaSubTeam.objects.create(name='Edição', ministry=self.ministry)
        MediaSubTeamMembership.objects.create(sub_team=new_team, member=self.other_member)
        data = self.payload(self.other_user.pk, prefix='edit')
        data['edit-sub_team'] = new_team.pk
        data['edit-assignment_id'] = [str(task.pk)]
        response = self.client.post(reverse('media_demand_quick_update', args=[content.pk]), data)
        self.assertEqual(response.status_code, 302)
        task.refresh_from_db()
        content.refresh_from_db()
        self.assertEqual(content.sub_team, new_team)
        self.assertEqual(content.responsible, self.other_user)
        self.assertEqual(task.assigned_to, self.other_user)

    def test_rejected_update_does_not_change_title_or_tasks(self):
        self.client.force_login(self.user)
        self.client.post(reverse('media_demand_quick_create'), self.payload())
        content = MediaContent.objects.get(title='Transmissão')
        task = content.tasks.get()
        data = self.payload(self.other_user.pk, prefix='edit')
        data['edit-title'] = 'Não salvar'
        data['edit-assignment_id'] = [str(task.pk)]
        self.assertEqual(self.client.post(reverse('media_demand_quick_update', args=[content.pk]), data).status_code, 400)
        content.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(content.title, 'Transmissão')
        self.assertEqual(task.assigned_to, self.user)

    def test_model_team_change_cannot_leave_incompatible_tasks(self):
        content = MediaContent.objects.create(title='Live', content_type='livestream', sub_team=self.team)
        MediaTask.objects.create(content=content, title='Operador', assigned_to=self.user)
        content.sub_team = MediaSubTeam.objects.create(name='Outra equipe', ministry=self.ministry)
        with self.assertRaises(ValidationError):
            content.save()

    def test_steps_from_another_demand_are_rejected(self):
        content = MediaContent.objects.create(title='Live', content_type='livestream')
        other = MediaContent.objects.create(title='Outro', content_type='artwork')
        task = MediaTask.objects.create(title='Etapa', content=other)
        with self.assertRaises(ValidationError):
            sync_content_assignments(content, [{'task_id': task.pk, 'role': 'Editar'}])
        self.assertTrue(MediaTask.objects.filter(pk=task.pk, content=other).exists())

    def test_regular_member_can_create_demand_but_not_manage_teams(self):
        self.client.force_login(self.other_user)
        data = self.payload()
        data['demand-sub_team'] = ''
        self.assertEqual(self.client.post(reverse('media_demand_quick_create'), data).status_code, 302)
        self.assertEqual(self.client.post(reverse('media_subteam_create'), {'name': 'Não criar'}).status_code, 403)



class MinistryOrganizationMigrationTests(TransactionTestCase):
    migrate_from = ('website', '0102_alter_mediacontent_content_type_and_more')
    migrate_to = ('website', '0103_ministry_organization')

    def test_existing_team_ids_and_demand_links_survive(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        try:
            old = executor.loader.project_state([self.migrate_from]).apps
            ministry, _ = old.get_model('website', 'Ministry').objects.get_or_create(name='Mídia Externa')
            team = old.get_model('website', 'MediaSubTeam').objects.create(name='Legada')
            content = old.get_model('website', 'MediaContent').objects.create(title='Live antiga', content_type='livestream', sub_team_id=team.pk)
            executor = MigrationExecutor(connection)
            executor.migrate([self.migrate_to])
            self.assertEqual(MediaSubTeam.objects.get(pk=team.pk).ministry_id, ministry.pk)
            migrated = executor.loader.project_state([self.migrate_to]).apps
            self.assertEqual(migrated.get_model('website', 'MediaContent').objects.get(pk=content.pk).sub_team_id, team.pk)
        finally:
            latest = MigrationExecutor(connection)
            latest.migrate(latest.loader.graph.leaf_nodes())
