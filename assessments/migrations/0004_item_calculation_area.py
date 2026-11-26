import django.db.models.deletion
from django.db import migrations, models


def link_existing_items(apps, schema_editor):
    """Не клонировать старые ответы и не менять подписанные JSON-снимки."""
    Assessment = apps.get_model("assessments", "Assessment")
    Item = apps.get_model("assessments", "AssessmentItem")
    alias = schema_editor.connection.alias
    for assessment in Assessment.objects.using(alias).all().iterator():
        areas = list(assessment.calculation_areas.using(alias).all())
        levels = set(assessment.contour_links.using(alias).values_list("level_snapshot", flat=True))
        if len(areas) == 1 and levels == {areas[0].level}:
            Item.objects.using(alias).filter(assessment_id=assessment.pk).update(
                area_id=areas[0].pk
            )
        # Старый многоконтурный набор объединял уровни. Его нельзя автоматически
        # превратить в несколько рассмотренных анкет: оставляем area=NULL.


class Migration(migrations.Migration):
    dependencies = [("assessments", "0003_rework_assessment")]

    operations = [
        migrations.AlterModelOptions(name="calculationarea", options={"ordering": ["level", "pk"]}),
        migrations.AddConstraint(
            model_name="calculationarea",
            constraint=models.CheckConstraint(
                condition=models.Q(level__gte=1, level__lte=3), name="calculation_area_level_range"
            ),
        ),
        migrations.AddField(
            model_name="assessmentitem",
            name="area",
            field=models.ForeignKey(
                to="assessments.calculationarea",
                on_delete=django.db.models.deletion.PROTECT,
                related_name="items",
                null=True,
                blank=True,
                help_text="Пусто только для исторической анкеты с неопределённой областью",
            ),
        ),
        migrations.RemoveConstraint(model_name="assessmentitem", name="assessment_item_context"),
        migrations.AddConstraint(
            model_name="assessmentitem",
            constraint=models.UniqueConstraint(
                fields=["assessment", "area", "requirement", "context_process"],
                name="assessment_item_area_context",
            ),
        ),
        migrations.AddConstraint(
            model_name="assessmentitem",
            constraint=models.UniqueConstraint(
                fields=["assessment", "requirement", "context_process"],
                condition=models.Q(area__isnull=True),
                name="assessment_item_legacy_context",
            ),
        ),
        migrations.RunPython(link_existing_items, migrations.RunPython.noop),
    ]
