import django.db.models.deletion
from django.db import migrations, models

import evidence.models


class Migration(migrations.Migration):
    dependencies = [("evidence", "0001_initial"), ("organization", "0002_rework_organization")]

    operations = [
        migrations.AlterField(model_name="evidence", name="file", field=models.FileField(blank=True, max_length=250, null=True, upload_to=evidence.models.evidence_path)),
        migrations.AddField(model_name="evidence", name="title", field=models.CharField(blank=True, max_length=200)),
        migrations.AddField(model_name="evidence", name="kind", field=models.CharField(choices=[("document", "Документ"), ("interview", "Результат опроса сотрудника"), ("observation", "Результат наблюдения"), ("configuration", "Параметры конфигурации"), ("instrumental", "Результаты инструментальной проверки")], default="document", max_length=20)),
        migrations.AddField(model_name="evidence", name="source", field=models.CharField(blank=True, max_length=250)),
        migrations.AddField(model_name="evidence", name="provided_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="organization.employee")),
        migrations.AddField(model_name="evidence", name="received_on", field=models.DateField(blank=True, null=True)),
        migrations.AddField(model_name="evidence", name="review_status", field=models.CharField(choices=[("provided", "Предоставлено"), ("accepted", "Принято"), ("rejected", "Отклонено"), ("supplement", "Требуется дополнение")], default="provided", max_length=20)),
    ]
