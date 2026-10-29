from django.test import TestCase, override_settings
from django.contrib.auth.models import User
from django.urls import reverse
from front.models import EmailVerification


class SignupTests(TestCase):

    def test_signup_page_loads(self):
        response = self.client.get(reverse("front:signup"))
        self.assertEqual(response.status_code, 200)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", FRONT_EMAIL_VERIFICATION_REQUIRED=True)
    def test_signup_creates_inactive_user_and_sends_email(self):
        response = self.client.post(reverse("front:signup"), {
            "username": "testuser",
            "email": "test@example.com",
            "password": "StrongPass123!",
            "confirm_password": "StrongPass123!",
        })
        self.assertRedirects(response, reverse("front:verify_email", args=["testuser"]))
        user = User.objects.get(username="testuser")
        self.assertFalse(user.is_active)
        self.assertEqual(user.email, "test@example.com")
        self.assertTrue(EmailVerification.objects.filter(user=user).exists())

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_signup_rejects_mismatched_passwords(self):
        response = self.client.post(reverse("front:signup"), {
            "username": "testuser",
            "email": "test@example.com",
            "password": "StrongPass123!",
            "confirm_password": "DifferentPass456!",
        })
        self.assertRedirects(response, reverse("front:signup"))

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_signup_rejects_duplicate_username(self):
        User.objects.create_user(username="testuser", email="a@a.com", password="Test1234!")
        response = self.client.post(reverse("front:signup"), {
            "username": "testuser",
            "email": "b@b.com",
            "password": "StrongPass123!",
            "confirm_password": "StrongPass123!",
        })
        self.assertRedirects(response, reverse("front:signup"))

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_signup_rejects_duplicate_email(self):
        User.objects.create_user(username="user1", email="dup@example.com", password="Test1234!")
        response = self.client.post(reverse("front:signup"), {
            "username": "user2",
            "email": "dup@example.com",
            "password": "StrongPass123!",
            "confirm_password": "StrongPass123!",
        })
        self.assertRedirects(response, reverse("front:signup"))

    def test_signup_creates_active_user_when_verification_disabled(self):
        response = self.client.post(reverse("front:signup"), {
            "username": "testuser",
            "email": "test@example.com",
            "password": "StrongPass123!",
            "confirm_password": "StrongPass123!",
        })
        self.assertNotEqual(response.url, reverse("front:signup"))
        self.assertNotEqual(response.url, reverse("front:verify_email", args=["testuser"]))
        user = User.objects.get(username="testuser")
        self.assertTrue(user.is_active)
        self.assertFalse(EmailVerification.objects.filter(user=user).exists())


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", FRONT_EMAIL_VERIFICATION_REQUIRED=True)
class VerifyEmailTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", password="StrongPass123!",
            is_active=False,
        )
        self.verification = EmailVerification.objects.create(
            user=self.user, code="123456"
        )

    def test_verify_page_loads(self):
        response = self.client.get(reverse("front:verify_email", args=["testuser"]))
        self.assertEqual(response.status_code, 200)

    def test_verify_with_correct_code_activates_user(self):
        response = self.client.post(
            reverse("front:verify_email", args=["testuser"]), {"code": "123456"}
        )
        self.assertRedirects(response, reverse("front:login"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_active)
        self.verification.refresh_from_db()
        self.assertTrue(self.verification.is_used)

    def test_verify_with_wrong_code_shows_error(self):
        response = self.client.post(
            reverse("front:verify_email", args=["testuser"]), {"code": "000000"}, follow=True
        )
        self.assertContains(response, "Incorrect")

    def test_already_verified_skips_to_login(self):
        self.user.is_active = True
        self.user.save(update_fields=["is_active"])
        response = self.client.get(reverse("front:verify_email", args=["testuser"]))
        self.assertRedirects(response, reverse("front:login"))

    def test_verify_too_many_attempts_resets_code(self):
        self.verification.attempts = 5
        self.verification.save(update_fields=["attempts"])
        response = self.client.post(
            reverse("front:verify_email", args=["testuser"]), {"code": "000000"}
        )
        self.verification.refresh_from_db()
        self.assertEqual(self.verification.attempts, 0)
        self.assertNotEqual(self.verification.code, "123456")

    def test_resend_verification(self):
        response = self.client.get(reverse("front:resend_verification", args=["testuser"]))
        self.assertRedirects(response, reverse("front:verify_email", args=["testuser"]))
        self.verification.refresh_from_db()
        self.assertEqual(self.verification.attempts, 0)


class LoginTests(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(
            username="testuser", email="test@example.com", password="StrongPass123!",
            is_active=True,
        )

    def test_login_page_loads(self):
        response = self.client.get(reverse("front:login"))
        self.assertEqual(response.status_code, 200)

    def test_login_with_username(self):
        response = self.client.post(reverse("front:login"), {
            "username": "testuser", "password": "StrongPass123!",
        })
        self.assertNotEqual(response.url, reverse("front:login"))

    def test_login_with_email(self):
        response = self.client.post(reverse("front:login"), {
            "username": "test@example.com", "password": "StrongPass123!",
        })
        self.assertNotEqual(response.url, reverse("front:login"))

    @override_settings(FRONT_EMAIL_VERIFICATION_REQUIRED=True)
    def test_login_blocks_unverified_user(self):
        unverified = User.objects.create_user(
            username="unv", email="un@test.com", password="Test1234!", is_active=False,
        )
        EmailVerification.objects.create(user=unverified, code="111111")
        response = self.client.post(reverse("front:login"), {
            "username": "unv", "password": "Test1234!",
        })
        self.assertRedirects(response, reverse("front:verify_email", args=["unv"]))

    def test_login_invalid_credentials(self):
        response = self.client.post(reverse("front:login"), {
            "username": "testuser", "password": "wrongpassword",
        })
        self.assertContains(response, "Invalid")


class EmailVerificationModelTests(TestCase):

    def test_code_is_6_digits(self):
        user = User.objects.create_user(username="u", email="u@u.com", password="p")
        ev = EmailVerification.objects.create(user=user, code="654321")
        self.assertEqual(len(ev.code), 6)
        self.assertTrue(ev.code.isdigit())

    def test_defaults(self):
        user = User.objects.create_user(username="u", email="u@u.com", password="p")
        ev = EmailVerification.objects.create(user=user, code="999999")
        self.assertFalse(ev.is_used)
        self.assertEqual(ev.attempts, 0)