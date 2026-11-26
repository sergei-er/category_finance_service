import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def migrate_primary_contours(apps, schema_editor):
    Assessment = apps.get_model("assessments", "Assessment")
    AssessmentContour = apps.get_model("assessments", "AssessmentContour")
    CalculationArea = apps.get_model("assessments", "CalculationArea")
    for assessment in Assessment.objects.select_related("contour").iterator():
        AssessmentContour.objects.get_or_create(
            assessment=assessment,
            contour=assessment.contour,
            defaults={"level_snapshot": assessment.level, "name_snapshot": assessment.contour.name},
        )
        area, _ = CalculationArea.objects.get_or_create(
            assessment=assessment,
            level=assessment.level,
            defaults={"title": f"Контуры уровня {assessment.level}"},
        )
        area.contours.add(assessment.contour)


class Migration(migrations.Migration):
    dependencies = [
        ("assessments", "0002_initial"),
        ("organization", "0002_rework_organization"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(model_name="answerrevision", name="employee_score", field=models.DecimalField(blank=True, decimal_places=2, max_digits=3, null=True)),
        migrations.AddField(model_name="assessment", name="purpose", field=models.TextField(blank=True, verbose_name="Цель обследования")),
        migrations.AlterField(model_name="assessment", name="contour", field=models.ForeignKey(help_text="Основной контур; сохранён для совместимости существующих обследований", on_delete=django.db.models.deletion.PROTECT, to="organization.protectioncontour")),
        migrations.AlterField(model_name="assessment", name="state", field=models.CharField(choices=[("draft", "Черновик"), ("filling", "Заполнение"), ("review", "На проверке"), ("rework", "На доработке"), ("agreement", "На согласовании"), ("approved", "Утверждено"), ("closed", "Закрыто")], default="draft", max_length=15)),
        migrations.CreateModel(
            name="AssessmentContour",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("level_snapshot", models.PositiveSmallIntegerField()),
                ("name_snapshot", models.CharField(max_length=200)),
                ("assessment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="contour_links", to="assessments.assessment")),
                ("contour", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="organization.protectioncontour")),
            ],
            options={"ordering": ["level_snapshot", "contour_id"]},
        ),
        migrations.AddConstraint(model_name="assessmentcontour", constraint=models.UniqueConstraint(fields=("assessment", "contour"), name="assessment_contour_unique")),
        migrations.AddConstraint(model_name="assessmentcontour", constraint=models.CheckConstraint(condition=models.Q(("level_snapshot__gte", 1), ("level_snapshot__lte", 3)), name="assessment_contour_level_range")),
        migrations.AddField(model_name="assessment", name="contours", field=models.ManyToManyField(related_name="assessments", through="assessments.AssessmentContour", to="organization.protectioncontour")),
        migrations.AddField(model_name="assessment", name="systems", field=models.ManyToManyField(blank=True, related_name="assessments", to="organization.informationsystem")),
        migrations.CreateModel(
            name="CalculationArea",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("level", models.PositiveSmallIntegerField()),
                ("title", models.CharField(max_length=200)),
                ("assessment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="calculation_areas", to="assessments.assessment")),
                ("contours", models.ManyToManyField(related_name="calculation_areas", to="organization.protectioncontour")),
            ],
        ),
        migrations.AddConstraint(model_name="calculationarea", constraint=models.UniqueConstraint(fields=("assessment", "level"), name="assessment_area_level_unique")),
        migrations.CreateModel(
            name="ScoreAdjustment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("original_score", models.DecimalField(decimal_places=2, max_digits=3)),
                ("proposed_score", models.DecimalField(decimal_places=2, max_digits=3)),
                ("reason", models.TextField()),
                ("status", models.CharField(choices=[("pending", "Ожидает согласования"), ("accepted", "Согласовано"), ("rejected", "Отклонено"), ("superseded", "Заменено новой версией")], default="pending", max_length=15)),
                ("decision_comment", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("decided_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="decided_adjustments", to=settings.AUTH_USER_MODEL)),
                ("proposed_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="proposed_adjustments", to=settings.AUTH_USER_MODEL)),
                ("revision", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="adjustments", to="assessments.answerrevision")),
            ],
            options={"ordering": ["-created_at", "-pk"]},
        ),
        migrations.CreateModel(
            name="Recommendation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("description", models.TextField()),
                ("indicator", models.CharField(blank=True, max_length=200)),
                ("due_date", models.DateField(blank=True, null=True)),
                ("status", models.CharField(choices=[("planned", "Запланировано"), ("in_progress", "В работе"), ("done", "Выполнено")], default="planned", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("assessment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="recommendations", to="assessments.assessment")),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to=settings.AUTH_USER_MODEL)),
                ("finding", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="assessments.finding")),
                ("item", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="assessments.assessmentitem")),
                ("responsible_employee", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to="organization.employee")),
            ],
        ),
        migrations.RunPython(migrate_primary_contours, migrations.RunPython.noop),
    ]
