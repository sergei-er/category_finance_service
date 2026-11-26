from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.urls import reverse

from assessments import services
from assessments.models import Assessment
from calculations.engine import UndefinedResult
from standards.services import import_catalog

from . import test_workbench


class CatalogTransitionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_workbench.WorkbenchTests.setUpTestData.__func__(cls)
        # Текст намеренно тестовый: нормативная сверка этим тестом не подтверждается.
        cls.target = import_catalog(
            cls.admin,
            {
                "schema": 1,
                "title": "Тест перехода",
                "source": "Тест",
                "requirements": [
                    {
                        "code": "УЗП.1",
                        "text": "Тестовый текст меры",
                        "citation": "Тест",
                        "kind": "selection",
                        "process": 1,
                        "subprocess": 1,
                        "levels": {"1": "Т", "2": "Т", "3": "Т"},
                    }
                ],
            },
        )
        cls.assessment.processes = [1]
        cls.assessment.save(update_fields=["processes"])

    def test_creates_new_questionnaire_preserving_source_and_verification(self):
        old_items = list(self.assessment.items.values("pk", "snapshot"))
        revision = services.save_answer(
            self.employee,
            self.assessment.items.first().pk,
            text="Старый ответ",
            score=Decimal(1),
            sources="Тест",
        )
        new = services.replace_synthetic_questionnaire(self.admin, self.assessment.pk, self.target)
        self.assertNotEqual(new.pk, self.assessment.pk)
        self.assertEqual(new.state, "filling")
        item = new.items.get()
        self.assertEqual(item.snapshot["code"], "УЗП.1")
        self.assertEqual(item.snapshot["text"], "Тестовый текст меры")
        self.assertEqual(item.snapshot["mode"], "Т")
        self.assertFalse(item.snapshot["verified"])
        self.assertIsNone(item.current_revision)
        self.assertEqual(old_items, list(self.assessment.items.values("pk", "snapshot")))
        revision.refresh_from_db()
        self.assertEqual(revision.text, "Старый ответ")
        self.target.refresh_from_db()
        self.assertFalse(self.target.verified)
        with self.assertRaises(UndefinedResult):
            services.collect_inputs(new)
        answer = services.save_answer(
            self.employee, item.pk, text="Описание", score=Decimal(1), sources="Источник"
        )
        services.submit_assessment(self.employee, new.pk)
        services.review_answer(
            self.reviewer, answer.pk, outcome="accepted", score=Decimal(1), reason="Проверено"
        )
        with self.assertRaisesMessage(UndefinedResult, "Каталог не верифицирован"):
            services.approve_assessment(self.reviewer, new.pk)
        self.assertFalse(new.calculations.exists())

    def test_rejects_non_admin_and_synthetic_target_without_creating_rows(self):
        count = Assessment.objects.count()
        with self.assertRaises(PermissionDenied):
            services.replace_synthetic_questionnaire(self.employee, self.assessment.pk, self.target)
        with self.assertRaises(ValidationError):
            services.replace_synthetic_questionnaire(self.admin, self.assessment.pk, self.catalog)
        self.assertEqual(Assessment.objects.count(), count)

    def test_empty_scope_rolls_back_new_assessment(self):
        self.assessment.processes = [8]
        self.assessment.save(update_fields=["processes"])
        count = Assessment.objects.count()
        with self.assertRaises(ValidationError):
            services.replace_synthetic_questionnaire(self.admin, self.assessment.pk, self.target)
        self.assertEqual(Assessment.objects.count(), count)

    def test_web_transition_requires_confirmation_role_and_csrf(self):
        url = reverse("catalog-transition", args=[self.assessment.pk])
        self.client.force_login(self.employee)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.admin)
        count = Assessment.objects.count()
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(url, {"catalog": self.target.pk})
        self.assertEqual(Assessment.objects.count(), count)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        self.assertEqual(
            csrf_client.post(url, {"catalog": self.target.pk, "confirm": "on"}).status_code, 403
        )
        response = self.client.post(url, {"catalog": self.target.pk, "confirm": "on"})
        self.assertEqual(response.status_code, 302)
        new = Assessment.objects.latest("pk")
        self.client.force_login(self.employee)
        response = self.client.get(reverse("assessment", args=[new.pk]))
        self.assertContains(response, "УЗП.1")
        self.assertContains(response, "Каталог ожидает сверки")
        self.assertNotContains(response, "SYN-P")
