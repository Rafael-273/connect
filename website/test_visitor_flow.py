from datetime import date, datetime, timezone as datetime_timezone
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from .models.member import Member
from .models.user import User
from .models.visitor import Visitor, visitor_local_date


class VisitorFlowTests(TestCase):
    def test_visit_date_uses_sao_paulo_calendar_day(self):
        with patch(
            'website.models.visitor.timezone.now',
            return_value=datetime(2026, 10, 19, 1, 0, tzinfo=datetime_timezone.utc),
        ):
            self.assertEqual(visitor_local_date(), date(2026, 10, 18))
            visitor = Visitor.objects.create(name='Visitante da Noite')

        self.assertEqual(visitor.visit_date, date(2026, 10, 18))

    def test_member_registration_keeps_member_on_registration_page(self):
        user = User.objects.create_user(email='visitor-flow@test.com')
        Member.objects.create(user=user, name='Membro Teste')
        self.client.force_login(user)
        for number in range(11):
            Visitor.objects.create(name=f'Visitante {number:02d}')

        response = self.client.post(
            reverse('member_visitor_create'),
            {'name': 'Visitante Novo', 'companion': 'alone'},
            follow=True,
        )

        self.assertRedirects(response, reverse('member_visitor_create'))
        self.assertEqual(Visitor.objects.filter(name='Visitante Novo').count(), 1)
        self.assertContains(response, 'Visitante cadastrado com sucesso!')

    def test_admin_pagination_keeps_filters_without_duplicate_page(self):
        user = User.objects.create_superuser(email='visitor-admin@test.com')
        self.client.force_login(user)
        for number in range(21):
            Visitor.objects.create(name=f'Visitante Pagina {number:02d}')

        response = self.client.get(reverse('admin_visitors_list'), {'search': 'Visitante Pagina', 'page': 2})

        self.assertEqual(response.context['visitors'].number, 2)
        self.assertEqual(response.context['filter_query'], 'search=Visitante+Pagina')
        self.assertContains(response, 'search=Visitante+Pagina&amp;page=1')
        self.assertNotContains(response, 'page=1&amp;search=Visitante+Pagina&amp;page=2')

    def test_admin_registration_preserves_entered_visit_date(self):
        user = User.objects.create_superuser(email='visitor-date-admin@test.com')
        self.client.force_login(user)

        response = self.client.post(
            reverse('admin_visitor_create'),
            {'name': 'Visitante com Data', 'visit_date': '2026-10-10'},
        )

        self.assertRedirects(response, reverse('admin_visitors_list'))
        self.assertEqual(Visitor.objects.get(name='Visitante com Data').visit_date, date(2026, 10, 10))
