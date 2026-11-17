"""PWA metadata, root worker and authentication template regressions (no DB)."""
import json
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import resolve

from connect.pwa import service_worker


@override_settings(STATICFILES_STORAGE='django.contrib.staticfiles.storage.StaticFilesStorage')
class PWATests(SimpleTestCase):
    def test_worker_is_served_at_root_without_cache(self):
        self.assertIs(resolve('/sw.js').func, service_worker)
        response = service_worker(RequestFactory().get('/sw.js'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/javascript')
        self.assertEqual(response['Cache-Control'], 'no-cache')
        self.assertEqual(response['Service-Worker-Allowed'], '/')
        self.assertIn(b'filadelfia-connect-v4', response.content)

    def test_worker_rejects_posts(self):
        self.assertEqual(service_worker(RequestFactory().post('/sw.js')).status_code, 405)

    def test_manifest_covers_app_and_matches_shell(self):
        manifest = json.loads((Path(settings.BASE_DIR) / 'static/manifest.json').read_text())
        self.assertEqual(manifest['scope'], '/')
        self.assertEqual(manifest['id'], manifest['start_url'])
        self.assertEqual(manifest['display'], 'standalone')
        self.assertEqual(manifest['theme_color'], '#f7f1ef')
        self.assertEqual(manifest['theme_color'], manifest['background_color'])
        self.assertTrue(manifest['start_url'].startswith('/login/'))

    def test_authentication_pages_share_viewport_and_pwa_assets(self):
        for template in ('member/login.html', 'password_reset/request.html',
                         'password_reset/confirm.html', 'password_reset/complete.html', 'password_reset/email_sent.html',
                         'prosperar/login.html', 'admin_panel/login.html'):
            with self.subTest(template=template):
                html = render_to_string(template, {'csrf_token': 'test'})
                self.assertEqual(html.count('name="viewport"'), 1)
                self.assertIn('viewport-fit=cover', html)
                self.assertIn('interactive-widget=resizes-content', html)
                self.assertIn('pwa.css', html)
                self.assertIn('pwa.js', html)
                self.assertNotIn('-webkit-fill-available', html)
                self.assertNotIn("register('/static/sw.js'", html)

    def test_ios_install_instructions_include_web_app_mode(self):
        for template in ('member/dashboard.html', 'member/profile.html'):
            with self.subTest(template=template):
                source = (Path(settings.BASE_DIR) / 'templates' / template).read_text()
                self.assertIn('"Abrir como App"', source)
                self.assertIn('novo ícone na Tela Inicial', source)
                self.assertIn('remova-o da Tela Inicial', source)
