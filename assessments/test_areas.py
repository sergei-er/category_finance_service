from datetime import date
from decimal import Decimal
from io import BytesIO
from zipfile import ZipFile

from django.test import TestCase
from django.urls import reverse

from calculations.engine import UndefinedResult
from organization.models import ProtectionContour
from reports.export import build_docx, build_xlsx

from . import services
from . import tests as workflow_tests


class CalculationAreaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        workflow_tests.WorkflowTests.setUpTestData.__func__(cls)

    def create(self, levels, lifecycle=False):
        contours = [
            ProtectionContour.objects.create(
                organization=self.contour.organization,
                name=f"Контур {index}, уровень {level}",
                level=level,
            )
            for index, level in enumerate(levels)
        ]
        assessment = services.create_assessment(
            self.admin,
            title="Группы контуров",
            contours=contours,
            employee=self.employee,
            reviewer=self.reviewer,
            catalog=self.catalog,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            scope="Синтетическая проверка групп",
            processes=[3],
            lifecycle_included=lifecycle,
            lifecycle_reason="Синтетическая область без оценки жизненного цикла"
            if not lifecycle
            else "",
        )
        services.begin_assessment(self.admin, assessment.pk)
        assessment.refresh_from_db()
        return assessment

    def accept(self, assessment):
        for item in assessment.items.all():
            score = Decimal("1") if item.area.level == 1 else Decimal("0")
            services.save_answer(
                self.employee,
                item.pk,
                text="Синтетический ответ",
                sources="Тестовый документ",
                score=score,
            )
        services.submit_assessment(self.employee, assessment.pk)
        for item in assessment.items.all():
            revision = item.current_revision
            services.review_answer(
                self.reviewer,
                revision.pk,
                outcome="accepted",
                score=revision.employee_score,
                reason="Проверено в тесте",
            )

    def test_same_level_uses_one_questionnaire_and_one_cycle(self):
        assessment = self.create([1, 1, 1], lifecycle=True)
        self.assertEqual(assessment.calculation_areas.count(), 1)
        self.assertEqual(assessment.items.count(), 10)  # 2 выбор + 4 управление + 4 ЖЦ
        self.assertEqual(assessment.calculation_areas.get().contours.count(), 3)
        self.accept(assessment)
        result = services.run_calculation(self.reviewer, assessment.pk).result
        self.assertEqual(result["result"], "1.00")
        self.assertEqual(result["trace"][-1]["denominator"], 2)

    def test_levels_keep_independent_answers_and_export_group_results(self):
        assessment = self.create([1, 1, 3])
        self.assertEqual(assessment.items.count(), 12)
        self.assertEqual(assessment.calculation_areas.count(), 2)
        self.accept(assessment)
        snapshot = services.approve_assessment(self.reviewer, assessment.pk)
        self.assertEqual(snapshot.data["result"]["result"], "0.80")
        self.assertEqual([g["level"] for g in snapshot.data["result"]["groups"]], [1, 3])
        self.assertEqual(len(snapshot.data["inputs"]["answers"]), 12)
        self.assertEqual(snapshot.calculation.algorithm_version, "gost-areas-2")
        self.assertEqual(snapshot.digest, services.digest(snapshot.data))
        with ZipFile(BytesIO(build_docx(snapshot.data))) as archive:
            self.assertIn("Группа контуров: уровень 3", archive.read("word/document.xml").decode())
        with ZipFile(BytesIO(build_xlsx(snapshot.data))) as archive:
            xml = archive.read("xl/worksheets/sheet4.xml").decode()
            self.assertIn("1.00", xml)
            self.assertIn("0.00", xml)

    def test_level_snapshot_does_not_follow_contour_edits(self):
        assessment = self.create([1])
        contour = assessment.contours.get()
        contour.level = 3
        contour.name = "Переименованный контур"
        contour.save()
        self.accept(assessment)
        run = services.run_calculation(self.reviewer, assessment.pk)
        self.assertEqual(run.inputs["levels"], [1])
        self.assertEqual(run.result["groups"][0]["level"], 1)

    def test_missing_or_legacy_area_cannot_be_silently_ignored(self):
        assessment = self.create([1, 3])
        assessment.items.filter(area__level=3).delete()
        self.accept(assessment)
        with self.assertRaisesMessage(UndefinedResult, "Состав анкеты"):
            services.run_calculation(self.reviewer, assessment.pk)
        assessment.items.update(area=None)
        with self.assertRaisesMessage(UndefinedResult, "область не определена"):
            services.run_calculation(self.reviewer, assessment.pk)

    def test_multilevel_lifecycle_blocks_approval_without_hiding_process_results(self):
        assessment = self.create([1, 3], lifecycle=True)
        self.accept(assessment)
        run = services.run_calculation(self.reviewer, assessment.pk)
        self.assertEqual(run.result["processes"][0]["value"], "0.80")
        self.assertIsNone(run.result["result"])
        with self.assertRaisesMessage(UndefinedResult, "М-05"):
            services.approve_assessment(self.reviewer, assessment.pk)
        self.assertFalse(hasattr(assessment, "result_snapshot"))
        self.client.force_login(self.reviewer)
        response = self.client.get(
            reverse("assessment", args=[assessment.pk]), {"tab": "calculation"}
        )
        self.assertContains(response, "Общий результат не определён")
        self.assertContains(response, "Уровень защиты 3")

    def test_area_and_contour_filters_show_shared_rows_without_mutation(self):
        assessment = self.create([1, 1, 3])
        self.client.force_login(self.employee)
        area = assessment.calculation_areas.get(level=1)
        url = reverse("assessment", args=[assessment.pk])
        response = self.client.get(url, {"area": area.pk})
        self.assertEqual(response.context["matrix_filtered_count"], 6)
        self.assertEqual(response.context["matrix_total"], 12)
        self.assertContains(response, "Контуры уровня 1")
        for contour in area.contours.all():
            response = self.client.get(url, {"contour": contour.pk})
            self.assertEqual(response.context["matrix_filtered_count"], 6)
            self.assertTrue(
                all(row["item"].area_id == area.pk for row in response.context["matrix_rows"])
            )
        self.assertFalse(assessment.items.filter(revisions__isnull=False).exists())
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url, {"area": area.pk}).status_code, 403)
