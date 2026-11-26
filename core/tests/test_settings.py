"""Проверка загрузки окружения в новом процессе, без БД и изменения текущих settings."""

import os
import secrets
import subprocess
import sys

from django.test import SimpleTestCase


class SettingsTests(SimpleTestCase):
    def run_settings(self, overrides):
        env = os.environ.copy()
        env.update(
            {
                "DJANGO_SETTINGS_MODULE": "config.settings.production",
                "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
                "DJANGO_ALLOWED_HOSTS": "assessment.example.test",
                "DJANGO_CSRF_TRUSTED_ORIGINS": "https://assessment.example.test",
                "POSTGRES_PASSWORD": secrets.token_urlsafe(32),
            }
        )
        env.update(overrides)
        return subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import django; django.setup(); "
                    "from django.conf import settings; "
                    "assert not settings.DEBUG; "
                    "assert settings.SESSION_COOKIE_SECURE; "
                    "assert settings.CSRF_COOKIE_SECURE; "
                    "assert settings.SECURE_SSL_REDIRECT; "
                    "assert settings.DATABASES['default']['ENGINE'] == "
                    "'django.db.backends.postgresql'; "
                    "assert getattr(settings, 'SECURE_PROXY_SSL_HEADER', None) is None"
                ),
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_production_security_defaults(self):
        result = self.run_settings({"DJANGO_TRUST_PROXY": "0"})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_secret_is_rejected(self):
        result = self.run_settings({"DJANGO_SECRET_KEY": ""})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY", result.stderr)

    def test_wildcard_hosts_are_rejected(self):
        result = self.run_settings({"DJANGO_ALLOWED_HOSTS": "*"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("DJANGO_ALLOWED_HOSTS", result.stderr)

    def test_http_csrf_origin_is_rejected(self):
        result = self.run_settings({"DJANGO_CSRF_TRUSTED_ORIGINS": "http://example.test"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CSRF_TRUSTED_ORIGINS", result.stderr)
