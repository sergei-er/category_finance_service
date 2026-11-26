from urllib.parse import urlsplit

from django.test import Client, TestCase
from django.urls import reverse
from lxml import html

from assessments.forms import AssessmentForm
from assessments.models import AnswerRevision
from standards.models import CatalogVersion, RequirementVersion

from . import test_workbench


class RedesignedWorkspaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_workbench.WorkbenchTests.setUpTestData.__func__(cls)

    def test_questionnaire_has_three_panels_and_valid_inline_form_association(self):
        self.client.force_login(self.employee)
        response = self.client.get(reverse("assessment", args=[self.assessment.pk]))
        tree = html.fromstring(response.content)
        self.assertTrue(tree.xpath('//*[@class="workbench"]'))
        self.assertTrue(tree.xpath('//aside[@class="process-nav"]'))
        self.assertTrue(tree.xpath('//aside[@class="progress-panel"]'))
        self.assertEqual(len(tree.xpath('//ol[@class="workflow"]/li')), 5)
        for select in tree.xpath('//select[@name="score"]'):
            form_id = select.get("form")
            forms = tree.xpath("//form[@id=$value]", value=form_id)
            self.assertEqual(len(forms), 1)
            self.assertTrue(forms[0].xpath('.//input[@name="csrfmiddlewaretoken"]'))
            self.assertTrue(forms[0].xpath('.//textarea[@name="text"]'))
        ids = tree.xpath("//@id")
        self.assertEqual(len(ids), len(set(ids)))

    def test_inline_answer_preserves_zero_and_returns_to_edited_measure(self):
        self.client.force_login(self.employee)
        item = self.assessment.items.filter(snapshot__kind="selection").first()
        response = self.client.post(
            reverse("answer", args=[item.pk]),
            {
                "score": "0",
                "text": "Мера не выбрана",
                "sources": "Учебный регламент",
                "return_query": "tab=scores&process=3&kind=selection",
            },
        )
        self.assertEqual(response.status_code, 302)
        revision = item.current_revision
        self.assertEqual(revision.employee_score, 0)
        location = urlsplit(response["Location"])
        self.assertEqual(location.path, reverse("assessment", args=[self.assessment.pk]))
        self.assertEqual(location.fragment, f"measure-{item.pk}")
        self.assertIn("process=3", location.query)

    def test_inline_answer_checks_role_scale_and_csrf(self):
        item = self.assessment.items.filter(snapshot__kind="selection").first()
        url = reverse("answer", args=[item.pk])
        payload = {"score": "1", "text": "Тест", "sources": "Документ"}
        for actor in [self.other, self.reviewer, self.admin]:
            self.client.force_login(actor)
            self.assertEqual(self.client.post(url, payload).status_code, 403)
        self.client.force_login(self.employee)
        self.assertEqual(self.client.post(url, payload | {"score": "0.5"}).status_code, 200)
        self.assertFalse(AnswerRevision.objects.exists())
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.employee)
        self.assertEqual(client.post(url, payload).status_code, 403)

    def test_synthetic_catalog_hidden_without_deleting_history(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("settings-home"))
        self.assertNotContains(response, self.catalog.title)
        self.assertFalse(
            AssessmentForm().fields["catalog"].queryset.filter(pk=self.catalog.pk).exists()
        )
        self.assertRedirects(
            self.client.get(reverse("catalog", args=[self.catalog.pk])),
            reverse("settings-home"),
            fetch_redirect_response=False,
        )
        self.assertTrue(CatalogVersion.objects.filter(pk=self.catalog.pk).exists())
        self.assertTrue(self.assessment.items.exists())

    def test_reports_and_search_do_not_show_unfinished_or_unassigned_assessments(self):
        self.client.force_login(self.employee)
        response = self.client.get(reverse("workspace"), {"view": "reports"})
        self.assertNotContains(response, self.assessment.title)
        response = self.client.get(reverse("workspace"), {"q": "Нет такого обследования"})
        self.assertNotContains(response, self.assessment.title)
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(reverse("workspace")), self.assessment.title)

    def test_catalog_pagination_and_search(self):
        catalog = CatalogVersion.objects.create(title="Тест навигации каталога", source="Тест")
        RequirementVersion.objects.bulk_create(
            [
                RequirementVersion(
                    catalog=catalog,
                    code=f"TEST-{i:02}",
                    text=f"Тестовая запись {i}",
                    kind="selection",
                    process=3,
                    citation="Тест",
                )
                for i in range(35)
            ]
        )
        self.client.force_login(self.admin)
        url = reverse("catalog", args=[catalog.pk])
        response = self.client.get(url)
        self.assertEqual(len(response.context["requirements"]), 30)
        response = self.client.get(url, {"q": "TEST-34"})
        self.assertEqual(len(response.context["requirements"]), 1)
        self.assertContains(response, "TEST-34")
