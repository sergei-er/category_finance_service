from django.test import Client, TestCase
from django.urls import reverse

from auditlog.models import AuditEvent
from organization.models import Employee, InformationSystem, ProtectionContour

from . import test_workbench


class OrganizationDeleteTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        test_workbench.WorkbenchTests.setUpTestData.__func__(cls)
        cls.staff = Employee.objects.create(
            organization=cls.profile, first_name="Иван", last_name="Тестов", position="Инженер"
        )
        cls.system = InformationSystem.objects.create(
            organization=cls.profile, contour=cls.contour, name="Тестовая система"
        )
        cls.spare_contour = ProtectionContour.objects.create(
            organization=cls.profile, name="Свободный контур", level=2
        )

    def test_delete_unused_objects_only_after_confirmation_and_record_audit(self):
        self.client.force_login(self.admin)
        for kind, obj, tab in [
            ("employee", self.staff, "employees"),
            ("system", self.system, "systems"),
            ("contour", self.spare_contour, "systems"),
        ]:
            pk = obj.pk
            url = reverse(kind + "-delete", args=[pk])
            self.assertEqual(self.client.get(url).status_code, 200)
            self.client.post(url, {})
            self.assertTrue(type(obj).objects.filter(pk=pk).exists())
            response = self.client.post(url, {"confirm": "on"})
            self.assertRedirects(response, reverse("organization-card") + "?tab=" + tab)
            self.assertFalse(type(obj).objects.filter(pk=pk).exists())
            self.assertTrue(
                AuditEvent.objects.filter(
                    action="organization.entity_deleted",
                    entity=obj._meta.label_lower,
                    entity_id=str(pk),
                    actor=self.admin,
                ).exists()
            )

    def test_used_system_contour_and_employee_are_not_deleted(self):
        self.assessment.systems.add(self.system)
        self.profile.questionnaire_owner = self.staff
        self.profile.save(update_fields=["questionnaire_owner"])
        self.client.force_login(self.admin)
        count = AuditEvent.objects.count()
        for kind, obj in [
            ("employee", self.staff),
            ("system", self.system),
            ("contour", self.contour),
        ]:
            response = self.client.post(reverse(kind + "-delete", args=[obj.pk]), {"confirm": "on"})
            self.assertContains(response, "Удаление запрещено")
            self.assertTrue(type(obj).objects.filter(pk=obj.pk).exists())
        self.assertTrue(self.assessment.systems.filter(pk=self.system.pk).exists())
        self.assertEqual(AuditEvent.objects.count(), count)

    def test_dependencies_added_after_confirmation_are_checked_again(self):
        url = reverse("employee-delete", args=[self.staff.pk])
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(url), "Подтверждаю удаление")
        self.system.responsible_employee = self.staff
        self.system.save(update_fields=["responsible_employee"])
        self.assertContains(self.client.post(url, {"confirm": "on"}), "Удаление запрещено")
        self.staff.refresh_from_db()

    def test_roles_and_csrf_protect_every_delete_endpoint(self):
        for kind, obj in [
            ("employee", self.staff),
            ("system", self.system),
            ("contour", self.spare_contour),
        ]:
            url = reverse(kind + "-delete", args=[obj.pk])
            for actor in [self.employee, self.reviewer]:
                self.client.force_login(actor)
                self.assertEqual(self.client.get(url).status_code, 403)
                self.assertEqual(self.client.post(url, {"confirm": "on"}).status_code, 403)
            client = Client(enforce_csrf_checks=True)
            client.force_login(self.admin)
            self.assertEqual(client.post(url, {"confirm": "on"}).status_code, 403)
            self.assertTrue(type(obj).objects.filter(pk=obj.pk).exists())
