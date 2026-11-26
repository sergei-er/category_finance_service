"""Регрессионные проверки карточки, матрицы и действий разных ролей."""

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.http import QueryDict
from django.test import TestCase
from django.urls import reverse

from accounts.access import ADMIN, EMPLOYEE, REVIEWER
from accounts.roles import setup_roles
from assessments import services
from assessments.models import AnswerRevision, Assessment, ReviewDecision, Submission
from auditlog.models import AuditEvent
from core.matrix import matrix_context
from organization.models import OrganizationProfile, ProtectionContour
from reports.models import ResultSnapshot
from standards.demo import create_demo_catalog
from standards.models import CatalogVersion, RequirementVersion


class WorkbenchTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        setup_roles()
        for username, role in [
            ("admin", ADMIN),
            ("employee", EMPLOYEE),
            ("reviewer", REVIEWER),
            ("other", EMPLOYEE),
        ]:
            user = get_user_model().objects.create_user(username)
            user.groups.add(Group.objects.get(name=role))
            setattr(cls, username, user)
        cls.profile = OrganizationProfile.objects.create(
            name="Учебная организация", description="Синтетический профиль", synthetic=True
        )
        cls.contour = ProtectionContour.objects.create(
            organization=cls.profile, name="Учебный контур", level=2
        )
        cls.catalog = create_demo_catalog(cls.admin)
        cls.assessment = services.create_assessment(
            cls.admin,
            title="Рабочая матрица",
            contours=[cls.contour],
            employee=cls.employee,
            reviewer=cls.reviewer,
            catalog=cls.catalog,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            scope="Учебная область",
            processes=[3],
            lifecycle_included=True,
        )
        services.assign_participants(cls.admin, cls.assessment.pk, cls.employee, cls.reviewer)
        services.begin_assessment(cls.admin, cls.assessment.pk)
        cls.assessment.refresh_from_db()
        cls.initial_event_count = AuditEvent.objects.count()

    def payload(self, **overrides):
        data = {
            "title": "Новая карточка",
            "contours": [self.contour.pk],
            "employee": self.employee.pk,
            "reviewer": self.reviewer.pk,
            "catalog": self.catalog.pk,
            "period_start": "2026-01-01",
            "period_end": "2026-12-31",
            "scope": "Отдельная учебная область",
            "processes": ["3"],
            "lifecycle_included": "on",
        }
        return data | overrides

    def fill_and_submit(self):
        for item in self.assessment.items.all():
            services.save_answer(
                self.employee,
                item.pk,
                text="Учебное описание реализации",
                score=Decimal("1"),
                sources="Учебный регламент, пункт 1",
                exclusion_reason="Основание для рассмотрения проверяющим",
            )
        services.submit_assessment(self.employee, self.assessment.pk)
        self.assessment.refresh_from_db()

    def matrix(self, query=""):
        self.assessment.refresh_from_db()
        return matrix_context(self.assessment, QueryDict(query))

    def test_creation_modes_only_read_existing_demo_data_on_get(self):
        self.client.force_login(self.admin)
        original_profile = OrganizationProfile.objects.values().get(pk=self.profile.pk)
        original_counts = (
            Assessment.objects.count(),
            CatalogVersion.objects.count(),
            ProtectionContour.objects.count(),
            AnswerRevision.objects.count(),
        )
        for mode in ["manual", "demo"]:
            with self.subTest(mode=mode):
                response = self.client.get(reverse("assessment-create"), {"mode": mode})
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "workspace/assessment_create.html")
        self.assertEqual(
            original_counts,
            (
                Assessment.objects.count(),
                CatalogVersion.objects.count(),
                ProtectionContour.objects.count(),
                AnswerRevision.objects.count(),
            ),
        )
        self.assertEqual(
            OrganizationProfile.objects.values().get(pk=self.profile.pk), original_profile
        )
        self.assertEqual(AuditEvent.objects.count(), self.initial_event_count)

    def test_demo_post_creates_an_empty_draft_without_overwriting_profile(self):
        self.client.force_login(self.admin)
        original_profile = OrganizationProfile.objects.values().get(pk=self.profile.pk)
        original_catalog_count = CatalogVersion.objects.count()
        response = self.client.post(reverse("assessment-create"), self.payload(mode="demo"))
        self.assertEqual(response.status_code, 302)
        created = Assessment.objects.exclude(pk=self.assessment.pk).get()
        self.assertEqual(created.state, Assessment.State.DRAFT)
        self.assertEqual(created.created_by, self.admin)
        self.assertEqual(created.catalog, self.catalog)
        self.assertEqual(created.contour, self.contour)
        self.assertEqual(created.processes, [3])
        self.assertFalse(created.items.exists())
        self.assertEqual(created.employee, self.employee)
        self.assertEqual(created.reviewer, self.reviewer)
        self.assertFalse(AnswerRevision.objects.exists())
        self.assertFalse(ReviewDecision.objects.exists())
        self.assertFalse(Submission.objects.exists())
        self.assertTrue(
            AuditEvent.objects.filter(
                action="assessment.created", entity_id=str(created.pk)
            ).exists()
        )
        self.assertEqual(CatalogVersion.objects.count(), original_catalog_count)
        self.assertEqual(
            OrganizationProfile.objects.values().get(pk=self.profile.pk), original_profile
        )
        self.assertEqual(AuditEvent.objects.count(), self.initial_event_count + 1)

    def test_manual_creation_remains_compatible_with_posts_without_mode(self):
        self.client.force_login(self.admin)
        catalog = CatalogVersion.objects.create(
            title="Каталог теста формы", source="Тест", verified=True
        )
        response = self.client.post(reverse("assessment-create"), self.payload(catalog=catalog.pk))
        self.assertEqual(response.status_code, 302)
        created = Assessment.objects.exclude(pk=self.assessment.pk).get()
        self.assertEqual(created.title, "Новая карточка")
        self.assertEqual(created.state, Assessment.State.DRAFT)
        self.assertEqual(created.processes, [3])
        self.assertEqual(created.level, self.contour.level)
        self.assertFalse(created.items.exists())

    def test_demo_rejects_tampered_catalogs_and_unknown_mode(self):
        self.client.force_login(self.admin)
        other_catalog = CatalogVersion.objects.create(
            title="Каталог вне учебного шаблона", source="Тест формы", verified=True
        )
        unverified = CatalogVersion.objects.create(
            title="Непроверенный учебный каталог", source="Тест формы", synthetic=True
        )
        attempts = [
            self.payload(mode="demo", catalog=other_catalog.pk),
            self.payload(mode="demo", catalog=unverified.pk),
            self.payload(mode="unknown"),
        ]
        for data in attempts:
            with self.subTest(mode=data["mode"], catalog=data["catalog"]):
                response = self.client.post(reverse("assessment-create"), data)
                self.assertIn(response.status_code, [200, 400])
                self.assertEqual(Assessment.objects.count(), 1)
        self.assertFalse(AnswerRevision.objects.exists())
        self.assertEqual(AuditEvent.objects.count(), self.initial_event_count)

    def test_demo_cannot_overwrite_or_use_a_real_organization_profile(self):
        OrganizationProfile.objects.filter(pk=self.profile.pk).update(synthetic=False)
        original_profile = OrganizationProfile.objects.values().get(pk=self.profile.pk)
        self.client.force_login(self.admin)
        response = self.client.post(reverse("assessment-create"), self.payload(mode="demo"))
        self.assertIn(response.status_code, [200, 400])
        self.assertEqual(Assessment.objects.count(), 1)
        self.assertEqual(
            OrganizationProfile.objects.values().get(pk=self.profile.pk), original_profile
        )

    def test_employee_cannot_create_manual_or_demo_assessment(self):
        self.client.force_login(self.employee)
        for mode in ["manual", "demo"]:
            with self.subTest(mode=mode):
                response = self.client.post(reverse("assessment-create"), self.payload(mode=mode))
                self.assertEqual(response.status_code, 403)
        self.assertEqual(Assessment.objects.count(), 1)

    def test_reviewer_cannot_create_assessment(self):
        self.client.force_login(self.reviewer)
        url = reverse("assessment-create")
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, self.payload()).status_code, 403)
        self.assertEqual(Assessment.objects.count(), 1)

    def test_dashboard_renders_for_all_three_roles(self):
        for user in [self.admin, self.employee, self.reviewer]:
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(reverse("workspace"))
                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "workspace/dashboard.html")
                self.assertEqual(response.context["assessment_counts"]["total"], 1)
                if user == self.admin:
                    self.assertContains(response, "Создать обследование")
                else:
                    self.assertNotContains(response, "Создать обследование")

    def test_organization_assessment_list_is_restricted_to_assignments(self):
        self.client.force_login(self.other)
        response = self.client.get(reverse("organization-card"), {"tab": "assessments"})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.assessment.title)

    def test_zero_is_an_accepted_score_and_not_an_empty_answer(self):
        self.fill_and_submit()
        item = self.assessment.items.filter(snapshot__kind="selection").first()
        services.review_answer(
            self.reviewer,
            item.current_revision.pk,
            outcome="accepted",
            score=Decimal("0"),
            reason="В учебном примере мера не выбрана",
        )
        context = self.matrix("status=accepted")
        self.assertEqual(context["matrix_filtered_count"], 1)
        row = context["matrix_rows"][0]
        self.assertEqual(row["item"].pk, item.pk)
        self.assertEqual(row["status"], "accepted")
        self.assertEqual(row["score"], Decimal("0"))
        self.assertFalse(row["needs_review"])
        self.assertEqual(context["matrix_counts"]["accepted"], 1)
        self.assertEqual(context["matrix_counts"]["empty"], 0)
        self.assertEqual(context["matrix_counts"]["answered"], self.assessment.items.count())

    def test_revised_answer_requires_recheck_while_other_decisions_remain_current(self):
        self.fill_and_submit()
        for item in self.assessment.items.all():
            services.review_answer(
                self.reviewer,
                item.current_revision.pk,
                outcome="accepted",
                score=Decimal("1"),
                reason="Подтверждена первая версия",
            )
        item = self.assessment.items.first()
        original = item.current_revision
        services.return_for_rework(self.reviewer, self.assessment.pk, "Уточнить один ответ")
        updated = services.save_answer(
            self.employee,
            item.pk,
            text="Уточнённый ответ",
            score=Decimal("1"),
            sources="Учебный регламент, редакция 2",
        )
        context = self.matrix("status=recheck")
        self.assertEqual(context["matrix_filtered_count"], 1)
        row = context["matrix_rows"][0]
        self.assertEqual(row["revision"].pk, updated.pk)
        self.assertIsNone(row["decision"])
        self.assertIsNone(row["score"])
        self.assertTrue(row["needs_review"])
        self.assertEqual(context["matrix_counts"]["recheck"], 1)
        self.assertEqual(context["matrix_counts"]["accepted"], self.assessment.items.count() - 1)
        self.assertEqual(original.decision.outcome, "accepted")
        self.assertEqual(item.revisions.count(), 2)
        self.assertGreater(AuditEvent.objects.count(), self.initial_event_count)

    def test_filters_do_not_reduce_scope_or_allow_submission_of_one_visible_answer(self):
        item = self.assessment.items.filter(snapshot__kind="selection").first()
        services.save_answer(
            self.employee,
            item.pk,
            text="Один ответ",
            score=Decimal("1"),
            sources="Учебный источник",
        )
        self.client.force_login(self.employee)
        query = {
            "tab": "scores",
            "q": item.snapshot["code"],
            "kind": "selection",
            "process": "3",
            "status": "pending",
        }
        response = self.client.get(reverse("assessment", args=[self.assessment.pk]), query)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["matrix_filtered_count"], 1)
        self.assertEqual(response.context["matrix_total"], self.assessment.items.count())
        self.assertEqual(
            response.context["matrix_counts"]["empty"], self.assessment.items.count() - 1
        )
        original_scope = list(self.assessment.items.values_list("pk", flat=True))
        response = self.client.post(
            reverse("assessment-action", args=[self.assessment.pk, "submit"]),
            {"item_ids": [item.pk], **query},
        )
        self.assertEqual(response.status_code, 400)
        self.assessment.refresh_from_db()
        self.assertEqual(self.assessment.state, Assessment.State.FILLING)
        self.assertFalse(self.assessment.submissions.exists())
        self.assertEqual(list(self.assessment.items.values_list("pk", flat=True)), original_scope)

    def test_matrix_uses_frozen_requirement_and_does_not_auto_exclude_optional_measure(self):
        item = self.assessment.items.filter(snapshot__kind="selection").first()
        snapshot = item.snapshot.copy()
        # Имитируем импорт отметки Н для проверки её отображения.
        item.snapshot = snapshot | {"mode": "Н"}
        item.save(update_fields=["snapshot"])
        RequirementVersion.objects.filter(pk=item.requirement_id).update(
            text="ПОДМЕНЁННАЯ ФОРМУЛИРОВКА ПОСЛЕ СТАРТА", code="CHANGED-AFTER-START"
        )
        context = self.matrix(f"q={snapshot['code']}&tab=applicability")
        self.assertEqual(context["matrix_filtered_count"], 1)
        row = context["matrix_rows"][0]
        self.assertEqual(row["item"].snapshot["text"], snapshot["text"])
        self.assertEqual(row["mode"], "Н")
        self.assertEqual(row["status"], "empty")
        self.assertTrue(row["included"])
        self.assertEqual(context["matrix_counts"]["excluded"], 0)
        self.assertEqual(len(context["applicability_rows"]), self.assessment.items.count())
        self.client.force_login(self.employee)
        response = self.client.get(reverse("assessment", args=[self.assessment.pk]))
        self.assertContains(response, snapshot["text"])
        self.assertNotContains(response, "ПОДМЕНЁННАЯ ФОРМУЛИРОВКА ПОСЛЕ СТАРТА")

    def test_tabs_are_read_only_and_do_not_bypass_assessment_access(self):
        original_state = self.assessment.state
        url = reverse("assessment", args=[self.assessment.pk])
        tabs = ["card", "scores", "applicability", "evidence", "findings", "calculation", "report"]
        for user in [self.employee, self.reviewer]:
            self.client.force_login(user)
            for tab in tabs:
                with self.subTest(user=user.username, tab=tab):
                    response = self.client.get(url, {"tab": tab})
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.context["active_tab"], tab)
        response = self.client.get(url, {"tab": "../../settings/audit"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["active_tab"], "scores")
        self.client.force_login(self.other)
        for tab in tabs:
            with self.subTest(tab=tab):
                self.assertEqual(self.client.get(url, {"tab": tab}).status_code, 403)
        self.assessment.refresh_from_db()
        self.assertEqual(self.assessment.state, original_state)
        self.assertFalse(AnswerRevision.objects.exists())
        self.assertFalse(Submission.objects.exists())
        self.assertEqual(AuditEvent.objects.count(), self.initial_event_count)

    def test_methodology_requires_login_and_rejects_writes(self):
        url = reverse("methodology")
        self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(self.employee)
        self.assertRedirects(
            self.client.get(url), reverse("workspace"), fetch_redirect_response=False
        )
        self.assertEqual(self.client.post(url).status_code, 405)
        self.assertEqual(AuditEvent.objects.count(), self.initial_event_count)

    def test_report_tab_checks_snapshot_integrity_before_showing_approved_result(self):
        self.fill_and_submit()
        for item in self.assessment.items.all():
            services.review_answer(
                self.reviewer,
                item.current_revision.pk,
                outcome="accepted",
                score=Decimal("1"),
                reason="Подтверждены сведения учебного примера",
            )
        snapshot = services.approve_assessment(self.reviewer, self.assessment.pk)
        self.client.force_login(self.reviewer)
        url = reverse("assessment", args=[self.assessment.pk])
        self.assertEqual(self.client.get(url, {"tab": "report"}).status_code, 200)
        snapshot.data["result"]["result"] = "0.12"
        ResultSnapshot.objects.filter(pk=snapshot.pk).update(data=snapshot.data)
        response = self.client.get(url, {"tab": "report"})
        self.assertContains(response, "Нарушена целостность снимка", status_code=409)
        self.assertNotContains(response, "0.12", status_code=409)
