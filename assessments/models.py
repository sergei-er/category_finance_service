from django.conf import settings
from django.db import models


class Assessment(models.Model):
    class State(models.TextChoices):
        DRAFT = "draft", "Черновик"
        FILLING = "filling", "Заполнение"
        REVIEW = "review", "На проверке"
        REWORK = "rework", "На доработке"
        AGREEMENT = "agreement", "На согласовании"
        APPROVED = "approved", "Утверждено"
        CLOSED = "closed", "Закрыто"

    title = models.CharField("Название", max_length=200)
    contour = models.ForeignKey(
        "organization.ProtectionContour",
        on_delete=models.PROTECT,
        help_text="Основной контур; сохранён для совместимости существующих обследований",
    )
    contours = models.ManyToManyField(
        "organization.ProtectionContour", through="AssessmentContour", related_name="assessments"
    )
    systems = models.ManyToManyField(
        "organization.InformationSystem", blank=True, related_name="assessments"
    )
    catalog = models.ForeignKey("standards.CatalogVersion", on_delete=models.PROTECT)
    period_start = models.DateField("Начало периода")
    period_end = models.DateField("Окончание периода")
    scope = models.TextField("Область обследования")
    purpose = models.TextField("Цель обследования", blank=True)
    processes = models.JSONField(default=list)
    lifecycle_included = models.BooleanField(default=True)
    lifecycle_reason = models.TextField(blank=True)
    level = models.PositiveSmallIntegerField()
    state = models.CharField(max_length=15, choices=State.choices, default=State.DRAFT)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_assessments"
    )
    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="assigned_assessments",
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reviewed_assessments",
    )
    previous = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT)
    organization_snapshot = models.JSONField(default=dict)
    contour_snapshot = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(employee=models.F("reviewer"))
                | models.Q(employee__isnull=True)
                | models.Q(reviewer__isnull=True),
                name="assessment_distinct_participants",
            ),
            models.CheckConstraint(
                condition=models.Q(period_end__gte=models.F("period_start")),
                name="assessment_period_order",
            ),
        ]


class AssessmentContour(models.Model):
    assessment = models.ForeignKey(
        Assessment, on_delete=models.CASCADE, related_name="contour_links"
    )
    contour = models.ForeignKey("organization.ProtectionContour", on_delete=models.PROTECT)
    level_snapshot = models.PositiveSmallIntegerField()
    name_snapshot = models.CharField(max_length=200)

    class Meta:
        ordering = ["level_snapshot", "contour_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["assessment", "contour"], name="assessment_contour_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(level_snapshot__gte=1, level_snapshot__lte=3),
                name="assessment_contour_level_range",
            ),
        ]


class CalculationArea(models.Model):
    assessment = models.ForeignKey(
        Assessment, on_delete=models.CASCADE, related_name="calculation_areas"
    )
    level = models.PositiveSmallIntegerField()
    title = models.CharField(max_length=200)
    contours = models.ManyToManyField(
        "organization.ProtectionContour", related_name="calculation_areas"
    )

    class Meta:
        ordering = ["level", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["assessment", "level"], name="assessment_area_level_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(level__gte=1, level__lte=3), name="calculation_area_level_range"
            ),
        ]


class AssessmentItem(models.Model):
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="items")
    area = models.ForeignKey(
        CalculationArea,
        on_delete=models.PROTECT,
        related_name="items",
        null=True,
        blank=True,
        help_text="Пусто только для исторической анкеты с неопределённой областью",
    )
    requirement = models.ForeignKey("standards.RequirementVersion", on_delete=models.PROTECT)
    context_process = models.PositiveSmallIntegerField(default=0)
    snapshot = models.JSONField(default=dict)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["assessment", "area", "requirement", "context_process"],
                name="assessment_item_area_context",
            ),
            models.UniqueConstraint(
                fields=["assessment", "requirement", "context_process"],
                condition=models.Q(area__isnull=True),
                name="assessment_item_legacy_context",
            ),
        ]

    @property
    def current_revision(self):
        return self.revisions.order_by("-number").first()


