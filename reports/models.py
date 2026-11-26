from django.conf import settings
from django.db import models


class ResultSnapshot(models.Model):
    assessment = models.OneToOneField(
        "assessments.Assessment", on_delete=models.PROTECT, related_name="result_snapshot"
    )
    calculation = models.OneToOneField("calculations.CalculationRun", on_delete=models.PROTECT)
    data = models.JSONField()
    digest = models.CharField(max_length=64)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class ReportExport(models.Model):
    snapshot = models.ForeignKey(ResultSnapshot, on_delete=models.PROTECT)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    sha256 = models.CharField(max_length=64)
    format = models.CharField(max_length=10)
    created_at = models.DateTimeField(auto_now_add=True)
