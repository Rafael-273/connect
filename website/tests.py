from django.test import TestCase, override_settings
from django.urls import reverse
from django.core import mail
from django.contrib.auth.tokens import default_token_generator
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import force_bytes

from website.models.user import User


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    SITE_DOMAIN='testserver',
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
)
class PasswordResetRequestTests(TestCase):
    """Tests for objective 3: password reset request flow"""

    def setUp(self):
        self.user = User.objects.create_user(
            email='membro@example.com',
            password='senha123',
        )
        self.url = reverse('password_reset_request')

    def test_request_page_loads(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_valid_email_sends_reset_email(self):
        response = self.client.post(self.url, {'email': 'membro@example.com'}, follow=True)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('membro@example.com', mail.outbox[0].to)

    def test_reset_email_contains_reset_link(self):
        self.client.post(self.url, {'email': 'membro@example.com'})
        self.assertEqual(len(mail.outbox), 1)
        email_body = mail.outbox[0].body
        self.assertIn('password-reset/confirm/', email_body)

    def test_reset_link_uses_site_domain(self):
        self.client.post(self.url, {'email': 'membro@example.com'})
        self.assertEqual(len(mail.outbox), 1)
        # The HTML email body should use testserver as the domain
        html_body = mail.outbox[0].alternatives[0][0] if mail.outbox[0].alternatives else mail.outbox[0].body
        self.assertIn('testserver', html_body)

    def test_unknown_email_does_not_raise_error(self):
        response = self.client.post(self.url, {'email': 'desconhecido@example.com'})
        # Django silently ignores unknown emails; no exception, redirects to done page
        self.assertIn(response.status_code, [200, 302])
        self.assertEqual(len(mail.outbox), 0)

    def test_successful_request_redirects_to_done(self):
        response = self.client.post(self.url, {'email': 'membro@example.com'})
        self.assertRedirects(response, reverse('password_reset_done'))


@override_settings(
    EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    SITE_DOMAIN='testserver',
    STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage',
)
class PasswordResetConfirmTests(TestCase):
    """Tests for objective 4: password reset confirm form"""

    def setUp(self):
        self.user = User.objects.create_user(
            email='membro@example.com',
            password='senha_antiga123',
        )
        self.uid = urlsafe_base64_encode(force_bytes(self.user.pk))
        self.token = default_token_generator.make_token(self.user)
        self.confirm_url = reverse(
            'password_reset_confirm',
            kwargs={'uidb64': self.uid, 'token': self.token},
        )

    def test_confirm_page_loads_with_valid_token(self):
        # Django redirects the first GET to set the token in session
        response = self.client.get(self.confirm_url, follow=True)
        self.assertEqual(response.status_code, 200)

    def test_confirm_page_shows_error_for_invalid_token(self):
        bad_url = reverse(
            'password_reset_confirm',
            kwargs={'uidb64': self.uid, 'token': 'invalid-token'},
        )
        response = self.client.get(bad_url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'inválido')

    def test_valid_new_password_resets_successfully(self):
        # First GET to set session token (Django 4+ uses session-based token)
        response = self.client.get(self.confirm_url, follow=True)
        # POST new passwords
        post_url = response.redirect_chain[-1][0] if response.redirect_chain else self.confirm_url
        response = self.client.post(post_url, {
            'new_password1': 'nova_senha_forte123',
            'new_password2': 'nova_senha_forte123',
        })
        # Should redirect to complete page
        self.assertIn(response.status_code, [200, 302])
        # Verify password was changed
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('nova_senha_forte123'))

    def test_complete_page_links_to_login(self):
        complete_url = reverse('password_reset_complete')
        response = self.client.get(complete_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('member_login'))

    def test_mismatched_passwords_show_error(self):
        # Follow the GET redirect first
        response = self.client.get(self.confirm_url, follow=True)
        post_url = response.redirect_chain[-1][0] if response.redirect_chain else self.confirm_url
        response = self.client.post(post_url, {
            'new_password1': 'nova_senha_forte123',
            'new_password2': 'senha_diferente456',
        })
        self.assertEqual(response.status_code, 200)
        # Password must NOT have changed
        self.user.refresh_from_db()
        self.assertFalse(self.user.check_password('nova_senha_forte123'))