class AnswerRevision(models.Model):
    item = models.ForeignKey(AssessmentItem, on_delete=models.PROTECT, related_name="revisions")
    number = models.PositiveIntegerField()
    text = models.TextField()
    employee_score = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True)
    sources = models.TextField(blank=True)
    exclusion_reason = models.TextField(blank=True)
    compensation = models.TextField(blank=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    evidence = models.ManyToManyField("evidence.Evidence", through="AnswerEvidence")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["item", "number"], name="answer_revision_number")
        ]

    @property
    def decision(self):
        return self.decisions.order_by("-pk").first()


class AnswerEvidence(models.Model):
    revision = models.ForeignKey(AnswerRevision, on_delete=models.PROTECT)
    evidence = models.ForeignKey("evidence.Evidence", on_delete=models.PROTECT)
    reference = models.CharField(max_length=250, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["revision", "evidence"], name="answer_evidence_unique")
        ]


class ReviewDecision(models.Model):
    revision = models.ForeignKey(AnswerRevision, on_delete=models.PROTECT, related_name="decisions")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    outcome = models.CharField(
        max_length=15,
        choices=[("accepted", "Принято"), ("rework", "Доработка"), ("excluded", "Не оценивается")],
    )
    score = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True)
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)


class ScoreAdjustment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает согласования"
        ACCEPTED = "accepted", "Согласовано"
        REJECTED = "rejected", "Отклонено"
        SUPERSEDED = "superseded", "Заменено новой версией"

    revision = models.ForeignKey(
        AnswerRevision, on_delete=models.PROTECT, related_name="adjustments"
    )
    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="proposed_adjustments"
    )
    original_score = models.DecimalField(max_digits=3, decimal_places=2)
    proposed_score = models.DecimalField(max_digits=3, decimal_places=2)
    reason = models.TextField()
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="decided_adjustments",
    )
    decision_comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]


class Submission(models.Model):
    assessment = models.ForeignKey(Assessment, on_delete=models.PROTECT, related_name="submissions")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    revisions = models.JSONField()
    digest = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)


class Finding(models.Model):
    assessment = models.ForeignKey(Assessment, on_delete=models.PROTECT, related_name="findings")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Факт №{self.pk}"

    @property
    def current_revision(self):
        return self.revisions.order_by("-number").first()


class FindingRevision(models.Model):
    finding = models.ForeignKey(Finding, on_delete=models.PROTECT, related_name="revisions")
    number = models.PositiveIntegerField()
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    description = models.TextField()
    source = models.CharField(
        max_length=20, choices=[("organization", "Организация"), ("reviewer", "Проверяющий")]
    )
    discovered_on = models.DateField()
    investigation_completed = models.BooleanField(default=False)
    response_documented = models.BooleanField(default=False)
    potential_harm = models.BooleanField(default=False)
    basis = models.TextField()
    evidence = models.ManyToManyField("evidence.Evidence", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["finding", "number"], name="finding_revision_number")
        ]

    @property
    def decision(self):
        return self.decisions.order_by("-pk").first()


class FindingDecision(models.Model):
    revision = models.ForeignKey(
        FindingRevision, on_delete=models.PROTECT, related_name="decisions"
    )
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    include = models.BooleanField()
    duplicate_of = models.ForeignKey(Finding, null=True, blank=True, on_delete=models.PROTECT)
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)


class Recommendation(models.Model):
    assessment = models.ForeignKey(
        Assessment, on_delete=models.PROTECT, related_name="recommendations"
    )
    title = models.CharField(max_length=200)
    description = models.TextField()
    item = models.ForeignKey(AssessmentItem, null=True, blank=True, on_delete=models.PROTECT)
    finding = models.ForeignKey(Finding, null=True, blank=True, on_delete=models.PROTECT)
    indicator = models.CharField(max_length=200, blank=True)
    responsible_employee = models.ForeignKey(
        "organization.Employee", null=True, blank=True, on_delete=models.PROTECT
    )
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=[("planned", "Запланировано"), ("in_progress", "В работе"), ("done", "Выполнено")],
        default="planned",
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
