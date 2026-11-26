"""Только HTTP localhost. Этот режим не предназначен для production."""

from .base import *  # noqa: F403

DEBUG = True
DEVELOPMENT_MODE = True
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False
