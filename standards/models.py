from django.conf import settings
from django.db import models


class CatalogVersion(models.Model):
    title = models.CharField("Название и редакция", max_length=250, unique=True)
    source = models.TextField("Источник и редакция")
    synthetic = models.BooleanField("Синтетический учебный каталог", default=False)
    verified = models.BooleanField("Верификация завершена", default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class RequirementVersion(models.Model):
    class Kind(models.TextChoices):
        SELECTION = "selection", "Выбор меры (раздел 7)"
        MANAGEMENT = "management", "Управление (раздел 8)"
        LIFECYCLE = "lifecycle", "Жизненный цикл (раздел 9)"

    catalog = models.ForeignKey(
        CatalogVersion, on_delete=models.PROTECT, related_name="requirements"
    )
    code = models.CharField("Код", max_length=80)
    text = models.TextField("Формулировка")
    kind = models.CharField("Показатель", max_length=20, choices=Kind.choices)
    process = models.PositiveSmallIntegerField(null=True, blank=True)
    subprocess = models.PositiveSmallIntegerField(null=True, blank=True)
    direction = models.PositiveSmallIntegerField(null=True, blank=True)
    stage = models.CharField(max_length=150, blank=True)
    citation = models.CharField("Раздел, пункт, таблица и страница", max_length=250)
    verified = models.BooleanField(default=False)
    verification_note = models.TextField(blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["catalog", "code"], name="catalog_requirement_code")
        ]
        ordering = ["kind", "process", "subprocess", "direction", "code"]

    def __str__(self):
        return self.code


class ImplementationRequirement(models.Model):
    requirement = models.ForeignKey(
        RequirementVersion, on_delete=models.CASCADE, related_name="implementations"
    )
    level = models.PositiveSmallIntegerField()
    mode = models.CharField(
        max_length=1,
        choices=[("О", "Организационная"), ("Т", "Техническая"), ("Н", "Необязательная")],
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["requirement", "level"], name="requirement_level_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(level__gte=1, level__lte=3), name="implementation_level_range"
            ),
            models.CheckConstraint(
                condition=models.Q(mode__in=["О", "Т", "Н"]), name="implementation_mode_valid"
            ),
        ]
