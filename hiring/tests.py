from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta

# Django 5.1.6 + Python 3.14 incompatibility fix (same as crm/tests.py)
from django.template.context import BaseContext, Context

def _safe_context_copy(self):
    duplicate = BaseContext(None)
    duplicate.dicts = list(self.dicts)
    if isinstance(self, Context):
        duplicate.template_name = getattr(self, "template_name", None)
        duplicate.render_context = getattr(self, "render_context", None)
    return duplicate

Context.__copy__ = _safe_context_copy

from crm.models import StaffProfile

from .models import CandidateApplication, HiringMeeting, MeetingAttendee
from .services import (
    create_application,
    hire_candidate,
    schedule_meeting,
    parse_datetime,
)


class HiringBaseTestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner1", password="x")
        StaffProfile.objects.create(user=self.owner, role="owner")
        self.manager = User.objects.create_user(username="manager1", password="x")
        StaffProfile.objects.create(user=self.manager, role="manager")
        self.staff = User.objects.create_user(username="staff1", password="x")
        StaffProfile.objects.create(user=self.staff, role="staff")


class ApplicationServiceTests(HiringBaseTestCase):
    def test_create_application_dedupe_by_email(self):
        app, created = create_application(name="Rahim", email="rahim@x.com", phone="01711111111")
        self.assertTrue(created)
        self.assertEqual(app.phone, "+8801711111111")  # normalized
        dup, created = create_application(name="Rahim Again", email="rahim@x.com", phone="01711")
        self.assertFalse(created)
        self.assertEqual(dup.pk, app.pk)

    def test_create_application_rejected_email_can_reapply(self):
        app, created = create_application(name="A", email="a@x.com")
        app.status = "rejected"
        app.save(update_fields=["status"])
        new, created = create_application(name="A", email="a@x.com")
        self.assertTrue(created)
        self.assertNotEqual(new.pk, app.pk)


class HireServiceTests(HiringBaseTestCase):
    def setUp(self):
        super().setUp()
        self.app, _ = create_application(name="Sakib Hasan", email="sakib@x.com", phone="01822222222")

    def test_hire_creates_staff_account(self):
        user, password = hire_candidate(candidate=self.app, role="staff", temp_password="secret123")
        self.assertIsNotNone(user.pk)
        self.assertTrue(user.check_password("secret123"))
        sp = StaffProfile.objects.get(user=user)
        self.assertEqual(sp.role, "staff")
        self.assertTrue(sp.is_active)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, "hired")
        self.assertEqual(self.app.hired_user, user)

    def test_hire_is_idempotent_by_email(self):
        user1, _ = hire_candidate(candidate=self.app, role="staff")
        user2, _ = hire_candidate(candidate=self.app, role="manager")
        self.assertEqual(user1.pk, user2.pk)
        StaffProfile.objects.get(user=user1).refresh_from_db()
        self.assertEqual(StaffProfile.objects.filter(user=user1).count(), 1)


class MeetingTests(HiringBaseTestCase):
    def setUp(self):
        super().setUp()
        self.a, _ = create_application(name="A", email="a@x.com")
        self.b, _ = create_application(name="B", email="b@x.com")

    def test_bulk_meeting_invites_and_marks_interview(self):
        when = timezone.now() + timedelta(days=2)
        meeting = schedule_meeting(
            title="Group Interview", when=when, platform="zoom",
            link="https://meet.google.com/_", candidates=[self.a, self.b],
        )
        self.assertEqual(HiringMeeting.objects.count(), 1)
        self.assertEqual(meeting.attendees.count(), 2)
        self.assertEqual(MeetingAttendee.objects.get(candidate=self.a).rsvp, "invited")
        self.a.refresh_from_db()
        self.b.refresh_from_db()
        self.assertEqual(self.a.status, "interview_scheduled")
        self.assertEqual(self.b.status, "interview_scheduled")

    def test_parse_datetime(self):
        dt = parse_datetime("2026-08-10T14:30")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.minute, 30)
        self.assertIsNone(parse_datetime(""))


class PermissionTests(HiringBaseTestCase):
    def test_staff_cannot_access_hiring(self):
        self.client.login(username="staff1", password="x")
        resp = self.client.get("/crm/hiring/")
        self.assertEqual(resp.status_code, 403)

    def test_owner_can_access_hiring(self):
        self.client.login(username="owner1", password="x")
        resp = self.client.get("/crm/hiring/")
        self.assertEqual(resp.status_code, 200)

    def test_public_apply_creates_application(self):
        resp = self.client.post("/careers/", {
            "name": "Test Candidate",
            "email": "cand@x.com",
            "phone": "01933333333",
            "position": "sales_executive",
            "experience_years": "3",
            "skills": "cold calling, closing",
        })
        self.assertEqual(resp.status_code, 200)
        from .models import CandidateApplication
        self.assertTrue(CandidateApplication.objects.filter(email="cand@x.com").exists())