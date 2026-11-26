from datetime import date

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class AreaMigrationTests(TransactionTestCase):
    def test_existing_single_level_history_is_preserved_and_ambiguous_history_is_not_copied(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        before = [node for node in latest if node[0] != "assessments"] + [
            ("assessments", "0003_rework_assessment")
        ]
        executor.migrate(before)
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        apps = executor.loader.project_state(before).apps
        user = apps.get_model("auth", "User").objects.create(username="migration-owner")
        profile = apps.get_model("organization", "OrganizationProfile").objects.create(name="Тест")
        contour = apps.get_model("organization", "ProtectionContour").objects.create(
            organization=profile,
            name="Исходный контур",
            level=3,
        )
        catalog = apps.get_model("standards", "CatalogVersion").objects.create(title="Миграция")
        requirement = apps.get_model("standards", "RequirementVersion").objects.create(
            catalog=catalog,
            code="TEST",
            text="Синтетическая мера",
            kind="selection",
            process=3,
        )
        assessment_model = apps.get_model("assessments", "Assessment")
        area_model = apps.get_model("assessments", "CalculationArea")
        link_model = apps.get_model("assessments", "AssessmentContour")
        item_model = apps.get_model("assessments", "AssessmentItem")
        saved = []
        for multilevel in [False, True]:
            assessment = assessment_model.objects.create(
                title="Историческое обследование",
                contour=contour,
                catalog=catalog,
                period_start=date(2026, 1, 1),
                period_end=date(2026, 2, 1),
                scope="Тест",
                level=2,
                processes=[3],
                created_by=user,
                state="closed",
                organization_snapshot={"name": "Имя до изменения"},
                contour_snapshot={"name": "Сохранённое имя", "level": 2},
            )
            # Живой contour.level уже 3; перенос должен использовать сохранённый уровень 2.
            link_model.objects.create(
                assessment=assessment,
                contour=contour,
                level_snapshot=2,
                name_snapshot="Сохранённое имя",
            )
            area = area_model.objects.create(assessment=assessment, level=2, title="Группа 2")
            area.contours.add(contour)
            if multilevel:
                other = apps.get_model("organization", "ProtectionContour").objects.create(
                    organization=profile,
                    name="Второй контур",
                    level=1,
                )
                link_model.objects.create(
                    assessment=assessment, contour=other, level_snapshot=1, name_snapshot=other.name
                )
                area_model.objects.create(assessment=assessment, level=1, title="Группа 1")
            item = item_model.objects.create(
                assessment=assessment,
                requirement=requirement,
                context_process=3,
                snapshot={"level": 2, "code": "TEST"},
            )
            revision = apps.get_model("assessments", "AnswerRevision").objects.create(
                item=item,
                number=1,
                text="Сохранённый ответ",
                author=user,
            )
            run = apps.get_model("calculations", "CalculationRun").objects.create(
                assessment=assessment,
                actor=user,
                inputs={"legacy": True},
                result={"result": "0.50"},
                digest="unchanged-run",
            )
            snapshot = apps.get_model("reports", "ResultSnapshot").objects.create(
                assessment=assessment,
                calculation=run,
                approved_by=user,
                data={"historic": "Не пересчитывать"},
                digest="unchanged-snapshot",
            )
            saved.append((item.pk, area.pk, revision.pk, snapshot.pk, multilevel))
        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        apps = executor.loader.project_state(latest).apps
        for item_id, area_id, revision_id, snapshot_id, multilevel in saved:
            item = apps.get_model("assessments", "AssessmentItem").objects.get(pk=item_id)
            self.assertEqual(item.area_id, None if multilevel else area_id)
            self.assertEqual(item.snapshot, {"level": 2, "code": "TEST"})
            self.assertEqual(item.assessment.state, "closed")
            revision = apps.get_model("assessments", "AnswerRevision").objects.get(pk=revision_id)
            self.assertEqual(revision.item_id, item_id)
            self.assertEqual(revision.text, "Сохранённый ответ")
            snapshot = apps.get_model("reports", "ResultSnapshot").objects.get(pk=snapshot_id)
            self.assertEqual(snapshot.data, {"historic": "Не пересчитывать"})
            self.assertEqual(snapshot.digest, "unchanged-snapshot")
        self.assertEqual(apps.get_model("assessments", "AssessmentItem").objects.count(), 2)
