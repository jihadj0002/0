from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta
import json

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
    CallLog, Meeting, Task, Followup, Notification, SalesScript,
    LearningTopic, LearningArticle,
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

    def test_lead_new_with_empty_company_ok(self):
        from django.test import Client
        c = Client()
        c.force_login(self.manager)
        resp = c.post(
            "/crm/leads/new/",
            {"name": "No Company", "phone": "01711-123456", "company": "", "stage": "", "source": "manual"},
        )
        self.assertEqual(resp.status_code, 302)
        lead = Lead.objects.get(name="No Company")
        self.assertIsNone(lead.company)
        self.assertEqual(lead.phone, "+8801711123456")

    def test_lead_popup_endpoint(self):
        from django.test import Client
        c = Client()
        c.force_login(self.owner)
        lead, _ = create_lead(self.owner, name="Popup", phone="01712-654321")
        resp = c.get(f"/crm/ajax/leads/{lead.pk}/popup")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn(lead.name, body)
        self.assertIn("+8801712654321", body)

    def test_normalize_phone(self):
        from crm.services import normalize_phone
        self.assertEqual(normalize_phone("01345-693054"), "+8801345693054")
        self.assertEqual(normalize_phone("+880 1731-676263"), "+8801731676263")
        self.assertEqual(normalize_phone("8801759215525"), "+8801759215525")
        self.assertEqual(normalize_phone("0179727"), "0179727")

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


