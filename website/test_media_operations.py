import datetime
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from website.models import (
    Event, EventDate, MediaContent, MediaTask, MediaEventType, MediaPlanningTemplate,
    MediaPlanningTemplateItem, MediaPlanningTemplateItemStep,
    Ministry, MinistryMembership, MediaSubTeam, Member, User,
)
from website.services.media_planning import apply_template_to_event, offset_to_datetime, get_available_template_items
from website.services.demands_hub import (
    sync_content_assignments,
    sync_content_status_from_tasks,
    sync_template_item_assignments,
)
from website.forms.media_organization import MediaPlanningTemplateItemForm
from website.services.event_media_integration import (
    mark_institutional_published,
    mark_media_organized,
    require_media_eligible,
)


class MediaOperationsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ministry, _ = Ministry.objects.get_or_create(name='Mídia Externa')
        cls.ministry.code = 'midia_externa'
        cls.ministry.save()
        cls.user = User.objects.create_user('operations@example.test', password='test')
        cls.member = Member.objects.create(name='Líder', user=cls.user)
        MinistryMembership.objects.create(ministry=cls.ministry, member=cls.member, role='leader')
        cls.team = MediaSubTeam.objects.create(ministry=cls.ministry, name='Design')
        cls.kind = MediaEventType.objects.create(name='Conferência de operações')
        cls.template = MediaPlanningTemplate.objects.create(event_type=cls.kind, name='Conferência')
        cls.item = MediaPlanningTemplateItem.objects.create(template=cls.template, title='Arte principal', content_type='artwork', default_sub_team=cls.team, due_offset_days=-30, publication_offset_days=0, lead_offset_days=-45)
        cls.event = Event.objects.create(title='Conferência anual', description='Evento', event_date=datetime.date(2026, 10, 15), display_start=datetime.date(2026, 10, 1), display_end=datetime.date(2026, 10, 15), event_type=cls.kind)

    def setUp(self):
        self.client.force_login(self.user)

    def generate(self):
        return apply_template_to_event(self.event, [self.item])[0]

    def move(self):
        self.event.event_date = datetime.date(2026, 11, 15)
        self.event.save(update_fields=['event_date'])

    def test_generation_copies_rules_team_and_unassigned_responsible(self):
        content = self.generate()
        self.assertEqual(content.due_date, offset_to_datetime(self.event.event_date, -30))
        self.assertEqual(content.start_date, offset_to_datetime(self.event.event_date, -45))
        self.assertEqual(content.publication_date, offset_to_datetime(self.event.event_date, 0))
        self.assertTrue(content.due_date_auto)
        self.assertTrue(content.publication_date_auto)
        self.assertEqual(content.sub_team, self.team)
        self.assertIsNone(content.responsible)

    def test_offsets_before_on_after_and_blank(self):
        for offset in (-60, 0, 17, None):
            with self.subTest(offset=offset):
                item = MediaPlanningTemplateItem.objects.create(template=self.template, title=str(offset), content_type='artwork', due_offset_days=offset)
                content = apply_template_to_event(self.event, [item])[0]
                self.assertEqual(content.due_date, offset_to_datetime(self.event.event_date, offset))
                self.assertEqual(content.due_date_auto, offset is not None)

    def test_template_reapply_is_idempotent_including_duplicate_input(self):
        self.assertEqual(len(apply_template_to_event(self.event, [self.item, self.item])), 1)
        self.assertEqual(apply_template_to_event(self.event, [self.item]), [])
        self.assertFalse(get_available_template_items(self.event).exists())

    def test_removed_demand_is_not_silently_regenerated(self):
        content = self.generate()
        content.delete()
        self.assertEqual(apply_template_to_event(self.event, [self.item]), [])
        self.assertFalse(get_available_template_items(self.event).exists())

    def test_move_uses_frozen_rules_not_current_template(self):
        content = self.generate()
        self.item.due_offset_days = -2
        self.item.title = 'Template alterado'
        self.item.save()
        self.move()
        content.refresh_from_db()
        self.assertEqual(content.due_date, offset_to_datetime(self.event.event_date, -30))
        self.assertEqual(content.title, 'Arte principal')
        self.assertEqual(content.publication_date, offset_to_datetime(self.event.event_date, 0))

    def test_manual_date_update_fields_preserved_after_move(self):
        content = self.generate()
        manual = offset_to_datetime(self.event.event_date, -7)
        content.due_date = manual
        content.save(update_fields=['due_date'])
        content.refresh_from_db()
        self.assertFalse(content.due_date_auto)
        self.move()
        content.refresh_from_db()
        self.assertEqual(content.due_date, manual)
        self.assertEqual(content.publication_date, offset_to_datetime(self.event.event_date, 0))

    def test_manual_clearing_survives_move(self):
        content = self.generate()
        content.due_date = None
        content.save()
        self.move()
        content.refresh_from_db()
        self.assertIsNone(content.due_date)
        self.assertFalse(content.due_date_auto)

    def test_status_change_does_not_detach_automatic_deadline(self):
        content = self.generate()
        content.status = 'approved'
        content.save(update_fields=['status'])
        self.move()
        content.refresh_from_db()
        self.assertTrue(content.due_date_auto)
        self.assertEqual(content.due_date, offset_to_datetime(self.event.event_date, -30))

    def test_event_unlink_detaches_dates_without_erasing_them(self):
        content = self.generate()
        deadline = content.due_date
        content.event = None
        content.save(update_fields=['event'])
        content.refresh_from_db()
        self.assertFalse(content.due_date_auto)
        self.assertEqual(content.due_date, deadline)

    def test_content_status_syncs_from_task_progress(self):
        content = self.generate()
        sync_content_assignments(content, [
            {'role': 'Designer', 'due_offset_days': -30},
            {'role': 'Revisor', 'due_offset_days': -25},
        ])
        content.refresh_from_db()
        self.assertEqual(content.status, 'pending')

        tasks = list(content.tasks.order_by('sort_order', 'pk'))
        tasks[0].status = 'completed'
        tasks[0].save(update_fields=['status'])
        sync_content_status_from_tasks(content)
        content.refresh_from_db()
        self.assertEqual(content.status, 'in_progress')

        for task in tasks:
            task.status = 'completed'
            task.save(update_fields=['status'])
        sync_content_status_from_tasks(content)
        content.refresh_from_db()
        self.assertEqual(content.status, 'approved')

    def test_task_status_view_updates_content_status(self):
        content = self.generate()
        sync_content_assignments(content, [
            {'role': 'Designer', 'due_offset_days': -30},
            {'role': 'Revisor', 'due_offset_days': -25},
        ])
        task = content.tasks.order_by('sort_order', 'pk').first()
        response = self.client.post(
            reverse('media_task_update_status', args=[task.pk]),
            {'status': 'completed'},
            HTTP_X_REQUESTED_WITH='XMLHttpRequest',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'completed')
        self.assertEqual(data['content_status'], 'in_progress')
        self.assertEqual(data['content_label'], 'Em Produção')
        content.refresh_from_db()
        self.assertEqual(content.status, 'in_progress')

    def test_tasks_do_not_overwrite_demand_deadline(self):
        content = self.generate()
        expected = content.due_date
        sync_content_assignments(content, [{'role': 'Editar', 'due_offset_days': -7}])
        content.refresh_from_db()
        task = content.tasks.get()
        self.assertEqual(content.due_date, expected)
        self.assertTrue(content.due_date_auto)
        self.move()
        task.refresh_from_db()
        self.assertEqual(task.due_date.date(), datetime.date(2026, 11, 8))

    def test_legacy_manual_deadline_preserved(self):
        deadline = offset_to_datetime(self.event.event_date, -3)
        content = MediaContent.objects.create(title='Legada', content_type='video', event=self.event, template_item=self.item, due_date=deadline)
        self.move()
        content.refresh_from_db()
        self.assertEqual(content.due_date, deadline)
        self.assertFalse(content.due_date_auto)

    def test_event_string_date_from_admin_recalculates(self):
        content = self.generate()
        self.event.event_date = '2026-12-01'
        self.event.save()
        content.refresh_from_db()
        self.assertEqual(content.due_date.date(), datetime.date(2026, 11, 1))

    def test_partial_event_save_does_not_recalculate_unsaved_date(self):
        content = self.generate()
        before = content.due_date
        self.event.event_date = datetime.date(2027, 1, 1)
        self.event.location = 'Novo local'
        self.event.save(update_fields=['location'])
        content.refresh_from_db()
        self.assertEqual(content.due_date, before)

    def test_delete_unlinks_events_and_removes_type_from_creation(self):
        content = self.generate()
        response = self.client.post(reverse('media_event_type_delete', args=[self.kind.pk]))
        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        content.refresh_from_db()
        self.assertIsNone(self.event.event_type_id)
        self.assertIsNone(content.template_item_id)
        self.assertFalse(MediaEventType.all_objects.filter(pk=self.kind.pk).exists())
        self.assertFalse(MediaPlanningTemplate.all_objects.filter(pk=self.template.pk).exists())
        with self.assertRaises(ValidationError):
            apply_template_to_event(self.event, [self.item])
        self.assertNotContains(self.client.get(reverse('media_event_quick_create')), '<option value="%s">Conferência de operações</option>' % self.kind.pk, html=True)

    def test_delete_without_template(self):
        kind = MediaEventType.objects.create(name='Sem template')
        pk = kind.pk
        self.assertEqual(self.client.post(reverse('media_event_type_delete', args=[pk])).status_code, 302)
        self.assertFalse(MediaEventType.all_objects.filter(pk=pk).exists())

    def test_recreate_event_type_with_same_name_after_delete(self):
        pk = self.kind.pk
        self.client.post(reverse('media_event_type_delete', args=[pk]))
        response = self.client.post(reverse('media_template_create'), {
            'name': 'Conferência de operações',
            'description': 'Nova descrição',
            'is_active': 'on',
            'sort_order': '0',
        })
        self.assertEqual(response.status_code, 302)
        recreated = MediaEventType.objects.get(name='Conferência de operações')
        self.assertNotEqual(recreated.pk, pk)
        self.assertEqual(recreated.description, 'Nova descrição')

    def test_creating_event_type_returns_to_event_type_list(self):
        response = self.client.post(reverse('media_template_create'), {
            'name': 'Novo tipo',
            'description': 'Descrição',
            'is_active': 'on',
            'sort_order': '0',
        })
        self.assertRedirects(response, reverse('media_event_type_list'))

    def test_create_event_applies_template_automatically(self):
        response = self.client.post(reverse('media_event_quick_create'), {
            'event-title': 'Novo evento',
            'event-event_date': '2026-12-25',
            'event-event_type': self.kind.pk,
        })
        self.assertEqual(response.status_code, 302)
        event = Event.objects.get(title='Novo evento')
        self.assertEqual(event.media_contents.count(), 1)
        self.assertEqual(event.media_contents.get().due_date.date(), datetime.date(2026, 11, 25))

    def test_create_event_with_multiple_dates(self):
        response = self.client.post(reverse('media_event_quick_create'), {
            'event-title': 'Conferência 3 dias',
            'event-event_date': '2026-12-10',
            'event-extra_event_date': ['2026-12-11', '2026-12-12'],
            'event-extra_event_time': ['', '19:30'],
        })
        self.assertEqual(response.status_code, 302)
        event = Event.objects.get(title='Conferência 3 dias')
        self.assertEqual(event.event_date, datetime.date(2026, 12, 10))
        self.assertEqual(event.end_date, datetime.date(2026, 12, 12))
        self.assertEqual(event.display_start, datetime.date(2026, 12, 10))
        self.assertEqual(event.display_end, datetime.date(2026, 12, 12))
        extra = list(event.dates.order_by('event_date'))
        self.assertEqual(len(extra), 2)
        self.assertEqual(extra[0].event_date, datetime.date(2026, 12, 11))
        self.assertEqual(extra[1].event_time.strftime('%H:%M'), '19:30')

    def test_invalid_event_end_retains_input_without_writes(self):
        response = self.client.post(reverse('media_event_quick_create'), {'event-title': 'Não criar', 'event-event_date': '2026-12-25', 'event-end_date': '2026-12-24'})
        self.assertContains(response, 'Não criar', status_code=400)
        self.assertFalse(Event.objects.filter(title='Não criar').exists())

    def test_event_edit_recalculates_and_preserves_public_display_dates(self):
        content = self.generate()
        response = self.client.post(reverse('media_event_update', args=[self.event.pk]), {'event-title': self.event.title, 'event-event_date': '2026-11-15'})
        self.assertEqual(response.status_code, 302)
        content.refresh_from_db()
        self.event.refresh_from_db()
        self.assertEqual(content.due_date.date(), datetime.date(2026, 10, 16))
        self.assertEqual(self.event.display_start, datetime.date(2026, 10, 1))

    def test_demand_listing_includes_event_and_standalone_filters_tasks(self):
        content = self.generate()
        free = MediaContent.objects.create(title='Avulsa', content_type='video')
        MediaTask.objects.create(title='Responsável', content=free, assigned_to=self.user)
        response = self.client.get(reverse('media_demands'))
        self.assertContains(response, content.title)
        self.assertContains(response, free.title)
        response = self.client.get(reverse('media_demands'), {'responsible': self.user.pk})
        self.assertContains(response, free.title)
        self.assertNotContains(response, content.title)
        response = self.client.get(reverse('media_demands'), {'event': self.event.pk, 'team': self.team.pk, 'status': 'pending', 'due_to': '2026-09-15'})
        self.assertEqual(list(response.context['page_obj']), [content])

    def test_deleted_demand_excluded_from_event_progress(self):
        self.generate().delete()
        response = self.client.get(reverse('media_events'))
        self.assertEqual(response.context['page_obj'][0].demand_count, 0)

    def test_calendar_deadlines_and_valid_detail_links(self):
        content = self.generate()
        response = self.client.get(reverse('media_calendar_events'), {'start': '2026-09-01', 'end': '2026-11-01'})
        self.assertEqual(response.status_code, 200)
        deadline = next(row for row in response.json() if row['id'] == f'deadline-{content.pk}')
        self.assertTrue(deadline['extendedProps']['automatic'])
        self.assertContains(self.client.get(deadline['url']), content.title)
        self.assertEqual(self.client.get(reverse('media_calendar_events'), {'start': 'nonsense'}).status_code, 400)

    def test_task_calendar_scope_lists_all_task_steps(self):
        content = self.generate()
        sync_content_assignments(content, [
            {'role': 'Designer', 'due_offset_days': -30},
            {'role': 'Revisor', 'due_offset_days': -25},
        ])
        tasks = list(content.tasks.order_by('sort_order', 'pk'))
        tasks[0].status = 'completed'
        tasks[0].save(update_fields=['status'])
        response = self.client.get(reverse('media_calendar_events'), {
            'start': '2026-09-01',
            'end': '2026-11-01',
            'scope': 'tasks',
        })
        self.assertEqual(response.status_code, 200)
        rows = response.json()
        self.assertEqual(len(rows), 2)
        completed = next(row for row in rows if row['id'] == f'task-{tasks[0].pk}')
        self.assertEqual(completed['extendedProps']['status'], 'completed')
        self.assertEqual(completed['extendedProps']['taskTitle'], 'Designer')
        self.assertEqual(completed['extendedProps']['contentTitle'], content.title)
        self.assertIn('contentUrl', completed['extendedProps'])

    def test_media_calendar_dashboard_renders(self):
        self.generate()
        response = self.client.get(reverse('media_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'media-task-calendar')
        self.assertContains(response, 'Calendário')

    def test_calendar_invalid_json_or_dates_and_manual_reschedule(self):
        content = self.generate()
        url = reverse('media_calendar_update', args=[content.pk])
        for body in ('null', '[]', '{"publication_date": "oops"}', '{}'):
            self.assertEqual(self.client.post(url, body, content_type='application/json').status_code, 400)
        response = self.client.post(url, '{"publication_date":"2026-10-20T12:00:00-03:00"}', content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.move()
        content.refresh_from_db()
        self.assertFalse(content.publication_date_auto)
        self.assertEqual(content.publication_date.date(), datetime.date(2026, 10, 20))

    def test_template_item_form_validates_assignment_offsets(self):
        data = {
            'template-title': 'Arte',
            'template-content_type': 'artwork',
            'template-sort_order': '0',
            'template-assignment_role': ['Designer'],
            'template-assignment_due_days': ['7'],
            'template-assignment_due_relation': ['before'],
        }
        form = MediaPlanningTemplateItemForm(data, prefix='template')
        self.assertTrue(form.is_valid(), form.errors)

    def test_template_item_steps_apply_as_tasks_in_form_order(self):
        item = MediaPlanningTemplateItem.objects.create(
            template=self.template, title='Com etapas', content_type='artwork',
        )
        sync_template_item_assignments(item, [
            {'role': 'Designer', 'due_offset_days': -14, 'description': 'Briefing', 'task_id': None},
            {'role': 'Publicador', 'due_offset_days': 0, 'description': '', 'task_id': None},
        ])
        content = apply_template_to_event(self.event, [item])[0]
        tasks = list(content.tasks.order_by('sort_order', 'pk'))
        self.assertEqual([t.title for t in tasks], ['Designer', 'Publicador'])
        self.assertEqual(tasks[0].due_offset_days, -14)
        self.assertEqual(tasks[0].description, 'Briefing')
        self.assertIsNone(content.due_date)
        self.assertFalse(content.due_date_auto)
        self.move()
        tasks[0].refresh_from_db()
        self.assertEqual(tasks[0].due_date.date(), datetime.date(2026, 11, 1))

    def test_read_pages_render_with_existing_event(self):
        self.generate()
        for name, args in [('media_events', []), ('media_demands', []), ('media_calendar', []), ('media_template_detail', [self.template.pk]), ('media_event_type_list', []), ('media_event_update', [self.event.pk])]:
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200)

    def test_template_item_edit_opens_modal_on_detail_page(self):
        url = reverse('media_template_detail', args=[self.template.pk]) + f'?edit={self.item.pk}'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Editar demanda padrão')
        self.assertContains(response, 'Salvar alterações')
        self.assertEqual(self.client.get(reverse('media_template_item_edit', args=[self.item.pk])).status_code, 302)

    def test_non_media_member_cannot_read_operations(self):
        user = User.objects.create_user('outside@example.test', password='test')
        Member.objects.create(name='Visitante', user=user)
        self.client.force_login(user)
        for route in ('media_events', 'media_demands', 'media_calendar_events'):
            self.assertEqual(self.client.get(reverse(route)).status_code, 302)

    def test_member_can_edit_event_demand_assignments(self):
        MinistryMembership.objects.filter(member=self.member).update(role='member')
        content = self.generate()
        user = User.objects.create_user('editor@example.test', password='test')
        editor = Member.objects.create(name='Editor', user=user)
        MinistryMembership.objects.create(ministry=self.ministry, member=editor, role='member')
        response = self.client.post(reverse('media_demand_quick_update', args=[content.pk]), {
            'edit-title': content.title,
            'edit-content_type': content.content_type,
            'edit-sub_team': str(self.team.pk),
            'edit-assignment_role': ['Designer'],
            'edit-assignment_user': [str(user.pk)],
            'edit-assignment_due_days': ['7'],
            'edit-assignment_due_relation': ['before'],
        })
        self.assertEqual(response.status_code, 302)
        content.refresh_from_db()
        task = content.tasks.get()
        self.assertEqual(task.title, 'Designer')
        self.assertEqual(task.assigned_to_id, user.pk)

    def test_member_cannot_change_event_dates(self):
        MinistryMembership.objects.filter(member=self.member).update(role='member')
        response = self.client.post(reverse('media_event_update', args=[self.event.pk]), {'event-title': 'Não alterar', 'event-event_date': '2027-01-01'})
        self.assertEqual(response.status_code, 302)
        self.event.refresh_from_db()
        self.assertNotEqual(self.event.title, 'Não alterar')

    def test_edit_event_populates_html_date_value(self):
        response = self.client.get(reverse('media_event_update', args=[self.event.pk]))
        self.assertContains(response, 'value="2026-10-15"')

    def test_cleared_generated_deadline_not_restored_by_steps(self):
        content = self.generate()
        content.due_date = None
        content.save()
        sync_content_assignments(content, [{'role': 'Editar', 'due_offset_days': -7}])
        content.refresh_from_db()
        self.assertIsNone(content.due_date)

    def test_removing_template_item_preserves_generated_demand(self):
        content = self.generate()
        self.item.delete()
        content.refresh_from_db()
        self.assertIsNone(content.deleted)
        self.move()
        content.refresh_from_db()
        self.assertEqual(content.due_date, offset_to_datetime(self.event.event_date, -30))

    def test_invalid_team_rolls_back_all_generated_demands(self):
        ministry = Ministry.objects.create(name='Outra equipe ministerial')
        team = MediaSubTeam.objects.create(name='Outra', ministry=ministry)
        invalid = MediaPlanningTemplateItem.objects.create(template=self.template, title='Inválida', content_type='video', default_sub_team=team)
        with self.assertRaises(ValidationError):
            apply_template_to_event(self.event, [self.item, invalid])
        self.assertFalse(self.event.media_contents.exists())

    def test_multiday_event_overlaps_calendar_period(self):
        self.event.end_date = datetime.date(2026, 10, 20)
        self.event.save(update_fields=['end_date'])
        rows = self.client.get(reverse('media_calendar_events'), {'start': '2026-10-19', 'end': '2026-10-26'}).json()
        event = next(row for row in rows if row['id'] == f'event-{self.event.pk}')
        self.assertEqual(event['end'], '2026-10-21')

    def test_recurring_event_is_excluded_and_rejected_by_media_services(self):
        recurring = Event.objects.create(
            title='Culto semanal', description='Recorrente', is_recurring=True,
            event_date=datetime.date(2026, 10, 18), display_start=datetime.date(2026, 10, 1),
            display_end=datetime.date(2026, 12, 31), event_type=self.kind,
        )
        self.assertNotContains(self.client.get(reverse('media_events')), 'Culto semanal')
        with self.assertRaises(ValidationError):
            require_media_eligible(recurring)
        with self.assertRaises(ValidationError):
            apply_template_to_event(recurring, [self.item])

    def test_institutional_and_media_statuses_are_independent(self):
        self.assertEqual(self.event.institutional_status, Event.INSTITUTIONAL_STATUS_PUBLISHED)
        self.assertFalse(self.event.is_media_organized)
        mark_media_organized(self.event)
        self.event.refresh_from_db()
        self.assertTrue(self.event.is_media_organized)
        self.assertEqual(self.event.institutional_status, Event.INSTITUTIONAL_STATUS_PUBLISHED)

        media_event = Event.objects.create(
            title='Evento da mídia', description='Pendente institucional',
            event_date=datetime.date(2026, 12, 1), display_start=datetime.date(2026, 12, 1),
            display_end=datetime.date(2026, 12, 1),
            institutional_status=Event.INSTITUTIONAL_STATUS_PENDING,
        )
        mark_media_organized(media_event)
        mark_institutional_published(media_event)
        media_event.refresh_from_db()
        self.assertEqual(media_event.institutional_status, Event.INSTITUTIONAL_STATUS_PUBLISHED)
        self.assertTrue(media_event.is_media_organized)

    def test_media_creation_starts_pending_institutional_publication(self):
        response = self.client.post(reverse('media_event_quick_create'), {
            'event-title': 'Evento operacional',
            'event-event_date': '2026-12-28',
        })
        self.assertEqual(response.status_code, 302)
        event = Event.objects.get(title='Evento operacional')
        self.assertEqual(event.institutional_status, Event.INSTITUTIONAL_STATUS_PENDING)
        self.assertEqual(event.media_organization.status, 'pending')

    def test_event_suggestions_exclude_recurring_events(self):
        Event.objects.create(
            title='Encontro recorrente', description='Recorrente', is_recurring=True,
            event_date=datetime.date(2026, 11, 2), display_start=datetime.date(2026, 11, 1),
            display_end=datetime.date(2026, 12, 1),
        )
        response = self.client.get(reverse('media_event_suggestions'), {'q': 'Conferência'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row['id'] for row in response.json()['results']], [self.event.pk])

    def test_removing_event_from_media_preserves_central_event(self):
        content = self.generate()
        mark_media_organized(self.event)
        response = self.client.post(reverse('media_event_remove_from_media', args=[self.event.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())
        self.assertFalse(MediaContent.objects.filter(pk=content.pk).exists())
        self.assertFalse(self.event.month_plans.exists())
        self.event.refresh_from_db()
        self.assertFalse(self.event.is_media_organized)
        from website.services.event_media_integration import media_eligible_events
        self.assertFalse(media_eligible_events().filter(pk=self.event.pk).exists())

    def test_removing_media_created_event_deletes_the_central_event(self):
        response = self.client.post(reverse('media_event_quick_create'), {
            'event-title': 'Evento só da mídia',
            'event-event_date': '2026-12-28',
        })
        self.assertEqual(response.status_code, 302)
        event = Event.objects.get(title='Evento só da mídia')
        response = self.client.post(reverse('media_event_remove_from_media', args=[event.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Event.objects.filter(pk=event.pk).exists())

    def test_event_can_be_edited_from_the_demands_hub_modal(self):
        response = self.client.get(reverse('media_content_list'), {'selected': f'event-{self.event.pk}'})
        self.assertContains(response, 'demands-modal-event-edit')
        response = self.client.post(reverse('media_event_update', args=[self.event.pk]), {
            'event-edit-title': 'Conferência atualizada',
            'event-edit-event_date': '2026-11-20',
            'event-edit-event_time': '19:30',
            'event-edit-location': 'Templo principal',
        })
        self.assertRedirects(response, f"{reverse('media_content_list')}?selected=event-{self.event.pk}")
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, 'Conferência atualizada')
        self.assertEqual(self.event.event_date, datetime.date(2026, 11, 20))

    def test_event_edit_modal_shows_and_updates_additional_dates(self):
        EventDate.objects.create(event=self.event, event_date=datetime.date(2026, 10, 16), event_time=datetime.time(19, 30))
        response = self.client.get(reverse('media_content_list'), {'selected': f'event-{self.event.pk}'})
        self.assertContains(response, 'value="2026-10-16"')
        response = self.client.post(reverse('media_event_update', args=[self.event.pk]), {
            'event-edit-title': self.event.title,
            'event-edit-event_date': '2026-10-15',
            'event-edit-event_time': '19:00',
            'event-edit-extra_event_date': ['2026-10-16', '2026-10-17'],
            'event-edit-extra_event_time': ['19:30', '20:00'],
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(list(self.event.dates.values_list('event_date', flat=True)), [
            datetime.date(2026, 10, 16), datetime.date(2026, 10, 17),
        ])

    def test_event_demand_edit_uses_absolute_task_dates(self):
        content = self.generate()
        sync_content_assignments(content, [{'role': 'Designer', 'due_offset_days': -30}])
        task = content.tasks.get()
        response = self.client.get(reverse('media_content_list'), {'selected': f'event-{self.event.pk}', 'edit': '1'})
        self.assertContains(response, 'name="edit-assignment_due_date"')
        response = self.client.post(reverse('media_demand_quick_update', args=[content.pk]), {
            'edit-title': content.title,
            'edit-content_type': content.content_type,
            'edit-assignment_id': [str(task.pk)],
            'edit-assignment_role': ['Designer'],
            'edit-assignment_user': [''],
            'edit-assignment_due_date': ['2026-09-25'],
            'edit-assignment_desc': ['Data confirmada'],
        })
        self.assertEqual(response.status_code, 302)
        task.refresh_from_db()
        self.assertIsNone(task.due_offset_days)
        self.assertEqual(task.due_date.date(), datetime.date(2026, 9, 25))
        self.move()
        task.refresh_from_db()
        self.assertEqual(task.due_date.date(), datetime.date(2026, 9, 25))

    def test_event_demand_can_be_deleted_without_deleting_event(self):
        content = self.generate()
        response = self.client.post(reverse('media_content_delete', args=[content.pk]), {
            'next': f"{reverse('media_content_list')}?selected=event-{self.event.pk}",
        })
        self.assertRedirects(response, f"{reverse('media_content_list')}?selected=event-{self.event.pk}")
        self.assertFalse(MediaContent.objects.filter(pk=content.pk).exists())
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())


class MediaOperationsMigrationTests(TransactionTestCase):
    def test_existing_dates_and_links_remain_manual(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        previous = ('website', '0103_ministry_organization')
        try:
            executor.migrate([previous])
            apps = executor.loader.project_state([previous]).apps
            old_event = apps.get_model('website', 'Event').objects.create(title='Antigo', description='Antigo', slug='antigo', event_date='2026-10-01', display_start='2026-10-01', display_end='2026-10-01')
            deadline = offset_to_datetime(datetime.date(2026, 9, 1), 0)
            old_content = apps.get_model('website', 'MediaContent').objects.create(title='Antiga', content_type='artwork', event_id=old_event.pk, due_date=deadline)
            MigrationExecutor(connection).migrate(latest)
            content = MediaContent.objects.get(pk=old_content.pk)
            self.assertEqual(content.event_id, old_event.pk)
            self.assertEqual(content.due_date, deadline)
            self.assertFalse(content.due_date_auto)
            self.assertIsNone(content.due_offset_days)
        finally:
            MigrationExecutor(connection).migrate(latest)
