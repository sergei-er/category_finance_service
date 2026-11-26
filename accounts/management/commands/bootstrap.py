import sys

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.access import ADMIN
from accounts.roles import setup_roles


class Command(BaseCommand):
    help = "Создать обычного администратора приложения; пароль только через stdin"

    def add_arguments(self, parser):
        parser.add_argument("--username", default="administrator")
        parser.add_argument("--password-stdin", action="store_true", required=True)

    @transaction.atomic
    def handle(self, *args, **options):
        setup_roles()
        username = options["username"]
        if get_user_model().objects.filter(username=username).exists():
            raise CommandError("Пользователь уже существует; пароль не изменён")
        password = sys.stdin.read().strip()
        validate_password(password)
        user = get_user_model().objects.create_user(username=username, password=password)
        user.groups.add(Group.objects.get(name=ADMIN))
        self.stdout.write("Администратор приложения создан. Пароль не выводится.")
