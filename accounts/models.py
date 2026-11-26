from django.db import models


class LoginAttempt(models.Model):
    identity = models.CharField(max_length=64, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
