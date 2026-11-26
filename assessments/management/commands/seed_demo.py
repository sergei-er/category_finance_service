import json
import sys

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.access import ADMIN, EMPLOYEE, REVIEWER
from accounts.roles import setup_roles
from assessments.services import create_assessment
from organization.models import OrganizationProfile, ProtectionContour
from standards.demo import create_demo_catalog, demo_payload
from standards.models import CatalogVersion


class Command(BaseCommand):
    help = "Добавить явно синтетический демонстрационный стенд без замены существующих паролей"

    def add_arguments(self, parser):
        parser.add_argument("--credentials-stdin", action="store_true", required=True)

    @transaction.atomic
    def handle(self, *args, **options):
        setup_roles()
        profile = OrganizationProfile.objects.first()
        if profile and not profile.synthetic:
            raise CommandError("Профиль не синтетический: демонстрационные данные не добавлены")
        credentials = json.load(sys.stdin)
        users = {}
        for key, role in [("admin", ADMIN), ("employee", EMPLOYEE), ("reviewer", REVIEWER)]:
            info = credentials[key]
            validate_password(info["password"])
            user = get_user_model().objects.filter(username=info["username"]).first()
            if user:
                if (
                    not user.check_password(info["password"])
                    or not user.groups.filter(name=role).exists()
                ):
                    raise CommandError(
                        "Существующая учётная запись не изменяется; используйте отдельные имена"
                    )
            else:
                user = get_user_model().objects.create_user(
                    username=info["username"], password=info["password"]
                )
                user.groups.add(Group.objects.get(name=role))
            users[key] = user
        if not profile:
            profile = OrganizationProfile.objects.create(
                name="Синтетический банк «Учебный»",
                description="Демонстрационный профиль, не реальная организация",
                synthetic=True,
            )
        contour, _ = ProtectionContour.objects.get_or_create(
            name="Демонстрационный контур",
            defaults={
                "organization": profile,
                "level": 2,
                "description": "Синтетическая область для проверки полного сценария",
            },
        )
        catalog = CatalogVersion.objects.filter(
            title=demo_payload()["title"]
        ).first() or create_demo_catalog(users["admin"])
        title = "Учебное обследование: демонстрация полного цикла"
        from assessments.models import Assessment

        if not Assessment.objects.filter(title=title).exists():
            today = timezone.localdate()
            assessment = create_assessment(
                users["admin"],
                title=title,
                contours=[contour],
                employee=users["employee"],
                reviewer=users["reviewer"],
                catalog=catalog,
                period_start=today,
                period_end=today,
                scope="Все восемь процессов учебного набора; не нормативная оценка",
                processes=list(range(1, 9)),
                lifecycle_included=True,
            )
        self.stdout.write("Синтетический стенд подготовлен; учётные данные не выводятся.")
