import uuid

from django.conf import settings
from django.db import models


def evidence_path(instance, filename):
    return f"{instance.assessment_id}/{uuid.uuid4().hex}"


class Evidence(models.Model):
    class Kind(models.TextChoices):
        DOCUMENT = "document", "Документ"
        INTERVIEW = "interview", "Результат опроса сотрудника"
        OBSERVATION = "observation", "Результат наблюдения"
        CONFIGURATION = "configuration", "Параметры конфигурации"
        INSTRUMENTAL = "instrumental", "Результаты инструментальной проверки"

    assessment = models.ForeignKey(
        "assessments.Assessment", on_delete=models.PROTECT, related_name="evidence_files"
    )
    file = models.FileField(upload_to=evidence_path, max_length=250, null=True, blank=True)
    title = models.CharField(max_length=200, blank=True)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.DOCUMENT)
    source = models.CharField(max_length=250, blank=True)
    provided_by = models.ForeignKey(
        "organization.Employee", null=True, blank=True, on_delete=models.PROTECT
    )
    received_on = models.DateField(null=True, blank=True)
    review_status = models.CharField(
        max_length=20,
        choices=[
            ("provided", "Предоставлено"), ("accepted", "Принято"),
            ("rejected", "Отклонено"), ("supplement", "Требуется дополнение"),
        ],
        default="provided",
    )
    original_name = models.CharField(max_length=180)
    content_type = models.CharField(max_length=120)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    description = models.TextField("Что подтверждает документ, раздел/страница")
    retired = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"№{self.pk}: {self.original_name}"
