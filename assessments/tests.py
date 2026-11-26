import tempfile
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.access import ADMIN, EMPLOYEE, REVIEWER
from accounts.roles import setup_roles
from auditlog.models import AuditEvent
from calculations.engine import UndefinedResult
from evidence.services import open_evidence, retire_evidence, upload_evidence
from organization.models import OrganizationProfile, ProtectionContour
from reports.export import build_docx
from reports.models import ReportExport
from standards.demo import create_demo_catalog
from standards.services import verify_requirement

from . import services as s
from .models import AnswerRevision, ReviewDecision, ScoreAdjustment


class WorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        setup_roles()
        cls.users = {}
        for key, role in [
            ("admin", ADMIN),
            ("employee", EMPLOYEE),
            ("reviewer", REVIEWER),
            ("other", EMPLOYEE),
        ]:
            user = get_user_model().objects.create_user(key, password="Test-example-strong-923!")
            user.groups.add(Group.objects.get(name=role))
            cls.users[key] = user
        cls.admin, cls.employee, cls.reviewer, cls.other = [
            cls.users[k] for k in ["admin", "employee", "reviewer", "other"]
        ]
        profile = OrganizationProfile.objects.create(name="Синтетический профиль")
        cls.contour = ProtectionContour.objects.create(organization=profile, name="Контур", level=2)
        cls.catalog = create_demo_catalog(cls.admin)

    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        context = override_settings(MEDIA_ROOT=self.media.name)
        context.enable()
        self.addCleanup(context.disable)
        self.a = s.create_assessment(
            self.admin,
            title="Тест",
            contours=[self.contour],
            employee=self.employee,
            reviewer=self.reviewer,
            catalog=self.catalog,
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            scope="Учебная область",
            processes=[3],
            lifecycle_included=True,
        )
        s.assign_participants(self.admin, self.a.pk, self.employee, self.reviewer)
        s.begin_assessment(self.admin, self.a.pk)
        self.a.refresh_from_db()
        self.initial_event_count = AuditEvent.objects.count()

    def fill(self):
        for item in self.a.items.all():
            s.save_answer(
                self.employee,
                item.pk,
                text="Реализовано",
                score=Decimal("1"),
                sources="Синтетический регламент",
            )

    def accept(self):
        self.fill()
        s.submit_assessment(self.employee, self.a.pk)
        for item in self.a.items.all():
            s.review_answer(
                self.reviewer,
                item.current_revision.pk,
                outcome="accepted",
                score=Decimal("1"),
                reason="Учебное подтверждение",
            )

    def test_full_cycle_snapshot_and_export(self):
        self.assertEqual(AuditEvent.objects.count(), self.initial_event_count)
        archived_event = AuditEvent.objects.create(
            actor=self.admin,
            action="legacy.assessment.create",
            entity="assessments.assessment",
            entity_id=str(self.a.pk),
            details={"preserved": True},
        )
        archived_data = AuditEvent.objects.values().get(pk=archived_event.pk)
        self.accept()
        run = s.run_calculation(self.reviewer, self.a.pk)
        self.assertEqual(run.result["result"], "1.00")
        snapshot = s.approve_assessment(self.reviewer, self.a.pk)
        self.assertEqual(snapshot.digest, s.digest(snapshot.data))
        first = build_docx(snapshot.data)
        self.assertTrue(first.startswith(b"PK"))
        self.assertEqual(first, build_docx(snapshot.data))
        s.close_assessment(self.reviewer, self.a.pk)
        self.a.refresh_from_db()
        self.assertEqual(self.a.state, "closed")
        with self.assertRaises(ValidationError):
            s.save_answer(self.employee, self.a.items.first().pk, text="Изменение")
        with self.assertRaises(ValidationError):
            s.return_for_rework(self.reviewer, self.a.pk, "Открыть")
        self.assertEqual(
            AnswerRevision.objects.filter(item__assessment=self.a).count(), self.a.items.count()
        )
        self.assertEqual(
            ReviewDecision.objects.filter(revision__item__assessment=self.a).count(),
            self.a.items.count(),
        )
        self.assertTrue(AuditEvent.objects.filter(action="assessment.approved").exists())
        self.assertTrue(AuditEvent.objects.filter(action="assessment.closed").exists())
        self.assertEqual(AuditEvent.objects.values().get(pk=archived_event.pk), archived_data)

    def test_incomplete_blocks_submission(self):
        with self.assertRaises(ValidationError):
            s.submit_assessment(self.employee, self.a.pk)

    def test_non_admin_cannot_create_assessment(self):
        for actor in [self.employee, self.reviewer, self.other]:
            with self.subTest(actor=actor.username), self.assertRaises(PermissionDenied):
                s.create_assessment(actor)

    def test_missing_employee_score_is_not_implicitly_one(self):
        item = self.a.items.first()
        for score in [None, Decimal("NaN"), Decimal("0.25"), 1]:
            with self.subTest(score=score), self.assertRaises(ValidationError):
                s.save_answer(
                    self.employee, item.pk, text="Описание", sources="Документ", score=score
                )
        self.assertFalse(item.revisions.exists())

    def test_employee_score_obeys_section_scale_and_preserves_zero(self):
        selection = self.a.items.filter(snapshot__kind="selection").first()
        with self.assertRaises(ValidationError):
            s.save_answer(
                self.employee,
                selection.pk,
                text="Описание",
                sources="Документ",
                score=Decimal("0.5"),
            )
        revision = s.save_answer(
            self.employee,
            selection.pk,
            text="Мера не выбрана",
            sources="Документ",
            score=Decimal("0"),
        )
        self.assertEqual(revision.employee_score, Decimal("0"))
        management = self.a.items.filter(snapshot__kind="management").first()
        revision = s.save_answer(
            self.employee,
            management.pk,
            text="Частичная реализация",
            sources="Документ",
            score=Decimal("0.5"),
        )
        self.assertEqual(revision.employee_score, Decimal("0.5"))

    def test_score_adjustment_requires_individual_acceptance(self):
        self.accept()
        item = self.a.items.filter(snapshot__kind="selection").first()
        revision = item.current_revision
        s.review_answer(
            self.reviewer,
            revision.pk,
            outcome="accepted",
            score=Decimal("0"),
            reason="Мера не выбрана согласно представленному документу",
        )
        adjustment = revision.adjustments.get()
        self.assertEqual(adjustment.original_score, Decimal("1"))
        self.assertEqual(adjustment.proposed_score, Decimal("0"))
        with self.assertRaises(UndefinedResult):
            s.run_calculation(self.reviewer, self.a.pk)
        with self.assertRaises(ValidationError):
            s.approve_assessment(self.reviewer, self.a.pk)
        with self.assertRaises(PermissionDenied):
            s.decide_adjustment(self.admin, adjustment.pk, decision="accepted")
        s.decide_adjustment(self.employee, adjustment.pk, decision="accepted")
        self.assertEqual(s.run_calculation(self.reviewer, self.a.pk).result["result"], "0.88")
        self.assertEqual(revision.employee_score, Decimal("1"))

    def test_rejected_adjustment_is_preserved_and_new_proposal_needs_acceptance(self):
        self.accept()
        revision = self.a.items.filter(snapshot__kind="selection").first().current_revision
        s.review_answer(
            self.reviewer,
            revision.pk,
            outcome="accepted",
            score=Decimal("0"),
            reason="Обоснование корректировки",
        )
        first = revision.adjustments.get()
        with self.assertRaises(ValidationError):
            s.decide_adjustment(self.employee, first.pk, decision="rejected")
        s.decide_adjustment(
            self.employee, first.pk, decision="rejected", comment="Прилагается возражение"
        )
        with self.assertRaises(ValidationError):
            s.approve_assessment(self.reviewer, self.a.pk)
        s.review_answer(
            self.reviewer,
            revision.pk,
            outcome="accepted",
            score=Decimal("0"),
            reason="Дополнительное обоснование после рассмотрения возражения",
        )
        first.refresh_from_db()
        self.assertEqual(first.status, ScoreAdjustment.Status.SUPERSEDED)
        self.assertEqual(first.decision_comment, "Прилагается возражение")
        second = revision.adjustments.get(status=ScoreAdjustment.Status.PENDING)
        self.assertNotEqual(first.pk, second.pk)
        with self.assertRaises(UndefinedResult):
            s.run_calculation(self.reviewer, self.a.pk)
        s.decide_adjustment(self.employee, second.pk, decision="accepted")
        self.assertEqual(revision.adjustments.count(), 2)

    def test_no_implicit_zero(self):
        self.fill()
        s.submit_assessment(self.employee, self.a.pk)
        with self.assertRaises(UndefinedResult):
            s.run_calculation(self.reviewer, self.a.pk)

    def test_revision_requires_new_decision_only_changed_answer(self):
        self.accept()
        item = self.a.items.first()
        old = item.current_revision
        s.return_for_rework(self.reviewer, self.a.pk, "Уточнить ответ")
        new = s.save_answer(
            self.employee, item.pk, text="Уточнено", score=Decimal("1"), sources="Новая редакция"
        )
        self.assertEqual(new.number, 2)
        self.assertIsNone(new.decision)
        self.assertEqual(old.decision.outcome, "accepted")
        s.submit_assessment(self.employee, self.a.pk)
        with self.assertRaises(UndefinedResult):
            s.run_calculation(self.reviewer, self.a.pk)
        s.review_answer(
            self.reviewer,
            new.pk,
            outcome="accepted",
            score=Decimal("1"),
            reason="Повторная проверка",
        )
        self.assertEqual(s.run_calculation(self.reviewer, self.a.pk).result["result"], "1.00")
        self.assertEqual(AnswerRevision.objects.filter(item=item).count(), 2)
        self.assertEqual(ReviewDecision.objects.filter(revision__item=item).count(), 2)
        self.assertEqual(old.decision.reason, "Учебное подтверждение")
        self.assertEqual(new.decision.reason, "Повторная проверка")
        self.assertGreater(AuditEvent.objects.count(), self.initial_event_count)

    def test_other_user_and_admin_cannot_answer(self):
        for actor in [self.admin, self.reviewer, self.other]:
            with self.subTest(actor=actor.username), self.assertRaises(PermissionDenied):
                s.save_answer(actor, self.a.items.first().pk, text="Чужой ответ")

    def test_scale_rejected(self):
        self.fill()
        s.submit_assessment(self.employee, self.a.pk)
        item = self.a.items.filter(snapshot__kind="selection").first()
        with self.assertRaises(ValidationError):
            s.review_answer(
                self.reviewer,
                item.current_revision.pk,
                outcome="accepted",
                score=Decimal("0.5"),
                reason="Недопустимо",
            )

    def test_private_evidence_and_history(self):
        file = upload_evidence(
            self.employee,
            self.a.pk,
            SimpleUploadedFile("регламент.txt", b"synthetic document"),
            "Пункт 1",
        )
        revision = s.save_answer(
            self.employee,
            self.a.items.first().pk,
            text="Ответ",
            score=Decimal("1"),
            files=[file],
            reference="Пункт 1",
        )
        self.assertEqual(revision.evidence.get(), file)
        with self.assertRaises(PermissionDenied):
            open_evidence(self.other, file)
        with self.assertRaises(ValidationError):
            retire_evidence(self.employee, file.pk)
        stream = open_evidence(self.reviewer, file)
        self.assertEqual(stream.read(), b"synthetic document")
        stream.close()
        self.assertGreater(AuditEvent.objects.count(), self.initial_event_count)

    def test_invalid_file_and_integrity(self):
        with self.assertRaises(ValidationError):
            upload_evidence(
                self.employee, self.a.pk, SimpleUploadedFile("fake.pdf", b"not pdf"), "Пункт 1"
            )
        file = upload_evidence(
            self.employee, self.a.pk, SimpleUploadedFile("ok.txt", b"text"), "Пункт 1"
        )
        with file.file.open("wb") as stream:
            stream.write(b"changed")
        with self.assertRaises(ValidationError):
            open_evidence(self.employee, file)

    def test_catalog_locked_after_begin(self):
        with self.assertRaises(ValidationError):
            verify_requirement(self.admin, self.catalog.requirements.first().pk, "Редакция")

    def test_pages_and_authorization(self):
        paths = ["workspace", "assessment", "answer", "answer-history", "compare"]
        self.client.force_login(self.employee)
        for name in paths:
            pk = self.a.items.first().pk if name in {"answer", "answer-history"} else self.a.pk
            url = reverse(name) if name == "workspace" else reverse(name, args=[pk])
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertEqual(self.client.get(reverse("settings-home")).status_code, 403)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("assessment", args=[self.a.pk])).status_code, 403)

    def test_admin_pages(self):
        self.client.force_login(self.admin)
        for name in [
            "settings-home",
            "organization-edit",
            "contour-create",
            "user-create",
            "user-roles",
            "catalog-import",
        ]:
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertRedirects(
            self.client.get(reverse("catalog", args=[self.catalog.pk])),
            reverse("settings-home"),
            fetch_redirect_response=False,
        )
        self.assertNotContains(self.client.get(reverse("settings-home")), "settings/audit/")
        self.assertEqual(self.client.get("/settings/audit/").status_code, 404)

    def test_post_only_and_csrf(self):
        self.client.force_login(self.employee)
        url = reverse("assessment-action", args=[self.a.pk, "submit"])
        self.assertEqual(self.client.get(url).status_code, 405)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.employee)
        self.assertEqual(client.post(url).status_code, 403)

    def test_frozen_pages(self):
        self.accept()
        snapshot = s.approve_assessment(self.reviewer, self.a.pk)
        self.client.force_login(self.reviewer)
        for name, pk in [("snapshot", self.a.pk), ("calculation", snapshot.calculation_id)]:
            self.assertEqual(self.client.get(reverse(name, args=[pk])).status_code, 200)
        for fmt in ["docx", "json"]:
            self.assertEqual(
                self.client.post(reverse("report-export", args=[self.a.pk, fmt])).status_code, 200
            )
        self.assertEqual(ReportExport.objects.filter(snapshot=snapshot).count(), 2)
        self.assertGreater(AuditEvent.objects.count(), self.initial_event_count)

    def test_finding_duplicate_count_and_pending(self):
        self.accept()
        data = dict(
            description="Нарушение",
            source="reviewer",
            discovered_on=date(2026, 1, 1),
            investigation_completed=False,
            response_documented=False,
            potential_harm=True,
            basis="Учебное обстоятельство",
        )
        primary = s.save_finding(self.reviewer, self.a.pk, **data)
        with self.assertRaises(UndefinedResult):
            s.run_calculation(self.reviewer, self.a.pk)
        s.review_finding(self.reviewer, primary.pk, include=True, reason="Подтверждённый факт")
        duplicate = s.save_finding(self.reviewer, self.a.pk, **data)
        s.review_finding(
            self.reviewer,
            duplicate.pk,
            include=False,
            duplicate_of=primary.finding,
            reason="Тот же факт",
        )
        run = s.run_calculation(self.reviewer, self.a.pk)
        self.assertEqual(run.inputs["z"], 1)
        self.assertEqual(run.result["result"], "0.99")
        self.assertGreater(AuditEvent.objects.count(), self.initial_event_count)

    def test_organization_investigation_exemption(self):
        self.accept()
        rev = s.save_finding(
            self.reviewer,
            self.a.pk,
            description="Выявлено организацией",
            source="organization",
            discovered_on=date(2026, 1, 1),
            investigation_completed=True,
            response_documented=True,
            potential_harm=True,
            basis="Есть материалы расследования",
        )
        with self.assertRaises(ValidationError):
            s.review_finding(self.reviewer, rev.pk, include=True, reason="Неверное включение")

    def test_own_material_cannot_be_approved(self):
        material = upload_evidence(
            self.reviewer,
            self.a.pk,
            SimpleUploadedFile("reviewer.txt", b"synthetic"),
            "Материал проверяющего",
        )
        self.fill()
        rev = s.save_answer(
            self.employee,
            self.a.items.first().pk,
            text="Ответ",
            score=Decimal("1"),
            files=[material],
        )
        s.submit_assessment(self.employee, self.a.pk)
        with self.assertRaises(PermissionDenied):
            s.review_answer(
                self.reviewer,
                rev.pk,
                outcome="accepted",
                score=Decimal("1"),
                reason="Самоутверждение",
            )

    def test_bruteforce_throttle(self):
        url = reverse("login")
        for _ in range(5):
            self.assertEqual(
                self.client.post(url, {"username": "employee", "password": "wrong"}).status_code,
                200,
            )
        self.assertEqual(
            self.client.post(url, {"username": "employee", "password": "wrong"}).status_code, 429
        )

    def test_login_and_logout_do_not_record_actions(self):
        before_events = list(AuditEvent.objects.values_list("pk", flat=True))
        response = self.client.post(
            reverse("login"),
            {"username": "employee", "password": "Test-example-strong-923!"},
        )
        self.assertRedirects(response, reverse("workspace"))
        self.assertIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.post(reverse("logout")).status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(list(AuditEvent.objects.values_list("pk", flat=True)), before_events)
