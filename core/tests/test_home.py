from django.test import SimpleTestCase, override_settings
from django.urls import reverse


class HomeTests(SimpleTestCase):
    def test_home_renders_without_database(self):
        response = self.client.get(reverse("core:home"))
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertEqual(response["X-Frame-Options"], "DENY")

    def test_home_rejects_mutating_method(self):
        self.assertEqual(self.client.post(reverse("core:home")).status_code, 405)

    def test_unlisted_host_is_rejected(self):
        self.assertEqual(self.client.get("/", HTTP_HOST="untrusted.invalid").status_code, 400)

    def test_no_public_registration_or_file_serving(self):
        for path in ["/register/", "/media/private.pdf", "/evidence/private.pdf"]:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    @override_settings(DEVELOPMENT_MODE=False)
    def test_development_banner_is_not_shown_in_other_mode(self):
        response = self.client.get("/")
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
