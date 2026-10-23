import datetime
import tempfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from website.models import (
    Event, MediaAttachment, MediaComment, MediaContent, MediaTask,
    Member, Ministry, MinistryMembership, User,
)
from website.services.media_work import activity_data, deadline_info, ordered_tasks


class MediaMyWorkTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ministry, _ = Ministry.objects.get_or_create(code='midia_externa', defaults={'name': 'Mídia Externa'})
        cls.user = cls.create_member('worker')
        cls.other = cls.create_member('colleague')
        cls.leader = cls.create_member('leader', role='leader')
        cls.outsider = User.objects.create_user('outsider@example.test')
        Member.objects.create(name='Fora do ministério', user=cls.outsider)
        cls.event = Event.objects.create(
            title='Conferência', description='Evento', event_date=datetime.date(2026, 10, 28),
            display_start=datetime.date(2026, 10, 1), display_end=datetime.date(2026, 10, 28),
        )
        cls.content = MediaContent.objects.create(
            title='Chamada da Conferência', content_type='video', event=cls.event,
            description='Briefing permitido https://example.com/referencia',
            observations='Orientações permitidas', responsible=cls.other,
        )
        cls.task = MediaTask.objects.create(
            content=cls.content, assigned_to=cls.user, title='Editar chamada',
            description='Instruções da minha etapa',
        )
        cls.private_task = MediaTask.objects.create(
            content=cls.content, assigned_to=cls.other, title='ETAPA PRIVADA',
            description='DESCRIÇÃO PRIVADA',
        )
        MediaComment.objects.create(task=cls.private_task, author=cls.other, text='COMENTÁRIO PRIVADO')
        cls.leader_task = MediaTask.objects.create(content=cls.content, assigned_to=cls.leader, title='Minha revisão')
        cls.free_content = MediaContent.objects.create(title='Arte avulsa', content_type='artwork', responsible=cls.user)
        cls.free_task = MediaTask.objects.create(content=cls.free_content, assigned_to=cls.user, title='Criar arte avulsa')
        cls.completed_task = MediaTask.objects.create(content=cls.content, assigned_to=cls.user, title='Etapa já concluída', status='completed')

    @classmethod
    def create_member(cls, name, role='member'):
        user = User.objects.create_user(f'{name}@example.test', password='test')
        member = Member.objects.create(name=name, user=user)
        MinistryMembership.objects.create(ministry=cls.ministry, member=member, role=role)
        return user

    def setUp(self):
        self.client.force_login(self.user)

    def detail_url(self, task=None):
        return reverse('media_my_work_detail', args=[(task or self.task).pk])

    def status_url(self, task=None):
        return reverse('media_my_work_status', args=[(task or self.task).pk])

    def test_default_list_contains_own_open_and_recently_completed_steps(self):
        response = self.client.get(reverse('media_my_work'), {'user': self.other.pk, 'responsible': self.other.pk})
        self.assertEqual({a['id'] for a in response.context['activities']}, {self.task.pk, self.free_task.pk, self.completed_task.pk})
        self.assertNotContains(response, 'ETAPA PRIVADA')
        self.assertNotContains(response, 'Minha revisão')
        self.assertContains(response, 'Etapa já concluída')

    def test_completed_tab_is_also_personal(self):
        self.private_task.status = 'completed'
        self.private_task.save(update_fields=['status'])
        response = self.client.get(reverse('media_my_work'), {'tab': 'completed'})
        self.assertEqual([a['id'] for a in response.context['activities']], [self.completed_task.pk])
        self.assertNotContains(response, 'ETAPA PRIVADA')
        self.assertNotContains(response, 'Atrasadas')

    def test_context_is_allowlisted_and_other_step_information_is_absent(self):
        response = self.client.get(self.detail_url())
        for allowed in ['Briefing permitido', 'Orientações permitidas', 'Conferência', 'Instruções da minha etapa']:
            self.assertContains(response, allowed)
        for private in ['ETAPA PRIVADA', 'DESCRIÇÃO PRIVADA', 'COMENTÁRIO PRIVADO', 'colleague@example.test']:
            self.assertNotContains(response, private)
        activity = response.context['activity']
        self.assertNotIn('content', activity)
        self.assertNotIn('responsible', activity)
        self.assertNotIn('tasks', activity)
        self.assertNotIn('demand_status', activity)

    def test_other_step_ids_return_404_for_read_and_write(self):
        self.assertEqual(self.client.get(self.detail_url(self.private_task)).status_code, 404)
        self.assertEqual(self.client.post(self.status_url(self.private_task), {'status': 'completed'}).status_code, 404)
        self.private_task.refresh_from_db()
        self.assertEqual(self.private_task.status, 'pending')

    def test_activity_card_fragment_keeps_private_scope(self):
        response = self.client.get(self.detail_url(), {'fragment': 'activity'})
        self.assertTemplateUsed(response, 'member/media_planning/partials/my_work_detail_content.html')
        self.assertContains(response, 'Briefing permitido')
        self.assertNotContains(response, '<html')
        self.assertNotContains(response, 'ETAPA PRIVADA')
        self.assertNotContains(response, 'COMENTÁRIO PRIVADO')
        self.assertEqual(self.client.get(self.detail_url(self.private_task), {'fragment': 'activity'}).status_code, 404)

    def test_leader_can_access_personal_view_without_expanding_scope(self):
        self.client.force_login(self.leader)
        response = self.client.get(reverse('media_my_work'))
        self.assertEqual([a['id'] for a in response.context['activities']], [self.leader_task.pk])
        self.assertEqual(self.client.get(self.detail_url()).status_code, 404)
        self.assertEqual(self.client.post(self.status_url(), {'status': 'completed'}).status_code, 404)
        self.assertEqual(self.client.get(reverse('media_content_list')).status_code, 200)

    def test_deadline_order_groups_timezone_and_nulls_last(self):
        today = datetime.date(2026, 10, 22)
        tasks = []
        for offset in [20, 1, -1, 0, 4, -3]:
            due = timezone.make_aware(datetime.datetime.combine(today + datetime.timedelta(days=offset), datetime.time(12)))
            tasks.append(MediaTask.objects.create(content=self.content, assigned_to=self.user, title=f'Dia {offset}', due_date=due))
        with patch('website.views.media_planning.my_work.timezone.localdate', wraps=timezone.localdate) as localdate:
            # Freeze "today" while preserving conversions of actual deadlines.
            localdate.side_effect = lambda value=None: today if value is None else value.astimezone(timezone.get_current_timezone()).date()
            response = self.client.get(reverse('media_my_work'))
        activities = response.context['activities']
        self.assertEqual([a['id'] for a in activities[:6]], [tasks[i].pk for i in [5, 2, 3, 1, 4, 0]])
        self.assertEqual([a['deadline']['group'] for a in activities[:6]], ['Atrasadas', 'Atrasadas', 'Hoje', 'Amanhã', 'Próximos dias', 'Mais adiante'])
        self.assertIsNone(activities[-1]['due_date'])
        with timezone.override('America/Sao_Paulo'):
            utc = datetime.datetime(2026, 10, 23, 1, tzinfo=datetime.timezone.utc)
            self.assertEqual(deadline_info(utc, today)['label'], 'Hoje')

    def test_event_and_standalone_demands_share_layout(self):
        response = self.client.get(reverse('media_my_work'))
        self.assertContains(response, 'Conferência')
        self.assertContains(response, 'Demanda avulsa')
        self.assertContains(self.client.get(self.detail_url(self.free_task)), 'Arte avulsa')

    def test_local_day_and_display_remain_consistent_across_utc_midnight(self):
        self.task.due_date = datetime.datetime(2026, 10, 23, 2, tzinfo=datetime.timezone.utc)
        self.task.save(update_fields=['due_date'])
        now = datetime.datetime(2026, 10, 23, 1, tzinfo=datetime.timezone.utc)
        with patch('django.utils.timezone.now', return_value=now):
            response = self.client.get(self.detail_url())
        self.assertEqual(response.context['activity']['deadline']['label'], 'Hoje')
        self.assertContains(response, '22/10/2026 23:00')

    def test_demand_responsibility_alone_does_not_grant_step_access(self):
        unassigned = MediaTask.objects.create(content=self.free_content, title='Etapa sem responsável')
        response = self.client.get(reverse('media_my_work'))
        self.assertNotContains(response, 'Etapa sem responsável')
        self.assertEqual(self.client.get(self.detail_url(unassigned)).status_code, 404)
        self.assertEqual(self.client.post(self.status_url(unassigned), {'status': 'completed'}).status_code, 404)

    def test_protected_demand_status_is_preserved(self):
        self.free_content.status = 'published'
        self.free_content.save(update_fields=['status'])
        self.client.post(self.status_url(self.free_task), {'status': 'completed'})
        self.free_content.refresh_from_db()
        self.assertEqual(self.free_content.status, 'published')

    def test_status_only_update_reuses_demand_sync(self):
        response = self.client.post(self.status_url(), {
            'status': 'in_progress', 'title': 'ALTERADO', 'assigned_to': self.other.pk,
            'description': 'ALTERADO', 'content': self.free_content.pk, 'due_date': '2030-01-01',
        })
        self.assertRedirects(response, self.detail_url())
        self.task.refresh_from_db()
        self.content.refresh_from_db()
        self.assertEqual(self.task.status, 'in_progress')
        self.assertEqual(self.task.title, 'Editar chamada')
        self.assertEqual(self.task.description, 'Instruções da minha etapa')
        self.assertEqual(self.task.assigned_to_id, self.user.pk)
        self.assertEqual(self.task.content_id, self.content.pk)
        self.assertIsNone(self.task.due_date)
        self.assertEqual(self.content.status, 'in_progress')
        self.client.post(self.status_url(), {'status': 'completed'})
        self.content.refresh_from_db()
        self.assertEqual(self.content.status, 'in_progress')
        self.client.post(self.status_url(self.free_task), {'status': 'completed', 'return_to': 'list'})
        self.free_content.refresh_from_db()
        self.assertEqual(self.free_content.status, 'approved')

    def test_invalid_status_and_get_do_not_mutate(self):
        self.assertEqual(self.client.post(self.status_url(), {'status': 'approved'}).status_code, 400)
        self.assertEqual(self.client.get(self.status_url()).status_code, 405)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, 'pending')

    def test_csrf_required_for_update(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.status_url(), {'status': 'completed'}).status_code, 403)

    def test_outsiders_inactive_members_and_anonymous_are_denied(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('media_my_work')).status_code, 302)
        for user in [self.outsider, self.user]:
            self.client.force_login(user)
            if user == self.user:
                MinistryMembership.objects.filter(member=user.member).update(is_active=False)
            for url in [reverse('media_my_work'), self.detail_url()]:
                self.assertRedirects(self.client.get(url), reverse('member_dashboard'), fetch_redirect_response=False)
            self.assertEqual(self.client.post(self.status_url(), {'status': 'completed'}).status_code, 302)

    def test_inactive_member_profile_is_denied(self):
        Member.objects.filter(pk=self.user.member.pk).update(is_active=False)
        self.assertEqual(self.client.get(reverse('media_my_work')).status_code, 302)

    def test_members_do_not_gain_management_access(self):
        for name in ['media_content_list', 'media_organization']:
            self.assertRedirects(self.client.get(reverse(name)), reverse('member_dashboard'), fetch_redirect_response=False)

    def test_home_access_visible_for_members_and_leaders(self):
        for user in [self.user, self.leader]:
            self.client.force_login(user)
            response = self.client.get(reverse('member_dashboard'))
            self.assertContains(response, reverse('media_my_work'))
            if user == self.user:
                self.assertNotContains(response, reverse('media_content_list'))

    def test_attachments_allow_demand_and_own_step_only(self):
        with tempfile.TemporaryDirectory() as root, override_settings(MEDIA_ROOT=root):
            def attachment(name, **kwargs):
                return MediaAttachment.objects.create(name=name, file=SimpleUploadedFile(name + '.pdf', b'%PDF-test'), uploaded_by=self.other, **kwargs)
            shared = attachment('Arquivo geral', content=self.content)
            own = attachment('Meu arquivo', task=self.task)
            hidden = attachment('Arquivo privado', task=self.private_task)
            ambiguous = attachment('Arquivo inválido', content=self.content, task=self.private_task)
            unrelated = attachment('Arquivo avulso', content=self.free_content)
            response = self.client.get(self.detail_url())
            self.assertEqual({a.pk for a in response.context['attachments']}, {shared.pk, own.pk})
            for item in [shared, own]:
                url = reverse('media_my_work_attachment', args=[self.task.pk, item.pk])
                download = self.client.get(url)
                self.assertEqual(download.status_code, 200)
                self.assertEqual(b''.join(download.streaming_content), b'%PDF-test')
                self.assertContains(response, url)
                self.assertNotContains(response, item.file.url)
            for item in [hidden, ambiguous, unrelated]:
                self.assertNotContains(response, item.name)
                self.assertEqual(self.client.get(reverse('media_my_work_attachment', args=[self.task.pk, item.pk])).status_code, 404)
            self.assertEqual(self.client.get(reverse('media_my_work_attachment', args=[self.private_task.pk, shared.pk])).status_code, 404)

    def test_deleted_demands_and_steps_are_excluded(self):
        self.content.delete()
        response = self.client.get(reverse('media_my_work'))
        self.assertEqual([a['id'] for a in response.context['activities']], [self.free_task.pk])
        self.assertEqual(self.client.get(self.detail_url()).status_code, 404)

    def test_list_query_count_does_not_grow_with_tasks(self):
        with self.assertNumQueries(1):
            [activity_data(task) for task in ordered_tasks(self.user)]
        for index in range(8):
            MediaTask.objects.create(content=self.content, assigned_to=self.user, title=f'Etapa {index}')
        with self.assertNumQueries(1):
            [activity_data(task) for task in ordered_tasks(self.user)]

    def test_pagination_is_bounded(self):
        for index in range(42):
            MediaTask.objects.create(content=self.content, assigned_to=self.user, title=f'Etapa {index}')
        response = self.client.get(reverse('media_my_work'))
        self.assertEqual(len(response.context['activities']), 40)
        self.assertTrue(response.context['page'].has_next())

    def test_empty_state(self):
        MediaTask.objects.filter(assigned_to=self.user).update(
            status='completed', update_at=timezone.now() - datetime.timedelta(days=31),
        )
        response = self.client.get(reverse('media_my_work'))
        self.assertContains(response, 'Tudo certo por aqui.')

    def test_search_by_task_demand_and_event_keeps_personal_scope(self):
        for query, expected in [
            ('Editar chamada', [self.task.pk]),
            ('Chamada da Conferência', [self.task.pk, self.completed_task.pk]),
            ('Conferência', [self.task.pk, self.completed_task.pk]),
        ]:
            response = self.client.get(reverse('media_my_work'), {'q': query})
            self.assertEqual([a['id'] for a in response.context['activities']], expected)
        response = self.client.get(reverse('media_my_work'), {'q': 'ETAPA PRIVADA'})
        self.assertEqual(response.context['activities'], [])
        self.assertContains(response, 'Nenhuma atividade encontrada.')

    def test_status_and_local_date_range_filters(self):
        self.task.status = 'in_progress'
        self.task.due_date = datetime.datetime(2026, 10, 23, 2, tzinfo=datetime.timezone.utc)
        self.task.save(update_fields=['status', 'due_date'])
        response = self.client.get(reverse('media_my_work'), {
            'status': 'in_progress', 'period_from': '2026-10-22', 'period_to': '2026-10-22',
        })
        self.assertEqual([a['id'] for a in response.context['activities']], [self.task.pk])
        response = self.client.get(reverse('media_my_work'), {'period_from': '2026-10-23'})
        self.assertEqual(response.context['activities'], [])

    def test_invalid_filters_are_ignored_and_completed_search_is_preserved(self):
        response = self.client.get(reverse('media_my_work'), {'status': 'bogus', 'period_from': '2026-99-99', 'period_to': 'invalid'})
        self.assertEqual(len(response.context['activities']), 3)
        response = self.client.get(reverse('media_my_work'), {'tab': 'completed', 'q': 'já concluída', 'status': 'pending'})
        self.assertEqual([a['id'] for a in response.context['activities']], [self.completed_task.pk])
        self.assertIn('q=', response.context['pagination_query'])
        self.assertIn('tab=completed', response.context['pagination_query'])
