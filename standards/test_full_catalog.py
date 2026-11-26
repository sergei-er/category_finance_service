"""Интеграция локального полного пакета; нормативный текст не хранится в тестах.

Все записи и имитация верификации создаются Django TestCase в отдельной тестовой
БД. Успех тестов подтверждает перенос и защитные проверки, а не точность OCR.
Без private JSON класс явно пропускается; FULL_CATALOG_PATH задаёт иной путь.
"""

import json
import os
from collections import Counter
from datetime import date
from pathlib import Path
from unittest import SkipTest

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.http import QueryDict
from django.test import TestCase
from django.urls import reverse

from accounts.access import ADMIN, EMPLOYEE, REVIEWER
from accounts.roles import setup_roles
from assessments import services
from assessments.models import Assessment
from core.matrix import matrix_context
from organization.models import OrganizationProfile, ProtectionContour

from .models import CatalogVersion, ImplementationRequirement
from .services import import_catalog


class FullCatalogIntegrationTests(TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(
            os.environ.get(
                "FULL_CATALOG_PATH",
                settings.BASE_DIR / "local-catalogs/gost-57580.1-ed2020/catalog.json",
            )
        )
        if not path.is_file():
            raise SkipTest(
                "Нет локального полного каталога; задайте FULL_CATALOG_PATH "
                "или подготовьте local-catalogs/gost-57580.1-ed2020/catalog.json"
            )
        cls.payload = json.loads(path.read_text(encoding="utf-8"))
        super().setUpClass()

    @classmethod
    def setUpTestData(cls):
        setup_roles()
        for name, role in [("admin", ADMIN), ("employee", EMPLOYEE), ("reviewer", REVIEWER)]:
            user = get_user_model().objects.create_user(f"full-catalog-{name}")
            user.groups.add(Group.objects.get(name=role))
            setattr(cls, name, user)
        cls.profile = OrganizationProfile.objects.create(
            name="Тест интеграции каталога", synthetic=True
        )
        cls.contours = {
            level: ProtectionContour.objects.create(
                organization=cls.profile, name=f"Тестовый контур {level}", level=level
            )
            for level in [1, 2, 3]
        }
        cls.catalog = import_catalog(cls.admin, cls.payload)

    def create_draft(self, level=2):
        assessment = services.create_assessment(
            self.admin,
            employee=self.employee,
            reviewer=self.reviewer,
            title=f"Полный состав, уровень {level}",
            contour=self.contours[level],
            catalog=self.catalog,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            scope="Тестовые данные, все восемь процессов и жизненный цикл",
            processes=list(range(1, 9)),
            lifecycle_included=True,
        )
        services.assign_participants(self.admin, assessment.pk, self.employee, self.reviewer)
        return assessment

    def assert_start_rejected_without_items(self, assessment, message):
        with self.assertRaisesMessage(ValidationError, message):
            services.begin_assessment(self.admin, assessment.pk)
        assessment.refresh_from_db()
        self.assertEqual(assessment.state, Assessment.State.DRAFT)
        self.assertFalse(assessment.items.exists())
        self.assertEqual(assessment.organization_snapshot, {})
        self.assertEqual(assessment.contour_snapshot, {})

    def test_transition_from_syn_uses_full_source_without_claiming_verification(self):
        from standards.demo import create_demo_catalog

        old = self.create_draft()
        old.catalog = create_demo_catalog(self.admin)
        old.save(update_fields=["catalog"])
        services.begin_assessment(self.admin, old.pk)
        old_count = old.items.count()
        new = services.replace_synthetic_questionnaire(self.admin, old.pk, self.catalog)
        self.assertEqual(new.items.count(), 667)
        self.assertEqual(old.items.count(), old_count)
        self.assertFalse(new.items.filter(snapshot__code__startswith="SYN-").exists())
        source = {row["code"]: row for row in self.payload["requirements"]}
        for item in new.items.all():
            row = source[item.snapshot["code"]]
            self.assertEqual(item.snapshot["text"], row["text"])
            self.assertEqual(item.snapshot["mode"], row["levels"]["2"])
            self.assertFalse(item.snapshot["verified"])
        first = new.items.get(snapshot__code="УЗП.1")
        self.assertEqual(first.snapshot["mode"], "Т")
        self.assertIn("уникальными и персонифицированными", first.snapshot["text"])

    def test_import_preserves_all_408_requirements_and_1224_modes_without_verification(self):
        requirements = list(self.catalog.requirements.all())
        self.assertEqual(len(requirements), 408)
        self.assertEqual(
            Counter(row.kind for row in requirements),
            {"selection": 343, "management": 37, "lifecycle": 28},
        )
        self.assertFalse(self.catalog.synthetic)
        self.assertFalse(self.catalog.verified)
        self.assertFalse(self.catalog.requirements.filter(verified=True).exists())
        source = {row["code"]: row for row in self.payload["requirements"]}
        self.assertEqual({row.code for row in requirements}, set(source))
        for requirement in requirements:
            row = source[requirement.code]
            with self.subTest(code=requirement.code):
                # При ошибке не печатать полный нормативный текст в журнал тестов.
                self.assertTrue(requirement.text == row["text"], "Изменена формулировка")
                self.assertTrue(requirement.citation == row["citation"], "Изменена ссылка")
                self.assertEqual(
                    (
                        requirement.kind,
                        requirement.process,
                        requirement.subprocess,
                        requirement.direction,
                        requirement.stage,
                    ),
                    tuple(
                        row[key] for key in ["kind", "process", "subprocess", "direction", "stage"]
                    ),
                )
        implementations = ImplementationRequirement.objects.filter(
            requirement__catalog=self.catalog
        )
        self.assertEqual(implementations.count(), 1224)
        self.assertEqual(
            set(implementations.values_list("requirement__code", "level", "mode")),
            {
                (row["code"], int(level), mode)
                for row in self.payload["requirements"]
                for level, mode in row["levels"].items()
            },
        )

    def test_unverified_manual_post_and_direct_draft_cannot_start_questionnaire(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("assessment-create"),
            {
                "mode": "manual",
                "title": "Подстановка неподтверждённого каталога",
                "contours": [self.contours[2].pk],
                "employee": self.employee.pk,
                "reviewer": self.reviewer.pk,
                "catalog": self.catalog.pk,
                "period_start": "2026-01-01",
                "period_end": "2026-12-31",
                "scope": "Все процессы",
                "processes": [str(i) for i in range(1, 9)],
                "lifecycle_included": "on",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("catalog", response.context["form"].errors)
        self.assertFalse(Assessment.objects.exists())

        # Сервис допускает пустой черновик; защитная граница находится на старте.
        assessment = self.create_draft()
        self.assert_start_rejected_without_items(assessment, "Каталог не прошёл верификацию")

    def test_catalog_flag_cannot_bypass_one_unverified_requirement(self):
        # Только тестовая БД: моделируем несогласованные флаги, не нормативную сверку.
        self.catalog.requirements.update(verified=True)
        self.catalog.requirements.filter(code="ЖЦ.28").update(verified=False)
        CatalogVersion.objects.filter(pk=self.catalog.pk).update(verified=True)
        self.assertEqual(self.catalog.requirements.filter(verified=False).count(), 1)
        assessment = self.create_draft()
        self.assert_start_rejected_without_items(assessment, "неверифицированные записи")

    def test_all_three_levels_create_667_contexts_and_keep_optional_measures(self):
        # Имитация допуска исключительно в TestCase; реальный каталог не подтверждается.
        self.catalog.requirements.update(verified=True)
        CatalogVersion.objects.filter(pk=self.catalog.pk).update(verified=True)
        source = {row["code"]: row for row in self.payload["requirements"]}
        management_codes = {code for code, row in source.items() if row["kind"] == "management"}
        expected_stages = {
            "Создание (модернизация) АС": 11,
            "Ввод в эксплуатацию АС": 3,
            "Эксплуатация (сопровождение) АС": 11,
            "Эксплуатация (сопровождение) и снятие с эксплуатации АС": 3,
        }
        for level in [1, 2, 3]:
            with self.subTest(level=level):
                assessment = self.create_draft(level)
                services.begin_assessment(self.admin, assessment.pk)
                assessment.refresh_from_db()
                self.assertEqual(assessment.state, Assessment.State.FILLING)
                items = list(assessment.items.all())
                self.assertEqual(len(items), 667)
                self.assertEqual(
                    Counter(item.snapshot["kind"] for item in items),
                    {"selection": 343, "management": 296, "lifecycle": 28},
                )
                selection = [item for item in items if item.snapshot["kind"] == "selection"]
                self.assertEqual(
                    Counter(item.context_process for item in selection),
                    dict(zip(range(1, 9), [102, 47, 36, 28, 33, 42, 43, 12], strict=True)),
                )
                for process in range(1, 9):
                    management = [
                        item
                        for item in items
                        if item.snapshot["kind"] == "management" and item.context_process == process
                    ]
                    self.assertEqual(len(management), 37)
                    self.assertEqual(
                        {item.snapshot["code"] for item in management}, management_codes
                    )
                    self.assertEqual(
                        Counter(item.snapshot["direction"] for item in management),
                        {1: 5, 2: 16, 3: 12, 4: 4},
                    )
                lifecycle = [item for item in items if item.snapshot["kind"] == "lifecycle"]
                self.assertEqual(
                    Counter(item.snapshot["stage"] for item in lifecycle), expected_stages
                )
                self.assertTrue(all(item.context_process == 0 for item in lifecycle))
                for item in items:
                    snapshot = item.snapshot
                    row = source[snapshot["code"]]
                    self.assertEqual(snapshot["mode"], row["levels"][str(level)], row["code"])
                    self.assertEqual(snapshot["level"], level)
                    self.assertEqual(snapshot["process"], item.context_process)
                    self.assertTrue(snapshot["text"] == row["text"], row["code"])

                context = matrix_context(assessment, QueryDict("tab=applicability"))
                optional = [row for row in context["applicability_rows"] if row["mode"] == "Н"]
                expected_optional = sum(
                    8 if row["kind"] == "management" else 1
                    for row in source.values()
                    if row["levels"][str(level)] == "Н"
                )
                self.assertGreater(expected_optional, 0)
                self.assertEqual(len(optional), expected_optional)
                self.assertTrue(
                    all(row["included"] and row["decision"] is None for row in optional)
                )
                self.assertEqual(context["matrix_counts"]["excluded"], 0)
                self.assertEqual(context["matrix_counts"]["empty"], 667)
                self.assertEqual(len(context["applicability_rows"]), 667)
                self.assertFalse(assessment.submissions.exists())
