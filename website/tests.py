from django.test import TestCase
from django.urls import reverse
from .models.evangelism import Evangelized
from .models.neighborhood import Neighborhood


class EvangelismCreateViewTests(TestCase):
    def setUp(self):
        self.neighborhood = Neighborhood.objects.create(name='Centro')
        self.url = reverse('evangelism')

    def test_get_returns_200(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_uses_correct_template(self):
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, 'create/evangelism.html')

    def test_form_in_context(self):
        response = self.client.get(self.url)
        self.assertIn('form', response.context)

    def test_post_creates_evangelized(self):
        data = {
            'name': 'João Silva',
            'gender': 'male',
            'phone': '21999999999',
            'address': 'Rua Teste, 123',
            'neighborhood': self.neighborhood.pk,
            'evangelized_by': '',
            'conversion': 'new_convert',
            'prayer_request': '',
            'profile_notes': '',
        }
        response = self.client.post(self.url, data)
        self.assertEqual(Evangelized.objects.count(), 1)
        self.assertRedirects(response, reverse('home'))

    def test_post_invalid_missing_name(self):
        data = {
            'name': '',
            'conversion': 'new_convert',
        }
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Evangelized.objects.count(), 0)
        self.assertFormError(response, 'form', 'name', 'Este campo é obrigatório.')


class EvangelizedAdminTests(TestCase):
    def setUp(self):
        from .models.user import User
        self.admin_user = User.objects.create_superuser(
            email='admin@test.com',
            password='admin123'
        )
        self.client.login(username='admin@test.com', password='admin123')

    def test_evangelized_in_admin(self):
        response = self.client.get('/admin/website/evangelized/')
        self.assertEqual(response.status_code, 200)

    def test_evangelized_admin_create(self):
        neighborhood = Neighborhood.objects.create(name='Bairro Teste')
        data = {
            'name': 'Maria Souza',
            'gender': 'female',
            'phone': '21988887777',
            'address': '',
            'neighborhood': neighborhood.pk,
            'evangelized_by': '',
            'conversion': 'reconciled',
            'prayer_request': '',
            'profile_notes': '',
        }
        response = self.client.post('/admin/website/evangelized/add/', data)
        self.assertEqual(Evangelized.objects.count(), 1)

