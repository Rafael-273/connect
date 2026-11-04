from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from website.models import Member, Ministry, MinistryMembership, User, MediaSubTeam, MediaSubTeamMembership
from website.models.media_organization import MediaCredentialAccessLog, MediaResource, MediaResourceCredential
from website.utils.media_vault import encrypt_secret
from website.utils.media_vault import decrypt_secret


class ResourceModalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        ministry, _ = Ministry.objects.get_or_create(name='Mídia Externa')
        ministry.code = 'midia_externa'
        ministry.save()
        cls.user = User.objects.create_user('resources@example.test', password='test')
        cls.member = Member.objects.create(name='Líder de mídia', user=cls.user)
        MinistryMembership.objects.create(ministry=ministry, member=cls.member, role='leader')
        cls.team = MediaSubTeam.objects.create(name='Design', ministry=ministry)
        cls.other_team = MediaSubTeam.objects.create(name='Vídeo', ministry=ministry)
        cls.worker_user = User.objects.create_user('worker-resources@example.test', password='test')
        cls.worker = Member.objects.create(name='Integrante', user=cls.worker_user)
        MinistryMembership.objects.create(ministry=ministry, member=cls.worker, role='member')
        cls.participation = MediaSubTeamMembership.objects.create(sub_team=cls.team, member=cls.worker)

    def setUp(self):
        self.client.force_login(self.user)

    def test_list_includes_create_modal(self):
        response = self.client.get(reverse('media_resource_list'))
        self.assertContains(response, 'id="resource-create-modal"')
        self.assertContains(response, 'name="from_resource_modal" value="1"')
        self.assertContains(response, 'type="password"')
        self.assertNotContains(response, 'name="responsible"')
        self.assertNotContains(response, 'name="renewal_date"')
        self.assertNotContains(response, 'name="is_active"')
        self.assertFalse(response.context['resource_modal_open'])

    def test_invalid_submission_reopens_modal_with_values_and_errors(self):
        response = self.client.post(reverse('media_resource_create'), {
            'from_resource_modal': '1', 'name': 'Conta da equipe', 'category': 'other',
            'url': 'invalid-url', 'password': 'secret-do-not-repeat', 'access_teams': [self.team.pk],
        })
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'member/media_planning/resource_list.html')
        self.assertTrue(response.context['resource_modal_open'])
        self.assertContains(response, 'value="Conta da equipe"')
        self.assertContains(response, 'value="invalid-url"')
        self.assertNotContains(response, 'secret-do-not-repeat')
        self.assertContains(response, 'checked')
        self.assertIn('url', response.context['resource_form'].errors)
        self.assertFalse(MediaResource.objects.filter(name='Conta da equipe').exists())

    def test_valid_modal_submission_creates_resource_and_returns_to_list(self):
        response = self.client.post(reverse('media_resource_create'), {
            'from_resource_modal': '1', 'name': 'Conta da equipe', 'category': 'other',
            'access_teams': [self.team.pk], 'password': ' senha com espaços ',
        })
        self.assertRedirects(response, reverse('media_resource_list'))
        resource = MediaResource.objects.get(name='Conta da equipe', is_active=True)
        self.assertIn(self.team, resource.access_teams.all())
        self.assertNotEqual(resource.credential.encrypted_password, ' senha com espaços ')
        self.assertEqual(decrypt_secret(resource.credential.encrypted_password), ' senha com espaços ')
        self.assertEqual(resource.credential.last_updated_by, self.user)
        self.assertTrue(MediaCredentialAccessLog.objects.filter(credential=resource.credential, user=self.user, action='update').exists())

    def test_creation_without_password_does_not_create_credentials(self):
        response = self.client.post(reverse('media_resource_create'), {
            'from_resource_modal': '1', 'name': 'Sem senha', 'category': 'other',
        })
        self.assertRedirects(response, reverse('media_resource_list'))
        self.assertFalse(MediaResource.objects.get(name='Sem senha').has_credentials)

    @patch('website.views.media_planning.organization.encrypt_secret', side_effect=RuntimeError('encryption failed'))
    def test_encryption_failure_rolls_back_resource(self, encrypt):
        with self.assertRaises(RuntimeError):
            self.client.post(reverse('media_resource_create'), {
                'from_resource_modal': '1', 'name': 'Falha no cofre', 'category': 'other', 'password': 'segredo',
            })
        self.assertFalse(MediaResource.objects.filter(name='Falha no cofre').exists())

    def test_team_member_can_view_only_assigned_resources_and_reveal_password(self):
        assigned = MediaResource.objects.create(name='Acesso design', category='other')
        assigned.access_teams.add(self.team)
        MediaResourceCredential.objects.create(resource=assigned, encrypted_password=encrypt_secret('<senha>'))
        private = MediaResource.objects.create(name='Apenas líderes', category='other')
        other = MediaResource.objects.create(name='Acesso vídeo', category='other')
        other.access_teams.add(self.other_team)
        self.client.force_login(self.worker_user)
        response = self.client.get(reverse('media_resource_list'))
        self.assertContains(response, 'Acesso design')
        self.assertNotContains(response, 'Apenas líderes')
        self.assertNotContains(response, 'Acesso vídeo')
        self.assertNotContains(response, 'data-open-resource-modal')
        self.assertContains(self.client.get(reverse('media_resource_detail', args=[assigned.pk]), {'modal': '1'}), 'Revelar senha')
        reveal = self.client.post(reverse('media_resource_credential_reveal', args=[assigned.pk]))
        self.assertEqual(reveal.json()['password'], '<senha>')
        self.assertTrue(MediaCredentialAccessLog.objects.filter(credential__resource=assigned, user=self.worker_user, action='view').exists())
        for resource in (private, other):
            self.assertEqual(self.client.get(reverse('media_resource_detail', args=[resource.pk])).status_code, 404)
            self.assertEqual(self.client.post(reverse('media_resource_credential_reveal', args=[resource.pk])).status_code, 404)
        self.assertRedirects(self.client.get(reverse('media_resource_edit', args=[assigned.pk])), reverse('member_dashboard'), fetch_redirect_response=False)
        self.assertRedirects(self.client.post(reverse('media_resource_credential_update', args=[assigned.pk]), {'password': 'alterada'}), reverse('member_dashboard'), fetch_redirect_response=False)

    def test_access_changes_with_team_membership_and_team_status(self):
        resource = MediaResource.objects.create(name='Recurso', category='other')
        resource.access_teams.add(self.team)
        self.client.force_login(self.worker_user)
        url = reverse('media_resource_detail', args=[resource.pk])
        self.assertEqual(self.client.get(url, {'modal': '1'}).status_code, 200)
        self.participation.is_active = False
        self.participation.save()
        self.assertEqual(self.client.get(url).status_code, 404)
        self.participation.is_active = True
        self.participation.save()
        self.team.is_active = False
        self.team.save()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_inactive_ministry_membership_cannot_access_resource(self):
        resource = MediaResource.objects.create(name='Recurso', category='other')
        resource.access_teams.add(self.team)
        membership = MinistryMembership.objects.get(member=self.worker)
        membership.is_active = False
        membership.save()
        self.client.force_login(self.worker_user)
        self.assertRedirects(self.client.get(reverse('media_resource_detail', args=[resource.pk])), reverse('member_dashboard'), fetch_redirect_response=False)
        self.assertRedirects(self.client.post(reverse('media_resource_credential_reveal', args=[resource.pk])), reverse('member_dashboard'), fetch_redirect_response=False)

    def test_assigned_team_leader_has_access_without_separate_team_membership(self):
        self.team.leader = self.worker
        self.team.save()
        self.participation.is_active = False
        self.participation.save()
        resource = MediaResource.objects.create(name='Recurso', category='other')
        resource.access_teams.add(self.team)
        self.client.force_login(self.worker_user)
        self.assertEqual(self.client.get(reverse('media_resource_detail', args=[resource.pk]), {'modal': '1'}).status_code, 200)

    def test_leader_can_change_access_teams(self):
        resource = MediaResource.objects.create(name='Editável', category='other')
        response = self.client.post(reverse('media_resource_edit', args=[resource.pk]), {
            'edit-name': resource.name, 'edit-category': 'other', 'edit-access_teams': [self.team.pk],
        })
        self.assertRedirects(response, reverse('media_resource_list') + f'?resource={resource.pk}')
        self.assertEqual(list(resource.access_teams.all()), [self.team])
        response = self.client.post(reverse('media_resource_edit', args=[resource.pk]), {
            'edit-name': resource.name, 'edit-category': 'other',
        })
        self.assertRedirects(response, reverse('media_resource_list') + f'?resource={resource.pk}')
        self.assertFalse(resource.access_teams.exists())
        self.client.force_login(self.worker_user)
        self.assertEqual(self.client.get(reverse('media_resource_detail', args=[resource.pk])).status_code, 404)

    def test_rejects_teams_from_other_ministries(self):
        other_ministry = Ministry.objects.create(name='Outro ministério')
        team = MediaSubTeam.objects.create(name='Outra equipe', ministry=other_ministry)
        response = self.client.post(reverse('media_resource_create'), {
            'from_resource_modal': '1', 'name': 'Inválido', 'category': 'other', 'access_teams': [team.pk],
        })
        self.assertIn('access_teams', response.context['resource_form'].errors)
        self.assertFalse(MediaResource.objects.filter(name='Inválido').exists())

    def test_search_and_filters_apply_to_accessible_resources(self):
        match = MediaResource.objects.create(name='Instagram da equipe', category='social', email_username='equipe@example.test')
        match.access_teams.add(self.team)
        hidden = MediaResource.objects.create(name='Instagram privado', category='social')
        other = MediaResource.objects.create(name='Email de design', category='email', is_active=False)
        other.access_teams.add(self.team)
        self.client.force_login(self.worker_user)
        url = reverse('media_resource_list')
        response = self.client.get(url, {'q': 'INSTAGRAM', 'category': 'social', 'status': 'active'})
        self.assertContains(response, match.name)
        self.assertNotContains(response, hidden.name)
        self.assertNotContains(response, other.name)
        response = self.client.get(url, {'q': 'equipe@example.test'})
        self.assertContains(response, match.name)
        response = self.client.get(url, {'category': 'email', 'status': 'inactive'})
        self.assertContains(response, other.name)
        self.assertNotContains(response, match.name)
        response = self.client.get(url, {'q': 'inexistente'})
        self.assertContains(response, 'Nenhum recurso encontrado')
        self.assertContains(response, 'Limpar pesquisa e filtros')

    def test_modal_details_use_same_access_rules_without_exposing_password(self):
        resource = MediaResource.objects.create(name='Conta da equipe', category='social')
        resource.access_teams.add(self.team)
        MediaResourceCredential.objects.create(resource=resource, encrypted_password=encrypt_secret('senha privada'))
        url = reverse('media_resource_detail', args=[resource.pk])
        leader_list = self.client.get(reverse('media_resource_list'))
        self.assertContains(leader_list, 'id="resource-detail-actions"')
        leader_detail = self.client.get(url, {'modal': '1'})
        self.assertContains(leader_detail, f'data-edit-url="{reverse("media_resource_edit", args=[resource.pk])}"')
        self.assertNotContains(leader_detail, '> Editar recurso</a>')
        self.assertNotContains(leader_detail, 'Salvar credenciais')
        self.client.force_login(self.worker_user)
        self.assertNotContains(self.client.get(reverse('media_resource_list')), 'id="resource-detail-actions"')
        response = self.client.get(url, {'modal': '1'})
        self.assertTemplateUsed(response, 'member/media_planning/partials/resource_detail_content.html')
        self.assertContains(response, 'Revelar senha')
        self.assertNotContains(response, 'senha privada')
        self.assertNotContains(response, 'data-resource-credential-form')
        self.assertNotContains(response, 'data-edit-url')
        self.participation.is_active = False
        self.participation.save()
        self.assertEqual(self.client.get(url, {'modal': '1'}).status_code, 404)

    def test_edit_modal_keeps_existing_credentials_and_updates_them_safely(self):
        resource = MediaResource.objects.create(name='Conta original', category='social')
        credential = MediaResourceCredential.objects.create(resource=resource, encrypted_password=encrypt_secret('senha original'), encrypted_notes=encrypt_secret('notas originais'))
        url = reverse('media_resource_edit', args=[resource.pk])
        response = self.client.get(url, {'modal': '1'})
        self.assertTemplateUsed(response, 'member/media_planning/partials/resource_edit_content.html')
        self.assertNotContains(response, 'senha original')
        self.assertNotContains(response, 'notas originais')
        payload = {'edit-name': 'Conta atualizada', 'edit-category': 'social', 'edit-password': '', 'edit-sensitive_notes': ''}
        response = self.client.post(url, payload, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        credential.refresh_from_db()
        self.assertEqual(decrypt_secret(credential.encrypted_password), 'senha original')
        self.assertEqual(decrypt_secret(credential.encrypted_notes), 'notas originais')
        self.assertFalse(MediaCredentialAccessLog.objects.filter(credential=credential, action='update').exists())
        payload.update({'edit-password': ' nova senha ', 'edit-sensitive_notes': 'novas notas'})
        response = self.client.post(url, payload, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.json()['name'], 'Conta atualizada')
        credential.refresh_from_db()
        self.assertEqual(decrypt_secret(credential.encrypted_password), ' nova senha ')
        self.assertEqual(decrypt_secret(credential.encrypted_notes), 'novas notas')
        self.assertTrue(MediaCredentialAccessLog.objects.filter(credential=credential, user=self.user, action='update').exists())

    def test_invalid_edit_returns_modal_with_errors_without_echoing_password(self):
        resource = MediaResource.objects.create(name='Conta', category='other')
        response = self.client.post(reverse('media_resource_edit', args=[resource.pk]), {
            'edit-name': 'Nome alterado', 'edit-category': 'other', 'edit-url': 'invalida',
            'edit-password': 'senha secreta', 'edit-access_teams': [self.team.pk],
        }, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, 'Nome alterado', status_code=422)
        self.assertContains(response, 'checked', status_code=422)
        self.assertNotContains(response, 'senha secreta', status_code=422)
        resource.refresh_from_db()
        self.assertEqual(resource.name, 'Conta')

    def test_legacy_resource_pages_redirect_to_the_list(self):
        resource = MediaResource.objects.create(name='Conta', category='other')
        self.assertRedirects(self.client.get(reverse('media_resource_create')), reverse('media_resource_list') + '?create=1')
        self.assertRedirects(self.client.get(reverse('media_resource_detail', args=[resource.pk])), reverse('media_resource_list') + f'?resource={resource.pk}')
        self.assertRedirects(self.client.get(reverse('media_resource_edit', args=[resource.pk])), reverse('media_resource_list') + f'?edit={resource.pk}')

    def test_ajax_credential_update_preserves_audit_and_returns_json(self):
        resource = MediaResource.objects.create(name='Conta', category='social')
        response = self.client.post(reverse('media_resource_credential_update', args=[resource.pk]),
                                    {'password': 'nova senha'}, HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.json(), {'ok': True, 'has_password': True})
        resource.refresh_from_db()
        self.assertEqual(decrypt_secret(resource.credential.encrypted_password), 'nova senha')
        self.assertTrue(MediaCredentialAccessLog.objects.filter(credential=resource.credential, user=self.user, action='update').exists())
