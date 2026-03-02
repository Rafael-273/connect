from django.test import TestCase, Client
from django.core.exceptions import ValidationError
from datetime import date
from website.models.member import Member
from website.models.ministry import Ministry
from website.models.schedule import Schedule
from website.models.user import User


class ScheduleConflictTests(TestCase):
    """Testes para detecção de conflito de escala."""

    def setUp(self):
        self.ministry_a = Ministry.objects.create(name='Louvor')
        self.ministry_b = Ministry.objects.create(name='Infantil')
        self.member = Member.objects.create(name='João Silva')
        self.admin_user = User.objects.create_superuser(
            email='admin@test.com', password='testpass123'
        )
        self.schedule_date = date(2026, 3, 15)

    def test_no_conflict_different_dates(self):
        """Escalas em datas diferentes não devem gerar conflito."""
        Schedule.objects.create(
            member=self.member, ministry=self.ministry_a,
            scheduled_date=date(2026, 3, 14)
        )
        s = Schedule(
            member=self.member, ministry=self.ministry_b,
            scheduled_date=date(2026, 3, 15)
        )
        conflicts = s.get_conflicts()
        self.assertFalse(conflicts.exists())

    def test_no_conflict_same_ministry(self):
        """Escala no mesmo ministério e mesma data (edição) não deve gerar conflito."""
        s = Schedule.objects.create(
            member=self.member, ministry=self.ministry_a,
            scheduled_date=self.schedule_date
        )
        s2 = Schedule(
            member=self.member, ministry=self.ministry_a,
            scheduled_date=self.schedule_date
        )
        conflicts = s2.get_conflicts()
        self.assertFalse(conflicts.exists())

    def test_conflict_detected(self):
        """Escala no mesmo dia em ministério diferente deve gerar conflito."""
        Schedule.objects.create(
            member=self.member, ministry=self.ministry_a,
            scheduled_date=self.schedule_date
        )
        s = Schedule(
            member=self.member, ministry=self.ministry_b,
            scheduled_date=self.schedule_date
        )
        conflicts = s.get_conflicts()
        self.assertTrue(conflicts.exists())
        self.assertEqual(conflicts.first().ministry, self.ministry_a)

    def test_clean_raises_on_conflict_without_override(self):
        """clean() deve levantar ValidationError se conflito sem override."""
        Schedule.objects.create(
            member=self.member, ministry=self.ministry_a,
            scheduled_date=self.schedule_date
        )
        s = Schedule(
            member=self.member, ministry=self.ministry_b,
            scheduled_date=self.schedule_date
        )
        with self.assertRaises(ValidationError):
            s.clean()

    def test_clean_allows_override(self):
        """clean() deve permitir salvar com override_conflict=True."""
        Schedule.objects.create(
            member=self.member, ministry=self.ministry_a,
            scheduled_date=self.schedule_date
        )
        s = Schedule(
            member=self.member, ministry=self.ministry_b,
            scheduled_date=self.schedule_date,
            override_conflict=True,
            override_by=self.admin_user,
        )
        # Should not raise
        s.clean()
        s.save()
        self.assertTrue(s.override_conflict)
        self.assertEqual(s.override_by, self.admin_user)

    def test_conflict_api_returns_conflict(self):
        """API de verificação de conflito retorna dados corretos."""
        Schedule.objects.create(
            member=self.member, ministry=self.ministry_a,
            scheduled_date=self.schedule_date
        )
        client = Client()
        client.force_login(self.admin_user)
        import json
        response = client.post(
            '/admin-panel/api/schedule/check-conflict/',
            data=json.dumps({
                'member_id': self.member.id,
                'ministry_id': self.ministry_b.id,
                'scheduled_date': str(self.schedule_date),
            }),
            content_type='application/json',
        )
        data = response.json()
        self.assertTrue(data['has_conflict'])
        self.assertEqual(len(data['conflicts']), 1)
        self.assertEqual(data['conflicts'][0]['ministry'], 'Louvor')

    def test_conflict_api_no_conflict(self):
        """API retorna sem conflito quando não há."""
        client = Client()
        client.force_login(self.admin_user)
        import json
        response = client.post(
            '/admin-panel/api/schedule/check-conflict/',
            data=json.dumps({
                'member_id': self.member.id,
                'ministry_id': self.ministry_a.id,
                'scheduled_date': str(self.schedule_date),
            }),
            content_type='application/json',
        )
        data = response.json()
        self.assertFalse(data['has_conflict'])

    def test_schedule_str(self):
        """__str__ deve retornar formato esperado."""
        s = Schedule.objects.create(
            member=self.member, ministry=self.ministry_a,
            scheduled_date=self.schedule_date
        )
        self.assertIn('João Silva', str(s))
        self.assertIn('Louvor', str(s))
