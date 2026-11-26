"""Защитные настройки; запуск HTTPS-инфраструктуры требует отдельной проверки."""

import os

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

DEBUG = False
DEVELOPMENT_MODE = False
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")  # noqa: F405
if not ALLOWED_HOSTS or "*" in ALLOWED_HOSTS:
    raise ImproperlyConfigured("Для HTTPS укажите явный DJANGO_ALLOWED_HOSTS")
if len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5:  # noqa: F405
    raise ImproperlyConfigured("Для HTTPS требуется случайный SECRET_KEY длиной не менее 50")
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS")  # noqa: F405
if any(not origin.startswith("https://") for origin in CSRF_TRUSTED_ORIGINS):
    raise ImproperlyConfigured("CSRF_TRUSTED_ORIGINS в защищённом режиме допускает только HTTPS")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
# Доверять заголовку можно только при недоступности app извне и его перезаписи Nginx.
# По умолчанию доверие отключено; HTTPS-прокси задаёт DJANGO_TRUST_PROXY=1 явно.
if os.environ.get("DJANGO_TRUST_PROXY") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
