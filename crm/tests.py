from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta

# Django 5.1.6 + Python 3.14 incompatibility: Context.__copy__ calls
# copy(super()) which raises AttributeError when the test client stores
# rendered template contexts. Patch with a shallow-copy implementation.
from django.template.context import BaseContext, Context

def _safe_context_copy(self):
    duplicate = BaseContext(None)
    duplicate.dicts = list(self.dicts)
    if isinstance(self, Context):
        duplicate.template_name = getattr(self, "template_name", None)
        duplicate.render_context = getattr(self, "render_context", None)
    return duplicate

Context.__copy__ = _safe_context_copy

from crm.models import (
    StaffProfile, PipelineStage, Lead, Company, Activity, Customer,
    CallLog, Meeting, Task, Followup, Notification,
)
from crm.services import (
    create_lead, update_lead, add_note, convert_lead, complete_followup,
    lead_queryset_for, get_role,
)


class CrmBaseTestCase(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner1", password="x")
        StaffProfile.objects.create(user=self.owner, role="owner")
        self.manager = User.objects.create_user(username="manager1", password="x")
        StaffProfile.objects.create(user=self.manager, role="manager")
        self.staff = User.objects.create_user(username="staff1", password="x")
        StaffProfile.objects.create(user=self.staff, role="staff")
        self.support = User.objects.create_user(username="support1", password="x")
        StaffProfile.objects.create(user=self.support, role="support")

        self.new_stage = PipelineStage.objects.create(name="New Leads", order=0)
        self.won_stage = PipelineStage.objects.create(name="Won", order=80, is_won=True)
        self.lost_stage = PipelineStage.objects.create(name="Lost", order=90, is_lost=True)


class ServicesTests(CrmBaseTestCase):
    def test_create_lead_with_dedupe(self):
        lead, created = create_lead(self.owner, name="Rahim", phone="+8801711")
        self.assertTrue(created)
        dup, created = create_lead(self.owner, name="Rahim Dup", phone="+8801711")
        self.assertFalse(created)
        self.assertEqual(dup.pk, lead.pk)
        self.assertEqual(lead.stage, self.new_stage)
        self.assertTrue(lead.activities.filter(type="created").exists())

    def test_create_lead_dedupe_by_email(self):
        lead, created = create_lead(self.owner, name="A", email="a@b.com")
        self.assertTrue(created)
        dup, created = create_lead(self.owner, name="B", email="a@b.com")
        self.assertFalse(created)
        self.assertEqual(dup.pk, lead.pk)

    def test_update_lead_stage_change_logs_activity(self):
        lead, _ = create_lead(self.owner, name="Rahim", phone="+8801711")
        update_lead(self.staff, lead, stage=self.won_stage)
        self.assertTrue(lead.activities.filter(type="status_change").exists())
        self.assertTrue(lead.activities.filter(type="won").exists())
        lead.refresh_from_db()
        self.assertTrue(lead.converted)
        self.assertTrue(Customer.objects.filter(lead=lead).exists())

    def test_update_lead_assignment_logs_and_notifies(self):
        lead, _ = create_lead(self.owner, name="Rahim", phone="+8801711")
        update_lead(self.manager, lead, assigned_to=self.staff)
        self.assertTrue(lead.activities.filter(type="assignment").exists())
        self.assertTrue(Notification.objects.filter(user=self.staff).exists())

    def test_update_lead_lost(self):
        lead, _ = create_lead(self.owner, name="Rahim", phone="+8801711")
        update_lead(self.staff, lead, stage=self.lost_stage)
        self.assertTrue(lead.activities.filter(type="lost").exists())
        lead.refresh_from_db()
        self.assertFalse(lead.converted)

    def test_add_note(self):
        lead, _ = create_lead(self.owner, name="Rahim", phone="+8801711")
        add_note(self.staff, lead, "Called twice")
        self.assertTrue(lead.activities.filter(type="note", description="Called twice").exists())
        self.assertIsNone(add_note(self.staff, lead, "   "))

    def test_convert_lead_creates_platform_user(self):
        lead, _ = create_lead(self.owner, name="Karim", email="karim@x.com", phone="+8801722")
        customer = convert_lead(self.owner, lead, package="pro", monthly_value=5990)
        self.assertEqual(customer.lead, lead)
        self.assertIsNotNone(customer.platform_user)
        self.assertEqual(customer.platform_user.email, "karim@x.com")
        lead.refresh_from_db()
        self.assertTrue(lead.converted)
        self.assertTrue(lead.activities.filter(type="won").exists())
        # idempotent
        customer2 = convert_lead(self.owner, lead, package="pro")
        self.assertEqual(customer2.pk, customer.pk)

    def test_complete_followup(self):
        lead, _ = create_lead(self.owner, name="Rahim", phone="+8801711")
        f = Followup.objects.create(lead=lead, due=timezone.now(), kind="call")
        complete_followup(self.staff, f, lead)
        f.refresh_from_db()
        self.assertTrue(f.done)
        lead.refresh_from_db()
        self.assertIsNotNone(lead.last_contact)

    def test_lead_queryset_scoping_by_role(self):
        mine, _ = create_lead(self.manager, name="Mine", phone="+8801733", assigned_to=self.staff)
        other, _ = create_lead(self.manager, name="Other", phone="+8801744")
        staff_qs = lead_queryset_for(self.staff)
        self.assertIn(mine, staff_qs)
        self.assertIn(other, staff_qs)  # unassigned visible
        support_qs = lead_queryset_for(self.support)
        self.assertNotIn(mine, support_qs)

    def test_get_role(self):
        self.assertEqual(get_role(self.owner), "owner")
        self.assertEqual(get_role(self.staff), "staff")
        self.assertIsNone(get_role(self.owner) and None)


class PermissionsTests(CrmBaseTestCase):
    def test_staff_required_blocks_regular_user(self):
        from django.test import Client
        from crm.views import dashboard
        user = User.objects.create_user(username="tenant", password="x")
        c = Client()
        c.force_login(user)
        resp = c.get("/crm/")
        self.assertEqual(resp.status_code, 403)

    def test_owner_lands_on_crm_after_login(self):
        from django.test import Client
        c = Client()
        c.force_login(self.owner)
        resp = c.get("/crm/")
        self.assertEqual(resp.status_code, 200)

    def test_crm_role_required_blocks_staff_from_settings(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        resp = c.get("/crm/settings/")
        self.assertEqual(resp.status_code, 403)
        c2 = Client()
        c2.force_login(self.manager)
        resp2 = c2.get("/crm/settings/")
        self.assertEqual(resp2.status_code, 200)


class ViewSmokeTests(CrmBaseTestCase):
    def test_all_pages_render_for_owner(self):
        from django.test import Client
        c = Client()
        c.force_login(self.owner)
        lead, _ = create_lead(self.owner, name="Smoke", phone="+8801755")
        Company.objects.create(name="Acme", owner=self.owner)
        for url in [
            "/crm/", "/crm/leads/", "/crm/leads/new/", "/crm/pipeline/",
            "/crm/customers/", "/crm/companies/", "/crm/calls/", "/crm/demos/",
            "/crm/followups/", "/crm/calendar/", "/crm/tasks/", "/crm/scripts/",
            "/crm/faq/", "/crm/team/", "/crm/reports/", "/crm/settings/",
            f"/crm/leads/{lead.pk}/", f"/crm/leads/{lead.pk}/edit/",
        ]:
            resp = c.get(url)
            self.assertEqual(resp.status_code, 200, url)

    def test_lead_crud_flow(self):
        from django.test import Client
        c = Client()
        c.force_login(self.manager)
        resp = c.post("/crm/leads/new/", {"name": "Flow Lead", "phone": "+8801766", "source": "manual"})
        self.assertEqual(resp.status_code, 302)
        lead = Lead.objects.get(phone="+8801766")
        resp = c.post(f"/crm/leads/{lead.pk}/", {"action": "update", "score": 80})
        self.assertEqual(resp.status_code, 302)
        lead.refresh_from_db()
        self.assertEqual(lead.score, 80)

    def test_ajax_endpoints(self):
        from django.test import Client
        c = Client()
        c.force_login(self.owner)
        lead, _ = create_lead(self.owner, name="Ajax", phone="+8801777")
        resp = c.post(f"/crm/ajax/leads/{lead.pk}/move", {"stage": self.won_stage.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["won"])
        resp = c.post("/crm/ajax/calls/log", {"lead": lead.pk, "duration": 5, "outcome": "interested"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(CallLog.objects.filter(lead=lead).exists())
        meeting = Meeting.objects.create(lead=lead, staff=self.owner, datetime=timezone.now() + timedelta(days=1))
        resp = c.post(f"/crm/ajax/meetings/{meeting.pk}/status", {"status": "completed"})
        self.assertEqual(resp.status_code, 200)
        resp = c.get("/crm/ajax/search?q=Ajax")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["results"])
