from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from back.models import Integration, Conversation, Message
from back.views import _needs_setup


class SetupGateTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="setupgate", password="x1234567"
        )

    def test_fresh_user_needs_setup(self):
        self.assertTrue(_needs_setup(self.user))

    def test_completed_user_does_not_need_setup(self):
        self.user.profile.setup_completed_at = timezone.now()
        self.user.profile.save(update_fields=["setup_completed_at"])
        self.assertFalse(_needs_setup(self.user))

    def test_connected_messenger_auto_completes(self):
        Integration.objects.create(
            user=self.user,
            platform="messenger",
            is_connected=True,
            is_enabled=True,
            access_token="tok",
        )
        self.assertFalse(_needs_setup(self.user))
        self.user.profile.refresh_from_db()
        self.assertIsNotNone(self.user.profile.setup_completed_at)


class LoginRedirectTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.fresh = User.objects.create_user(
            username="fresh_user", password="pass1234567"
        )
        self.done = User.objects.create_user(
            username="done_user", password="pass1234567"
        )
        self.done.profile.setup_completed_at = timezone.now()
        self.done.profile.save(update_fields=["setup_completed_at"])

    def test_fresh_user_goes_to_setup_wizard(self):
        c = Client()
        ok = c.login(username="fresh_user", password="pass1234567")
        self.assertTrue(ok)
        resp = c.get(reverse("front:login"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("back:setup"))

    def test_setup_done_user_goes_to_dashboard(self):
        c = Client()
        c.login(username="done_user", password="pass1234567")
        resp = c.get(reverse("front:login"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("back:dashboard"))


class WizardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="wizard_user", password="pass1234567"
        )
        self.client.login(username="wizard_user", password="pass1234567")

    def test_get_connect_step_when_not_connected(self):
        resp = self.client.get(reverse("back:setup"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Continue with Facebook")

    def test_get_store_step_when_connected(self):
        Integration.objects.create(
            user=self.user,
            platform="messenger",
            is_connected=True,
            is_enabled=True,
            access_token="tok",
            page_name="Page One",
        )
        resp = self.client.get(reverse("back:setup"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "name=\"store_name\"")
        self.assertContains(resp, "Page One")

    def test_store_section_saves_and_advances(self):
        resp = self.client.post(reverse("back:setup"), {
            "section": "store",
            "store_name": "Wizmart",
            "currency": "BDT",
            "timezone": "Asia/Dhaka",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIn("step=agent", resp.url)
        self.user.profile.refresh_from_db()
        from context.models import StoreConfig
        store = StoreConfig.objects.get(user=self.user)
        self.assertEqual(store.store_name, "Wizmart")

    def test_agent_section_saves_and_advances(self):
        resp = self.client.post(reverse("back:setup"), {
            "section": "agent",
            "name": "Aria",
            "role": "Sales",
            "tone": "friendly",
            "style": "conversational",
            "language": "bn",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertIn("step=behavior", resp.url)
        from context.models import AgentIdentity
        identity = AgentIdentity.objects.get(user=self.user)
        self.assertEqual(identity.name, "Aria")
        self.assertEqual(identity.language, "bn")

    def test_finish_marks_setup_complete(self):
        resp = self.client.post(reverse("back:setup"), {"section": "finish"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("back:dashboard"))
        self.user.profile.refresh_from_db()
        self.assertIsNotNone(self.user.profile.setup_completed_at)


class OAuthNextTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="oauth_user", password="pass1234567"
        )
        self.client.login(username="oauth_user", password="pass1234567")

    def test_start_stores_sanitized_next(self):
        resp = self.client.get(
            reverse("api:meta-oauth-start"), {"next": reverse("back:setup")}
        )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("facebook.com", resp.url)
        self.assertEqual(
            self.client.session.get("meta_oauth_next"), reverse("back:setup")
        )

    def test_start_rejects_external_next(self):
        self.client.get(
            reverse("api:meta-oauth-start"), {"next": "https://evil.example/x"}
        )
        self.assertNotIn("meta_oauth_next", self.client.session)

    def test_next_consumed_on_redirect_after_oauth(self):
        from api.meta_oauth import _redirect_after_oauth
        request = type("R", (), {"session": {"meta_oauth_next": "/dbsetup/"}})()
        resp = _redirect_after_oauth(request)
        self.assertEqual(resp.url, "/dbsetup/")
        self.assertNotIn("meta_oauth_next", request.session)

    def test_default_fallback(self):
        from api.meta_oauth import _redirect_after_oauth
        request = type("R", (), {"session": {}})()
        resp = _redirect_after_oauth(request)
        self.assertEqual(resp.url, reverse("back:options"))


class DraftMessageTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(
            username="draft_user", password="pass1234567"
        )
        self.convo = Conversation.objects.create(
            user=self.user,
            platform="messenger",
            customer_id="cust_1",
            customer_name="Draft Customer",
        )
        self.client.login(username="draft_user", password="pass1234567")

    def make_message(self, text="Hello", status="sent"):
        return Message.objects.create(
            conversation=self.convo,
            sender="bot",
            status=status,
            text=text,
        )

    def load_messages(self):
        resp = self.client.get(
            reverse("back:ajax_load_messages"), {"cid": self.convo.id}
        )
        self.assertEqual(resp.status_code, 200)
        return resp.json()

    def test_draft_cancel_sets_canceled_status(self):
        draft = self.make_message(text="Hi there", status="draft")
        resp = self.client.post(reverse("back:draft_cancel", args=[draft.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")
        draft.refresh_from_db()
        self.assertEqual(draft.status, "canceled")

    def test_draft_cancel_requires_post(self):
        draft = self.make_message(text="Hi there", status="draft")
        resp = self.client.get(reverse("back:draft_cancel", args=[draft.id]))
        self.assertEqual(resp.status_code, 405)

    def test_draft_cancel_rejects_other_users_draft(self):
        User = get_user_model()
        other = User.objects.create_user(
            username="other_user", password="pass1234567"
        )
        other_convo = Conversation.objects.create(
            user=other, platform="messenger", customer_id="cust_other"
        )
        other_draft = Message.objects.create(
            conversation=other_convo, sender="bot", status="draft", text="Nope"
        )
        resp = self.client.post(reverse("back:draft_cancel", args=[other_draft.id]))
        self.assertEqual(resp.status_code, 404)
        other_draft.refresh_from_db()
        self.assertEqual(other_draft.status, "draft")

    def test_load_messages_excludes_canceled(self):
        sent = self.make_message(text="Sent", status="sent")
        draft = self.make_message(text="Draft", status="draft")
        canceled = self.make_message(text="Canceled", status="canceled")

        data = self.load_messages()
        ids = [m["id"] for m in data["messages"]]

        self.assertIn(sent.id, ids)
        self.assertIn(draft.id, ids)
        self.assertNotIn(canceled.id, ids)

    def test_canceled_not_returned_via_polling(self):
        draft = self.make_message(text="Draft", status="draft")
        canceled = self.make_message(text="Canceled", status="canceled")

        data = self.load_messages()
        ids = [m["id"] for m in data["messages"]]
        self.assertIn(draft.id, ids)

        resp = self.client.get(
            reverse("back:ajax_load_messages"),
            {"cid": self.convo.id, "last_id": draft.id},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        ids = [m["id"] for m in data["messages"]]
        self.assertNotIn(canceled.id, ids)

    def test_pending_drafts_false_after_cancel(self):
        draft = self.make_message(text="Draft", status="draft")
        self.assertTrue(self.convo.pending_drafts)

        self.client.post(reverse("back:draft_cancel", args=[draft.id]))
        self.convo.refresh_from_db()
        self.assertFalse(self.convo.pending_drafts)