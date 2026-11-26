"""Полный цикл отдельного, явно синтетического обследования."""

from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from assessments import services as s
from assessments.models import Assessment
from evidence.services import upload_evidence


class Command(BaseCommand):
    help = "Создать завершённый учебный пример, сохранив исходный черновик"

    def add_arguments(self, parser):
        parser.add_argument("--assessment", type=int, required=True)

    @transaction.atomic
    def handle(self, *args, **options):
        original = Assessment.objects.select_related(
            "catalog", "contour__organization", "employee", "reviewer"
        ).get(pk=options["assessment"])
        if not original.catalog.synthetic or not original.contour.organization.synthetic:
            raise CommandError("Команда работает только с синтетическим стендом")
        if not original.employee or not original.reviewer:
            raise CommandError("Назначьте отдельных участников")
        title = "Завершённый учебный пример с историей исправления"
        existing = Assessment.objects.filter(title=title, catalog=original.catalog).first()
        if existing:
            self.stdout.write(f"Учебный пример уже существует: №{existing.pk}")
            return
        employee, reviewer = original.employee, original.reviewer
        # Назначение выполняет администратор, ранее создавший учебный каталог.
        admin = original.catalog.requirements.first().verified_by
        today = timezone.localdate()
        assessment = s.create_assessment(
            admin,
            title=title,
            contours=list(original.contours.all()) or [original.contour],
            employee=employee,
            reviewer=reviewer,
            catalog=original.catalog,
            period_start=today,
            period_end=today,
            scope="СИНТЕТИЧЕСКИЙ ПРИМЕР: все процессы, один факт и исправление ответа",
            processes=list(range(1, 9)),
            lifecycle_included=True,
        )
        s.begin_assessment(admin, assessment.pk)
        document = upload_evidence(
            employee,
            assessment.pk,
            SimpleUploadedFile(
                "учебный-регламент-v1.txt", "Синтетический регламент v1. Пункт 1.\n".encode()
            ),
            "Учебный документ. Пункт 1 подтверждает несколько учебных ответов.",
        )
        for item in assessment.items.all():
            s.save_answer(
                employee,
                item.pk,
                text="Синтетическое описание реализации",
                score=Decimal("1") if item.snapshot["kind"] == "selection" else Decimal("0.5"),
                files=[document],
                reference="Пункт 1",
            )
        s.submit_assessment(employee, assessment.pk)
        rejected = assessment.items.first()
        for item in assessment.items.all():
            if item.pk == rejected.pk:
                s.review_answer(
                    reviewer,
                    item.current_revision.pk,
                    outcome="rework",
                    score=None,
                    reason="Уточните подтверждение: учебное замечание",
                )
            else:
                score = Decimal("1") if item.snapshot["kind"] == "selection" else Decimal("0.5")
                s.review_answer(
                    reviewer,
                    item.current_revision.pk,
                    outcome="accepted",
                    score=score,
                    reason="Учебная проверка содержания и подтверждения",
                )
        s.return_for_rework(reviewer, assessment.pk, "Исправить один ответ по замечанию")
        updated = upload_evidence(
            employee,
            assessment.pk,
            SimpleUploadedFile(
                "учебный-регламент-v2.txt",
                "Синтетический регламент v2. Уточнённый пункт 1.\n".encode(),
            ),
            "Исправленное учебное подтверждение. Пункт 1.",
        )
        revision = s.save_answer(
            employee,
            rejected.pk,
            text="Уточнённая синтетическая реализация",
            score=Decimal("1") if rejected.snapshot["kind"] == "selection" else Decimal("0.5"),
            files=[updated],
            reference="Уточнённый пункт 1",
        )
        s.submit_assessment(employee, assessment.pk)
        score = Decimal("1") if rejected.snapshot["kind"] == "selection" else Decimal("0.5")
        s.review_answer(
            reviewer,
            revision.pk,
            outcome="accepted",
            score=score,
            reason="Повторно проверена только изменённая версия",
        )
        fact = s.save_finding(
            reviewer,
            assessment.pk,
            description="Синтетический учитываемый факт",
            source="reviewer",
            discovered_on=today,
            investigation_completed=False,
            response_documented=False,
            potential_harm=True,
            basis="Учебный факт для демонстрации Z=1; не реальное нарушение",
        )
        s.review_finding(
            reviewer, fact.pk, include=True, reason="Учебный пример учёта одного факта"
        )
        snapshot = s.approve_assessment(reviewer, assessment.pk)
        s.close_assessment(reviewer, assessment.pk)
        self.stdout.write(
            f"Закрыт учебный пример №{assessment.pk}; R={snapshot.data['result']['result']}"
        )
