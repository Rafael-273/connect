from django.test import TestCase
from django.urls import reverse

from website.models import Member, Ministry, MinistryMembership, User


class MemberMinistryPagesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('ministry-pages@example.test', password='test')
        cls.member = Member.objects.create(user=cls.user, name='Teste Páginas')
        cls.media, _ = Ministry.objects.get_or_create(
            code='midia_externa', defaults={'name': 'Mídia Externa'},
        )
        cls.welcome, _ = Ministry.objects.get_or_create(name='Boas Vindas')
        cls.media_membership = MinistryMembership.objects.create(
            member=cls.member, ministry=cls.media, role='leader',
        )
        MinistryMembership.objects.create(member=cls.member, ministry=cls.welcome)

    def setUp(self):
        self.client.force_login(self.user)

    def detail(self, ministry):
        return self.client.get(reverse('member_ministry_detail', args=[ministry.pk]))

    def test_home_uses_its_own_template_without_ministry_tools(self):
        response = self.client.get(reverse('member_dashboard'))
        self.assertTemplateUsed(response, 'member/dashboard.html')
        self.assertTemplateNotUsed(response, 'member/ministries/tools/media.html')
        self.assertContains(response, reverse('member_ministry_detail', args=[self.media.pk]))
        self.assertNotContains(response, 'ministry-cards-container')

    def test_media_renders_only_media_tools_on_server(self):
        response = self.detail(self.media)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'member/ministries/media.html')
        self.assertTemplateNotUsed(response, 'member/dashboard.html')
        self.assertTemplateNotUsed(response, 'member/ministries/tools/welcome.html')
        self.assertContains(response, reverse('external_media_dashboard'))
        self.assertContains(response, reverse('media_content_list'))
        self.assertNotContains(response, reverse('member_visitor_create'))
        self.assertNotContains(response, 'Acesso autorizado')
        self.assertNotContains(response, 'ministry-tab')

    def test_welcome_has_a_separate_template(self):
        response = self.detail(self.welcome)
        self.assertTemplateUsed(response, 'member/ministries/welcome.html')
        self.assertContains(response, reverse('member_visitor_create'))
        self.assertNotContains(response, reverse('external_media_dashboard'))

    def test_media_volunteer_does_not_see_leader_tools(self):
        self.media_membership.role = 'member'
        self.media_membership.save()
        response = self.detail(self.media)
        self.assertContains(response, reverse('external_media_dashboard'))
        self.assertNotContains(response, reverse('media_content_list'))

    def test_detail_rejects_missing_or_inactive_membership(self):
        other = Ministry.objects.create(name='Sem vínculo')
        self.assertEqual(self.detail(other).status_code, 404)
        self.media_membership.is_active = False
        self.media_membership.save()
        self.assertEqual(self.detail(self.media).status_code, 404)

    def test_inactive_ministry_is_not_accessible(self):
        self.media.is_active = False
        self.media.save()
        self.assertEqual(self.detail(self.media).status_code, 404)

    def test_index_lists_links_without_rendering_tools(self):
        response = self.client.get(reverse('member_ministries'))
        self.assertTemplateUsed(response, 'member/ministries/index.html')
        self.assertContains(response, reverse('member_ministry_detail', args=[self.media.pk]))
        self.assertNotContains(response, reverse('external_media_dashboard'))

    def test_legacy_query_redirects_to_individual_page(self):
        response = self.client.get(reverse('member_ministries'), {'ministry': self.media.pk})
        self.assertRedirects(response, reverse('member_ministry_detail', args=[self.media.pk]))

    def test_unknown_ministry_has_a_useful_empty_page(self):
        ministry = Ministry.objects.create(name='Novo ministério')
        MinistryMembership.objects.create(member=self.member, ministry=ministry)
        response = self.detail(ministry)
        self.assertTemplateUsed(response, 'member/ministries/generic.html')
        self.assertContains(response, 'Novo ministério')

    def test_detail_requires_login(self):
        self.client.logout()
        response = self.detail(self.media)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('member_login'), response.url)
