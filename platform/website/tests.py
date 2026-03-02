from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from .models.user import User
from .models.member import Member
from .models.ministry import Ministry
from .models.escala import Escala


class EscalaModelTest(TestCase):
    def setUp(self):
        self.ministry = Ministry.objects.create(name='Louvor')
        self.user = User.objects.create_user(email='membro@test.com', password='senha123')
        self.member = Member.objects.create(name='Membro Teste', user=self.user)
        self.member.ministry.add(self.ministry)

    def test_criar_escala(self):
        escala = Escala.objects.create(
            title='Escala de Louvor',
            date=timezone.now().date(),
            ministry=self.ministry,
        )
        escala.members.add(self.member)
        self.assertEqual(str(escala), f'Escala de Louvor - Louvor ({timezone.now().date()})')
        self.assertIn(self.member, escala.members.all())

    def test_escala_sem_membros(self):
        escala = Escala.objects.create(
            title='Escala Vazia',
            date=timezone.now().date(),
            ministry=self.ministry,
        )
        self.assertEqual(escala.members.count(), 0)


class MinhasEscalasViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.ministry = Ministry.objects.create(name='Louvor')
        self.other_ministry = Ministry.objects.create(name='Intercessão')

        self.user = User.objects.create_user(email='membro@test.com', password='senha123')
        self.member = Member.objects.create(name='Membro Teste', user=self.user)
        self.member.ministry.add(self.ministry)

        self.other_user = User.objects.create_user(email='outro@test.com', password='senha123')
        self.other_member = Member.objects.create(name='Outro Membro', user=self.other_user)
        self.other_member.ministry.add(self.other_ministry)

        self.escala_propria = Escala.objects.create(
            title='Minha Escala',
            date=timezone.now().date(),
            ministry=self.ministry,
        )
        self.escala_propria.members.add(self.member)

        self.escala_outro = Escala.objects.create(
            title='Escala de Outro',
            date=timezone.now().date(),
            ministry=self.other_ministry,
        )
        self.escala_outro.members.add(self.other_member)

    def test_redireciona_nao_autenticado(self):
        response = self.client.get(reverse('minhas_escalas'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/admin-login/', response['Location'])

    def test_acesso_autenticado(self):
        self.client.login(username='membro@test.com', password='senha123')
        response = self.client.get(reverse('minhas_escalas'))
        self.assertEqual(response.status_code, 200)

    def test_exibe_apenas_escalas_do_usuario(self):
        self.client.login(username='membro@test.com', password='senha123')
        response = self.client.get(reverse('minhas_escalas'))
        escalas = list(response.context['escalas'])
        self.assertIn(self.escala_propria, escalas)
        self.assertNotIn(self.escala_outro, escalas)

    def test_nao_exibe_escalas_de_outro_ministerio(self):
        """Usuário não deve ver escalas de ministérios aos quais não pertence."""
        escala_ministerio_diferente = Escala.objects.create(
            title='Escala Outro Ministério',
            date=timezone.now().date(),
            ministry=self.other_ministry,
        )
        escala_ministerio_diferente.members.add(self.member)

        self.client.login(username='membro@test.com', password='senha123')
        response = self.client.get(reverse('minhas_escalas'))
        escalas = list(response.context['escalas'])
        self.assertNotIn(escala_ministerio_diferente, escalas)

    def test_usuario_sem_membro_ve_lista_vazia(self):
        user_sem_membro = User.objects.create_user(email='semembro@test.com', password='senha123')
        self.client.login(username='semembro@test.com', password='senha123')
        response = self.client.get(reverse('minhas_escalas'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['escalas']), [])


class EscalasMinisterioViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.ministry = Ministry.objects.create(name='Louvor')
        self.other_ministry = Ministry.objects.create(name='Intercessão')

        self.user = User.objects.create_user(email='membro@test.com', password='senha123')
        self.member = Member.objects.create(name='Membro Teste', user=self.user)
        self.member.ministry.add(self.ministry)

        self.escala = Escala.objects.create(
            title='Escala Louvor',
            date=timezone.now().date(),
            ministry=self.ministry,
        )
        self.escala.members.add(self.member)

    def test_redireciona_nao_autenticado(self):
        url = reverse('escalas_ministerio', args=[self.ministry.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

    def test_acesso_membro_do_ministerio(self):
        self.client.login(username='membro@test.com', password='senha123')
        url = reverse('escalas_ministerio', args=[self.ministry.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.escala, list(response.context['escalas']))

    def test_bloqueio_ministerio_nao_pertencente(self):
        """Usuário não deve acessar escalas de ministério ao qual não pertence."""
        self.client.login(username='membro@test.com', password='senha123')
        url = reverse('escalas_ministerio', args=[self.other_ministry.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

