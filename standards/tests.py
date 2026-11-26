from copy import deepcopy

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from accounts.access import ADMIN
from accounts.roles import setup_roles

from .demo import demo_payload
from .models import CatalogVersion
from .services import import_catalog, verify_catalog


class ImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        setup_roles()
        cls.admin = get_user_model().objects.create_user("import-admin")
        cls.admin.groups.add(Group.objects.get(name=ADMIN))
        cls.other = get_user_model().objects.create_user("other")

    def test_untrusted_verified_flag_ignored(self):
        payload = demo_payload()
        payload["verified"] = True
        for row in payload["requirements"]:
            row["verified"] = True
        catalog = import_catalog(self.admin, payload)
        self.assertFalse(catalog.verified)
        self.assertFalse(catalog.requirements.filter(verified=True).exists())
        with self.assertRaises(ValidationError):
            verify_catalog(self.admin, catalog.pk)

    def test_invalid_payload_rolls_back(self):
        good = demo_payload()
        malformed = [[], dict(good, title=[]), dict(good, source=42)]
        for field, value in [
            ("levels", {"1": [], "2": "О", "3": "О"}),
            ("process", 10),
            ("citation", ""),
        ]:
            payload = deepcopy(good)
            payload["requirements"][0][field] = value
            malformed.append(payload)
        for payload in malformed:
            with self.subTest(payload_type=type(payload)), self.assertRaises(ValidationError):
                import_catalog(self.admin, payload)
            self.assertEqual(CatalogVersion.objects.count(), 0)

    def test_import_admin_only(self):
        with self.assertRaises(PermissionDenied):
            import_catalog(self.other, demo_payload())
