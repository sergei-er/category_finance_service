from django.conf import settings
from django.db import models


class CalculationRun(models.Model):
    assessment = models.ForeignKey(
        "assessments.Assessment", on_delete=models.PROTECT, related_name="calculations"
    )
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    inputs = models.JSONField()
    result = models.JSONField()
    digest = models.CharField(max_length=64)
    algorithm_version = models.CharField(max_length=50, default="gost-prototype-1")
    created_at = models.DateTimeField(auto_now_add=True)