class DrawerStageTests(CrmBaseTestCase):
    """Stage change from the lead drawer via ajax_quick_update(field=stage)."""

    def test_assigned_staff_can_change_stage(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        lead, _ = create_lead(self.owner, name="Mine", phone="+8801788", assigned_to=self.staff)
        resp = c.post(f"/crm/ajax/leads/{lead.pk}/update", {"field": "stage", "value": self.won_stage.pk})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["stage_name"], "Won")
        self.assertTrue(data["won"])
        lead.refresh_from_db()
        self.assertEqual(lead.stage, self.won_stage)
        self.assertTrue(lead.activities.filter(type="status_change").exists())

    def test_staff_can_change_stage_on_unassigned_lead(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        lead, _ = create_lead(self.owner, name="Open", phone="+8801799")
        self.assertIsNone(lead.assigned_to)
        resp = c.post(f"/crm/ajax/leads/{lead.pk}/update", {"field": "stage", "value": self.won_stage.pk})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])

    def test_staff_cannot_change_stage_on_others_lead(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        other = User.objects.create_user(username="other_staff", password="x")
        StaffProfile.objects.create(user=other, role="staff")
        lead, _ = create_lead(self.owner, name="Theirs", phone="+8801800", assigned_to=other)
        resp = c.post(f"/crm/ajax/leads/{lead.pk}/update", {"field": "stage", "value": self.won_stage.pk})
        self.assertEqual(resp.status_code, 404)  # invisible to unrelated staff
        lead.refresh_from_db()
        self.assertNotEqual(lead.stage, self.won_stage)

    def test_invalid_stage_returns_404(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        lead, _ = create_lead(self.owner, name="Bad Stage", phone="+8801811", assigned_to=self.staff)
        resp = c.post(f"/crm/ajax/leads/{lead.pk}/update", {"field": "stage", "value": 99999})
        self.assertEqual(resp.status_code, 404)

    def test_popup_includes_stage_selector_for_assigned_staff(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        lead, _ = create_lead(self.owner, name="Popup Stage", phone="+8801822", assigned_to=self.staff)
        resp = c.get(f"/crm/ajax/leads/{lead.pk}/popup")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("Change Stage", body)
        self.assertIn(f'value="{self.new_stage.pk}"', body)

    def test_popup_hides_stage_selector_from_unrelated_staff(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        other = User.objects.create_user(username="other_staff2", password="x")
        StaffProfile.objects.create(user=other, role="staff")
        lead, _ = create_lead(self.owner, name="Hidden", phone="+8801833", assigned_to=other)
        resp = c.get(f"/crm/ajax/leads/{lead.pk}/popup")
        self.assertEqual(resp.status_code, 404)


class LearnTests(CrmBaseTestCase):
    def setUp(self):
        super().setUp()
        self.topic = LearningTopic.objects.create(name="Training", slug="training", order=1)
        self.article = LearningArticle.objects.create(
            topic=self.topic, slug="test-module", title="Test Module",
            summary="A summary",
            content="<h2>Heading</h2><table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>",
            order=1,
        )
        self.article2 = LearningArticle.objects.create(
            topic=self.topic, slug="test-module-2", title="Test Module 2",
            content="<p>Second</p>", order=2,
        )

    def test_learn_index_redirects_to_first_article(self):
        from django.test import Client
        c = Client()
        c.force_login(self.owner)
        resp = c.get("/crm/learn/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/crm/learn/test-module/", resp.url)

    def test_learn_article_renders_content_for_staff(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        resp = c.get("/crm/learn/test-module/")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("Test Module", body)
        self.assertIn("<h2>Heading</h2>", body)
        self.assertIn('id="learnJump"', body)  # jump dropdown is the only nav
        self.assertNotIn("learn-side", body)  # sidebar removed

    def test_learn_tables_wrapped_with_cell_labels(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        resp = c.get("/crm/learn/test-module/")
        body = resp.content.decode()
        self.assertIn('<div class="tbl-scroll"><table>', body)
        self.assertNotIn("<h2>Heading</h2><table>", body)  # table not bare inside content
        self.assertIn('data-label="A"', body)  # cell labels from header row
        self.assertIn('data-label="B"', body)

    def test_learn_requires_login(self):
        from django.test import Client
        c = Client()
        resp = c.get("/crm/learn/test-module/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login", resp.url)

    def test_inactive_article_404(self):
        from django.test import Client
        c = Client()
        c.force_login(self.owner)
        self.article2.active = False
        self.article2.save()
        resp = c.get("/crm/learn/test-module-2/")
        self.assertEqual(resp.status_code, 404)

    def test_seed_learn_is_idempotent(self):
        from django.core.management import call_command
        call_command("seed_learn")
        first_count = LearningArticle.objects.filter(active=True).count()
        self.assertGreaterEqual(first_count, 14)
        call_command("seed_learn")
        self.assertEqual(LearningArticle.objects.filter(active=True).count(), first_count)


class AssignToMeTests(CrmBaseTestCase):
    """Drawer "Assign to Me" (ajax_quick_update field=assigned_to)."""

    def test_staff_can_claim_unassigned_lead(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        lead, _ = create_lead(self.owner, name="Free Lead", phone="+8801844")
        self.assertIsNone(lead.assigned_to)
        resp = c.post(f"/crm/ajax/leads/{lead.pk}/update", {"field": "assigned_to", "value": "me"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["assignee"], "staff1")
        lead.refresh_from_db()
        self.assertEqual(lead.assigned_to, self.staff)
        self.assertTrue(lead.activities.filter(type="assignment").exists())
        self.assertTrue(Notification.objects.filter(user=self.staff).exists())

    def test_staff_cannot_claim_others_lead(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        other = User.objects.create_user(username="other3", password="x")
        StaffProfile.objects.create(user=other, role="staff")
        lead, _ = create_lead(self.owner, name="Taken", phone="+8801855", assigned_to=other)
        resp = c.post(f"/crm/ajax/leads/{lead.pk}/update", {"field": "assigned_to", "value": "me"})
        self.assertEqual(resp.status_code, 404)
        lead.refresh_from_db()
        self.assertEqual(lead.assigned_to, other)

    def test_staff_cannot_assign_to_specific_staff(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        lead, _ = create_lead(self.owner, name="Open2", phone="+8801866")
        resp = c.post(f"/crm/ajax/leads/{lead.pk}/update", {"field": "assigned_to", "value": self.manager.pk})
        self.assertEqual(resp.status_code, 403)
        lead.refresh_from_db()
        self.assertIsNone(lead.assigned_to)

    def test_manager_can_assign_specific_staff(self):
        from django.test import Client
        c = Client()
        c.force_login(self.manager)
        lead, _ = create_lead(self.owner, name="Assignable", phone="+8801877")
        resp = c.post(f"/crm/ajax/leads/{lead.pk}/update", {"field": "assigned_to", "value": self.staff.pk})
        self.assertEqual(resp.status_code, 200)
        lead.refresh_from_db()
        self.assertEqual(lead.assigned_to, self.staff)

    def test_manager_can_unassign(self):
        from django.test import Client
        c = Client()
        c.force_login(self.manager)
        lead, _ = create_lead(self.owner, name="Unassign", phone="+8801888", assigned_to=self.staff)
        resp = c.post(f"/crm/ajax/leads/{lead.pk}/update", {"field": "assigned_to", "value": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["assignee"])
        lead.refresh_from_db()
        self.assertIsNone(lead.assigned_to)

    def test_popup_shows_assign_button_when_unassigned(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        lead, _ = create_lead(self.owner, name="Popup Free", phone="+8801899")
        resp = c.get(f"/crm/ajax/leads/{lead.pk}/popup")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Assign to Me", resp.content.decode())

    def test_popup_hides_assign_button_when_assigned_to_me(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        lead, _ = create_lead(self.owner, name="Popup Mine", phone="+8801900", assigned_to=self.staff)
        resp = c.get(f"/crm/ajax/leads/{lead.pk}/popup")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("Assign to Me", resp.content.decode())

    def test_old_quick_update_url_returns_404(self):
        """Regression: the old /quick-update path must not exist anymore."""
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        lead, _ = create_lead(self.owner, name="Old Url", phone="+8801911", assigned_to=self.staff)
        resp = c.post(f"/crm/ajax/leads/{lead.pk}/quick-update", {"field": "stage", "value": self.won_stage.pk})
        self.assertEqual(resp.status_code, 404)


class ScriptToggleTests(CrmBaseTestCase):
    def test_script_toggle_with_trailing_slash(self):
        from django.test import Client
        c = Client()
        c.force_login(self.manager)
        script = SalesScript.objects.create(title="Cold A", category="cold_call", content="1. Opening")
        resp = c.post(f"/crm/scripts/{script.pk}/toggle/")
        self.assertEqual(resp.status_code, 200)
        script.refresh_from_db()
        self.assertFalse(script.active)
        resp = c.post(f"/crm/scripts/{script.pk}/toggle/")
        self.assertEqual(resp.status_code, 200)
        script.refresh_from_db()
        self.assertTrue(script.active)

    def test_script_toggle_without_slash_redirects_for_get(self):
        from django.test import Client
        c = Client()
        c.force_login(self.manager)
        script = SalesScript.objects.create(title="Cold B", category="cold_call", content="x")
        resp = c.get(f"/crm/scripts/{script.pk}/toggle")
        self.assertIn(resp.status_code, (301, 302))  # APPEND_SLASH redirect

    def test_script_toggle_requires_manager(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        script = SalesScript.objects.create(title="Cold C", category="cold_call", content="x")
        resp = c.post(f"/crm/scripts/{script.pk}/toggle/")
        self.assertEqual(resp.status_code, 403)
        script.refresh_from_db()
        self.assertTrue(script.active)

    def test_scripts_page_renders_escaped_content_data_attr(self):
        from django.test import Client
        c = Client()
        c.force_login(self.owner)
        SalesScript.objects.create(
            title="Multi Line", category="cold_call",
            content='1. Opening\n"Quoted" & <b>tags</b>',
        )
        resp = c.get("/crm/scripts/")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn("data-content=", body)
        self.assertIn("Multi Line", body)


class DemoSchedulingTests(CrmBaseTestCase):
    def test_demo_post_creates_meeting(self):
        from django.test import Client
        c = Client()
        c.force_login(self.manager)
        lead, _ = create_lead(self.owner, name="Demo Lead", phone="+8801922")
        dt = (timezone.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
        resp = c.post("/crm/demos/", {"lead": lead.pk, "datetime": dt, "platform": "zoom"})
        self.assertEqual(resp.status_code, 302)
        meeting = Meeting.objects.get(lead=lead)
        self.assertEqual(meeting.platform, "zoom")
        self.assertTrue(lead.activities.filter(type="demo").exists())

    def test_demo_post_invalid_datetime_returns_400(self):
        from django.test import Client
        c = Client()
        c.force_login(self.manager)
        lead, _ = create_lead(self.owner, name="Bad Demo", phone="+8801933")
        resp = c.post("/crm/demos/", {"lead": lead.pk, "datetime": "not-a-date"})
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Meeting.objects.filter(lead=lead).exists())

    def test_demo_post_missing_lead_returns_404(self):
        from django.test import Client
        c = Client()
        c.force_login(self.manager)
        dt = (timezone.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
        resp = c.post("/crm/demos/", {"lead": "", "datetime": dt})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(Meeting.objects.count(), 0)

    def test_demo_post_xhr_returns_json(self):
        from django.test import Client
        c = Client()
        c.force_login(self.manager)
        lead, _ = create_lead(self.owner, name="XHR Demo", phone="+8801944")
        dt = (timezone.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
        resp = c.post(
            "/crm/demos/",
            {"lead": lead.pk, "datetime": dt},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])


class TaskPermissionTests(CrmBaseTestCase):
    def _make_task(self, assigned_to=None, created_by=None, status="pending"):
        return Task.objects.create(
            title="T", assigned_to=assigned_to, priority="medium",
            status=status, created_by=created_by or self.owner,
        )

    def test_staff_create_forces_self_assignment(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        resp = c.post("/crm/tasks/", {"title": "My Task", "assigned_to": self.manager.pk})
        self.assertEqual(resp.status_code, 302)
        task = Task.objects.get(title="My Task")
        self.assertEqual(task.assigned_to, self.staff)

    def test_manager_create_can_assign_other_staff(self):
        from django.test import Client
        c = Client()
        c.force_login(self.manager)
        resp = c.post("/crm/tasks/", {"title": "Delegated", "assigned_to": self.staff.pk})
        self.assertEqual(resp.status_code, 302)
        task = Task.objects.get(title="Delegated")
        self.assertEqual(task.assigned_to, self.staff)

    def test_staff_cannot_update_others_task(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        other = User.objects.create_user(username="other4", password="x")
        StaffProfile.objects.create(user=other, role="staff")
        task = self._make_task(assigned_to=other, created_by=self.owner)
        resp = c.post(f"/crm/ajax/tasks/{task.pk}/update", {"status": "done"})
        self.assertEqual(resp.status_code, 403)
        task.refresh_from_db()
        self.assertEqual(task.status, "pending")

    def test_staff_can_update_own_task(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        task = self._make_task(assigned_to=self.staff, created_by=self.owner)
        resp = c.post(f"/crm/ajax/tasks/{task.pk}/update", {"status": "done"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "done")
        task.refresh_from_db()
        self.assertEqual(task.status, "done")

    def test_manager_can_update_any_task(self):
        from django.test import Client
        c = Client()
        c.force_login(self.manager)
        task = self._make_task(assigned_to=self.staff, created_by=self.staff)
        resp = c.post(f"/crm/ajax/tasks/{task.pk}/update", {"status": "doing", "priority": "high"})
        self.assertEqual(resp.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.status, "doing")
        self.assertEqual(task.priority, "high")

    def test_task_update_invalid_status_400(self):
        from django.test import Client
        c = Client()
        c.force_login(self.manager)
        task = self._make_task()
        resp = c.post(f"/crm/ajax/tasks/{task.pk}/update", {"status": "banana"})
        self.assertEqual(resp.status_code, 400)

    def test_task_update_reassign_by_staff_ignored(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        other = User.objects.create_user(username="other5", password="x")
        StaffProfile.objects.create(user=other, role="staff")
        task = self._make_task(assigned_to=self.staff, created_by=self.owner)
        resp = c.post(f"/crm/ajax/tasks/{task.pk}/update", {"assigned_to": other.pk})
        self.assertEqual(resp.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.assigned_to, self.staff)

    def test_task_update_reassign_by_manager(self):
        from django.test import Client
        c = Client()
        c.force_login(self.manager)
        other = User.objects.create_user(username="other6", password="x")
        StaffProfile.objects.create(user=other, role="staff")
        task = self._make_task(assigned_to=self.staff, created_by=self.owner)
        resp = c.post(f"/crm/ajax/tasks/{task.pk}/update", {"assigned_to": other.pk})
        self.assertEqual(resp.status_code, 200)
        task.refresh_from_db()
        self.assertEqual(task.assigned_to, other)

    def test_tasks_page_hides_done_by_default(self):
        from django.test import Client
        c = Client()
        c.force_login(self.owner)
        pending = self._make_task(assigned_to=self.owner, status="pending", created_by=self.owner)
        done = self._make_task(assigned_to=self.owner, status="done", created_by=self.owner)
        pending.title = "Visible Task"
        done.title = "Hidden Done Task"
        pending.save()
        done.save()
        resp = c.get("/crm/tasks/")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn(f'data-task="{pending.pk}"', body)
        self.assertIn("Visible Task", body)
        self.assertNotIn("Hidden Done Task", body)

    def test_tasks_page_done_visible_with_status_filter(self):
        from django.test import Client
        c = Client()
        c.force_login(self.owner)
        task = self._make_task(assigned_to=self.owner, status="done", created_by=self.owner)
        resp = c.get("/crm/tasks/?status=done")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Done", resp.content.decode())
        self.assertIn(str(task.pk), resp.content.decode())


class LeadAssigneeFilterTests(CrmBaseTestCase):
    def setUp(self):
        super().setUp()
        self.other = User.objects.create_user(username="staff2", password="x")
        StaffProfile.objects.create(user=self.other, role="staff")
        self.mine, _ = create_lead(self.owner, name="Mine", phone="+8801900", assigned_to=self.staff)
        self.theirs, _ = create_lead(self.owner, name="Theirs", phone="+8801901", assigned_to=self.other)
        self.open_lead, _ = create_lead(self.owner, name="Open", phone="+8801902")

    def test_owner_can_filter_by_staff_pk(self):
        from django.test import Client
        c = Client()
        c.force_login(self.owner)
        resp = c.get(f"/crm/leads/?assigned={self.staff.pk}")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn(f'data-lead="{self.mine.pk}"', body)
        self.assertNotIn(f'data-lead="{self.theirs.pk}"', body)
        self.assertNotIn(f'data-lead="{self.open_lead.pk}"', body)

    def test_owner_can_filter_unassigned(self):
        from django.test import Client
        c = Client()
        c.force_login(self.owner)
        resp = c.get("/crm/leads/?assigned=unassigned")
        body = resp.content.decode()
        self.assertIn(f'data-lead="{self.open_lead.pk}"', body)
        self.assertNotIn(f'data-lead="{self.mine.pk}"', body)

    def test_owner_filter_dropdown_lists_each_staff(self):
        from django.test import Client
        c = Client()
        c.force_login(self.owner)
        resp = c.get("/crm/leads/")
        body = resp.content.decode()
        self.assertIn(f'value="{self.staff.pk}"', body)
        self.assertIn(f'value="{self.other.pk}"', body)

    def test_staff_cannot_filter_by_other_staff_pk(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        resp = c.get(f"/crm/leads/?assigned={self.other.pk}")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertNotIn(f'data-lead="{self.theirs.pk}"', body)  # pk filter ignored for staff
        self.assertIn(f'data-lead="{self.mine.pk}"', body)       # staff still sees own + unassigned
        self.assertIn(f'data-lead="{self.open_lead.pk}"', body)

    def test_staff_filter_dropdown_has_no_staff_options(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        resp = c.get("/crm/leads/")
        body = resp.content.decode()
        self.assertNotIn("Staff: staff1", body)
        self.assertIn("Mine + Unassigned", body)


class AssignmentGuardTests(CrmBaseTestCase):
    def setUp(self):
        super().setUp()
        self.other = User.objects.create_user(username="staff3", password="x")
        StaffProfile.objects.create(user=self.other, role="staff")

    def test_staff_cannot_reassign_own_lead_to_other_staff_via_detail(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        lead, _ = create_lead(self.owner, name="Mine", phone="+8801910", assigned_to=self.staff)
        resp = c.post(f"/crm/leads/{lead.pk}/", {"action": "update", "assigned_to": self.other.pk})
        self.assertEqual(resp.status_code, 302)
        lead.refresh_from_db()
        self.assertEqual(lead.assigned_to, self.staff)

    def test_staff_can_assign_unassigned_lead_to_self_via_detail(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        lead, _ = create_lead(self.owner, name="Open", phone="+8801911")
        resp = c.post(f"/crm/leads/{lead.pk}/", {"action": "update", "assigned_to": self.staff.pk})
        self.assertEqual(resp.status_code, 302)
        lead.refresh_from_db()
        self.assertEqual(lead.assigned_to, self.staff)

    def test_staff_can_unassign_own_lead(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        lead, _ = create_lead(self.owner, name="Mine2", phone="+8801912", assigned_to=self.staff)
        resp = c.post(f"/crm/leads/{lead.pk}/", {"action": "update", "assigned_to": ""})
        self.assertEqual(resp.status_code, 302)
        lead.refresh_from_db()
        self.assertIsNone(lead.assigned_to)

    def test_manager_can_reassign_any_lead(self):
        from django.test import Client
        c = Client()
        c.force_login(self.manager)
        lead, _ = create_lead(self.owner, name="Any", phone="+8801913", assigned_to=self.staff)
        resp = c.post(f"/crm/leads/{lead.pk}/", {"action": "update", "assigned_to": self.other.pk})
        self.assertEqual(resp.status_code, 302)
        lead.refresh_from_db()
        self.assertEqual(lead.assigned_to, self.other)

    def test_staff_create_lead_forces_self_assignment(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        resp = c.post("/crm/leads/new/", {"name": "Forced", "assigned_to": self.other.pk})
        self.assertEqual(resp.status_code, 302)
        lead = Lead.objects.get(name="Forced")
        self.assertEqual(lead.assigned_to, self.staff)

    def test_manager_create_lead_can_assign_other_staff(self):
        from django.test import Client
        c = Client()
        c.force_login(self.manager)
        resp = c.post("/crm/leads/new/", {"name": "Delegated", "assigned_to": self.other.pk})
        self.assertEqual(resp.status_code, 302)
        lead = Lead.objects.get(name="Delegated")
        self.assertEqual(lead.assigned_to, self.other)

    def test_detail_page_assignee_select_restricted_for_staff(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        lead, _ = create_lead(self.owner, name="Sel", phone="+8801914", assigned_to=self.staff)
        resp = c.get(f"/crm/leads/{lead.pk}/")
        body = resp.content.decode()
        self.assertIn("staff1 (Me)", body)          # only themselves listed
        self.assertNotIn("staff3", body)            # other staff members absent
        self.assertNotIn("manager1", body)


class ConvertFlowTests(CrmBaseTestCase):
    def test_staff_can_convert_own_lead_via_ajax(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        lead, _ = create_lead(self.owner, name="Karim", email="karim2@x.com",
                              phone="+8801920", assigned_to=self.staff)
        resp = c.post(f"/crm/ajax/leads/{lead.pk}/convert",
                      {"package": "pro", "monthly_value": "5990"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        customer = Customer.objects.get(lead=lead)
        self.assertEqual(customer.package, "pro")
        self.assertEqual(customer.monthly_value, 5990)
        lead.refresh_from_db()
        self.assertTrue(lead.converted)
        self.assertEqual(lead.stage, self.won_stage)
        self.assertTrue(lead.activities.filter(type="status_change").exists())

    def test_convert_is_idempotent_and_preserves_package(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        lead, _ = create_lead(self.owner, name="Karim3", email="karim3@x.com",
                              phone="+8801921", assigned_to=self.staff)
        resp1 = c.post(f"/crm/ajax/leads/{lead.pk}/convert", {"package": "pro"})
        resp2 = c.post(f"/crm/ajax/leads/{lead.pk}/convert", {"package": "basic"})
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(Customer.objects.filter(lead=lead).count(), 1)
        customer = Customer.objects.get(lead=lead)
        self.assertEqual(customer.package, "pro")

    def test_popup_shows_stage_and_notes_update_form(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        lead, _ = create_lead(self.owner, name="Conv", phone="+8801922", assigned_to=self.staff)
        resp = c.get(f"/crm/ajax/leads/{lead.pk}/popup")
        body = resp.content.decode()
        self.assertIn("leadStageForm", body)
        self.assertIn('name="notes"', body)
        self.assertIn('name="stage"', body)
        self.assertNotIn("Convert to Customer", body)

    def test_popup_hides_convert_button_after_conversion(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        lead, _ = create_lead(self.owner, name="Done", phone="+8801923", assigned_to=self.staff)
        convert_lead(self.owner, lead, package="pro")
        resp = c.get(f"/crm/ajax/leads/{lead.pk}/popup")
        body = resp.content.decode()
        self.assertNotIn("Convert to Customer", body)

    def test_detail_page_convert_button_for_staff_owner(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        lead, _ = create_lead(self.owner, name="Owned", phone="+8801924", assigned_to=self.staff)
        resp = c.get(f"/crm/leads/{lead.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Mark Won & Convert", resp.content.decode())


class ScriptEditTests(CrmBaseTestCase):
    def setUp(self):
        super().setUp()
        self.script = SalesScript.objects.create(
            title="Cold opener", category="cold_call",
            content="1. Intro\n2. Qualify\n3. Close",
        )

    def test_manager_can_edit_script(self):
        from django.test import Client
        c = Client()
        c.force_login(self.manager)
        resp = c.post(f"/crm/scripts/{self.script.pk}/edit/",
                      {"title": "New Title", "category": "objection", "content": "New body\nLine two"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])
        self.script.refresh_from_db()
        self.assertEqual(self.script.title, "New Title")
        self.assertEqual(self.script.category, "objection")
        self.assertEqual(self.script.content, "New body\nLine two")

    def test_staff_cannot_edit_script(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        resp = c.post(f"/crm/scripts/{self.script.pk}/edit/",
                      {"title": "Hacked", "content": "x"})
        self.assertEqual(resp.status_code, 403)
        self.script.refresh_from_db()
        self.assertEqual(self.script.title, "Cold opener")

    def test_edit_script_requires_title(self):
        from django.test import Client
        c = Client()
        c.force_login(self.manager)
        resp = c.post(f"/crm/scripts/{self.script.pk}/edit/",
                      {"title": "  ", "content": "x"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "Title is required")

    def test_edit_script_rejects_invalid_category(self):
        from django.test import Client
        c = Client()
        c.force_login(self.manager)
        resp = c.post(f"/crm/scripts/{self.script.pk}/edit/",
                      {"title": "T", "category": "banana", "content": "x"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "Invalid category")

    def test_edit_script_missing_404(self):
        from django.test import Client
        c = Client()
        c.force_login(self.manager)
        resp = c.post("/crm/scripts/999999/edit/", {"title": "T", "content": "x"})
        self.assertEqual(resp.status_code, 404)

    def test_ajax_create_script_returns_json(self):
        from django.test import Client
        c = Client()
        c.force_login(self.manager)
        resp = c.post("/crm/scripts/",
                      {"title": "AJAX Script", "category": "closing", "content": "Just ask."},
                      HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        script = SalesScript.objects.get(title="AJAX Script")
        self.assertEqual(script.pk, data["pk"])

    def test_ajax_create_script_staff_forbidden(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        resp = c.post("/crm/scripts/", {"title": "Nope", "content": "x"},
                      HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(resp.status_code, 403)

    def test_scripts_page_embeds_content_safely_for_js(self):
        from django.test import Client
        c = Client()
        c.force_login(self.owner)
        resp = c.get("/crm/scripts/")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        card = body.split('class="panel script-card"')[1]
        self.assertIn("&quot;", card)  # JSON string HTML-escaped in attribute
        self.assertIn("\\n", card)     # newlines escaped as literal \n (not collapsed)
        self.assertIn("✏️ Edit", card)

    def test_scripts_page_hides_edit_for_staff(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        resp = c.get("/crm/scripts/")
        body = resp.content.decode()
        self.assertNotIn("✏️ Edit", body)
        self.assertNotIn("+ New Script", body)


class ImageImportTests(CrmBaseTestCase):
    """Owner-only: image → vision AI → reviewed leads → unassigned leads."""

    FAKE_IMAGE = "data:image/png;base64,iVBORw0KGgo="

    def _extract(self, data_url):
        return [
            {"name": "ABC Trading", "phone": "+8801912345678", "email": "a@b.co",
             "address": "Dhanmondi, Dhaka", "website": "abctrading.com", "industry": "Trading",
             "summary": "Wholesale supplier.", "tags": ["tier-1", "NEW", "Wholesale"],
             "tier": "tier-1", "notes": "VAT: VN-1042; Hours 9am-6pm; Owner: Md Karim"},
            {"name": "XYZ Restaurant", "phone": "", "email": "", "address": "Gulshan",
             "website": "", "industry": "", "summary": "", "tags": [], "tier": "",
             "notes": ""},
        ]

    def test_analyze_image_creates_leads_payload(self):
        from unittest.mock import patch
        from django.test import Client
        c = Client()
        c.force_login(self.owner)
        with patch("crm.ai_import.extract_leads_from_image", side_effect=self._extract):
            resp = c.post("/crm/ajax/leads/analyze-image", {"image": self.FAKE_IMAGE})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["leads"]), 2)
        self.assertEqual(data["leads"][0]["name"], "ABC Trading")
        self.assertEqual(data["leads"][0]["phone"], "+8801912345678")
        self.assertEqual(data["leads"][0]["tier"], "tier-1")
        self.assertEqual(data["leads"][0]["tags"], ["tier-1", "NEW", "Wholesale"])
        self.assertIn("VAT", data["leads"][0]["notes"])

    def test_analyze_image_empty_result_returns_error(self):
        from unittest.mock import patch
        from django.test import Client
        c = Client()
        c.force_login(self.owner)
        with patch("crm.ai_import.extract_leads_from_image", return_value=[]):
            resp = c.post("/crm/ajax/leads/analyze-image", {"image": self.FAKE_IMAGE})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["ok"])
        self.assertIn("Couldn't read any lead details", resp.json()["error"])

    def test_analyze_image_rejects_missing_image(self):
        from django.test import Client
        c = Client()
        c.force_login(self.owner)
        resp = c.post("/crm/ajax/leads/analyze-image", {})
        self.assertEqual(resp.status_code, 400)

    def test_analyze_image_owner_only(self):
        from django.test import Client
        for user in (self.manager, self.staff):
            c = Client()
            c.force_login(user)
            resp = c.post("/crm/ajax/leads/analyze-image", {"image": self.FAKE_IMAGE})
            self.assertEqual(resp.status_code, 403)

    def test_create_imported_leads(self):
        from django.test import Client
        c = Client()
        c.force_login(self.owner)
        leads = [
            {"name": "ABC Trading", "phone": "+8801912345678", "email": "a@b.co",
             "address": "Dhanmondi, Dhaka", "website": "abctrading.com", "industry": "Trading",
             "summary": "Wholesale supplier.", "tags": ["tier-1", "New", "#tier-1"],
             "tier": "tier-2", "notes": "VAT: VN-1042; Hours 9am-6pm"},
            {"name": "XYZ Restaurant", "phone": "", "email": "", "address": "Gulshan",
             "website": "", "industry": "", "summary": "", "tags": [], "tier": "",
             "notes": ""},
        ]
        resp = c.post("/crm/ajax/leads/create-from-import", {"leads": json.dumps(leads)})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["created"], 2)
        self.assertEqual(data["duplicates"], 0)

        lead = Lead.objects.get(name="ABC Trading")
        self.assertIsNone(lead.assigned_to)
        self.assertEqual(lead.source, "import")
        self.assertEqual(lead.phone, "+8801912345678")
        self.assertEqual(lead.email, "a@b.co")
        self.assertEqual(lead.industry, "Trading")
        self.assertEqual(lead.website, "abctrading.com")
        self.assertIn("Dhanmondi, Dhaka", lead.notes)
        self.assertIn("Wholesale supplier.", lead.notes)
        self.assertIn("VAT: VN-1042", lead.notes)
        self.assertIn("Dhanmondi, Dhaka", lead.notes)
        # tags normalized: lowercased, # stripped, deduped, tier merged (not duplicated)
        self.assertEqual(lead.tags, ["tier-1", "new", "tier-2"])

        xyz = Lead.objects.get(name="XYZ Restaurant")
        self.assertIsNone(xyz.assigned_to)
        self.assertIn("Gulshan", xyz.notes)
        self.assertEqual(xyz.tags, [])

    def test_create_imported_leads_comma_string_tags(self):
        from django.test import Client
        c = Client()
        c.force_login(self.owner)
        # Frontend review form sends tags as a comma-separated string.
        leads = [
            {"name": "Comma Co", "phone": "+8801933333333", "email": "", "address": "",
             "website": "", "industry": "", "summary": "", "tags": "Tier-2,Hot, HOT, ",
             "tier": "tier-3", "notes": ""},
            {"name": "", "phone": "", "email": "", "address": "", "website": "",
             "industry": "", "summary": "", "tags": "x, y", "tier": "", "notes": ""},
        ]
        resp = c.post("/crm/ajax/leads/create-from-import", {"leads": json.dumps(leads)})
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["created"], 1)
        lead = Lead.objects.get(name="Comma Co")
        self.assertEqual(lead.tags, ["tier-2", "hot", "tier-3"])

    def test_tags_dedupe_and_case_normalize(self):
        from crm.ai_import import normalize_tags
        self.assertEqual(normalize_tags(["TIER-1", "tier-1", "#hot", " H OT "], "tier-1"),
                         ["tier-1", "hot", "h ot"])
        self.assertEqual(normalize_tags("a,b ,  c", "tier-2"), ["a", "b", "c", "tier-2"])
        self.assertEqual(normalize_tags([], ""), [])

    def test_create_imported_leads_dedupe_and_skip_blank(self):
        from django.test import Client
        create_lead(self.owner, name="ABC Trading", phone="+8801912345678")
        c = Client()
        c.force_login(self.owner)
        leads = [
            {"name": "ABC Trading", "phone": "+8801912345678", "email": "", "address": "",
             "website": "", "industry": "", "summary": ""},
            {"name": "", "phone": "+8801999999999", "email": "", "address": "",
             "website": "", "industry": "", "summary": ""},
            {"name": "Fresh Market", "phone": "+8801911111111", "email": "", "address": "",
             "website": "", "industry": "", "summary": ""},
        ]
        resp = c.post("/crm/ajax/leads/create-from-import", {"leads": json.dumps(leads)})
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["created"], 1)
        self.assertEqual(data["duplicates"], 1)
        self.assertTrue(Lead.objects.filter(name="Fresh Market", source="import").exists())

    def test_create_imported_leads_invalid_payload(self):
        from django.test import Client
        c = Client()
        c.force_login(self.owner)
        resp = c.post("/crm/ajax/leads/create-from-import", {"leads": "not-json"})
        self.assertEqual(resp.status_code, 400)

    def test_create_imported_leads_owner_only(self):
        from django.test import Client
        for user in (self.manager, self.staff):
            c = Client()
            c.force_login(user)
            resp = c.post("/crm/ajax/leads/create-from-import",
                          {"leads": json.dumps([{"name": "X"}])})
            self.assertEqual(resp.status_code, 403)

    def test_leads_page_shows_import_button_for_owner_only(self):
        from django.test import Client
        c = Client()
        c.force_login(self.owner)
        resp = c.get("/crm/leads/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Import from Image", resp.content.decode())
        c = Client()
        c.force_login(self.manager)
        resp = c.get("/crm/leads/")
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("Import from Image", resp.content.decode())

    def test_notes_update_via_quick_update_clears_with_empty(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        lead, _ = create_lead(self.owner, name="Notesy", phone="+8801912345679",
                              assigned_to=self.staff)
        resp = c.post(f"/crm/ajax/leads/{lead.pk}/update", {"field": "notes", "value": "Hello world"})
        self.assertEqual(resp.status_code, 200)
        lead.refresh_from_db()
        self.assertEqual(lead.notes, "Hello world")
        resp = c.post(f"/crm/ajax/leads/{lead.pk}/update", {"field": "notes", "value": "   "})
        self.assertEqual(resp.status_code, 200)
        lead.refresh_from_db()
        self.assertEqual(lead.notes, "")


class PwaAndDashboardTests(CrmBaseTestCase):
    def test_manifest_endpoint(self):
        from django.test import Client
        c = Client()
        c.force_login(self.owner)
        resp = c.get("/crm/manifest.json")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("application/manifest+json", resp["Content-Type"])
        data = resp.json()
        self.assertEqual(data["name"], "Matrix CRM")
        self.assertEqual(data["start_url"], "/crm/")
        self.assertEqual(data["scope"], "/crm/")
        self.assertEqual(data["display"], "standalone")
        self.assertGreaterEqual(len(data["icons"]), 2)
        self.assertTrue(all(i["sizes"] in ("192x192", "512x512", "180x180") for i in data["icons"]))

    def test_service_worker_endpoint(self):
        from django.test import Client
        c = Client()
        c.force_login(self.owner)
        resp = c.get("/crm/sw.js")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("application/javascript", resp["Content-Type"])
        self.assertIn("addEventListener('fetch'", resp.content.decode())

    def test_manifest_and_sw_staff_only(self):
        from django.test import Client
        anon = Client()
        self.assertEqual(anon.get("/crm/manifest.json").status_code, 302)
        self.assertEqual(anon.get("/crm/sw.js").status_code, 302)
        regular = User.objects.create_user(username="regular1", password="x")
        c = Client()
        c.force_login(regular)
        self.assertEqual(c.get("/crm/manifest.json").status_code, 403)
        self.assertEqual(c.get("/crm/sw.js").status_code, 403)

    def test_dashboard_cards_link_to_filters(self):
        from django.test import Client
        c = Client()
        c.force_login(self.owner)
        resp = c.get("/crm/")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('class="card" href="/crm/leads/"', body)
        self.assertIn('href="/crm/leads/?bucket=hot"', body)
        self.assertIn('href="/crm/leads/?bucket=won"', body)
        self.assertIn('href="/crm/followups/"', body)

    def test_dashboard_cards_link_for_staff(self):
        from django.test import Client
        c = Client()
        c.force_login(self.staff)
        resp = c.get("/crm/")
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('href="/crm/leads/?bucket=hot"', body)
        self.assertIn('href="/crm/followups/"', body)
