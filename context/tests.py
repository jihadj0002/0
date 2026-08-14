from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone

from back.models import Conversation, Product, Sale
from context.models import SessionContext, StoreConfig
from context.crm_models import (
    CrmEvent, CustomerProfile, OrderDraft, SalesOpportunity,
)
from context.crm.signals import record_signal, record_fact, get_or_create_profile, set_current_product
from context.crm.engine import (
    purchase_intent_score, recompute, sentiment_from_text, infer_lifecycle,
)
from context.crm.drafts import (
    compute_order_totals, confirm_draft_order, save_draft, sync_session_state,
)
from context.crm.snapshot import build_crm_snapshot
from api.ai.context import fit_prompt, CORE_PROMPT, MAX_PROMPT_LENGTH


class CrmBaseTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="seller1", password="x")
        self.conv = Conversation.objects.create(
            user=self.user,
            platform="messenger",
            customer_id="psid_123",
            customer_name="Rahim",
        )
        self.product = Product.objects.create(
            user=self.user,
            name="Boroi Achar",
            price=350,
            stock_quantity=10,
            status=True,
        )
        StoreConfig.objects.update_or_create(
            user=self.user,
            defaults={
                "store_name": "Test Store",
                "delivery_charge_inside": 60,
                "delivery_charge_outside": 120,
                "currency": "BDT",
            },
        )


class EngineTests(CrmBaseTestCase):
    def test_purchase_intent_score_from_signals(self):
        record_signal(self.conv, "asked_price")
        record_signal(self.conv, "asked_photo")
        record_signal(self.conv, "asked_delivery")
        profile = get_or_create_profile(self.conv)[0]
        profile.refresh_from_db()
        self.assertEqual(purchase_intent_score(profile), 5 + 10 + 8)

    def test_signals_create_objections(self):
        record_signal(self.conv, "asked_delivery")
        record_signal(self.conv, "asked_delivery")
        profile = get_or_create_profile(self.conv)[0]
        profile.refresh_from_db()
        self.assertEqual(len(profile.objections), 1)
        self.assertEqual(profile.objections[0]["count"], 2)

    def test_recompute_lifecycle_transitions(self):
        record_signal(self.conv, "confirmed_product")
        Sale.objects.create(
            user=self.user,
            conversation=self.conv,
            customer_id=self.conv.customer_id,
            customer_name="Rahim",
            customer_phone="01711111111",
            amount=410,
            status="pending",
        )
        profile = recompute(self.conv)
        profile.refresh_from_db()
        self.assertEqual(profile.lifecycle_stage, "customer")
        self.assertEqual(profile.order_count, 1)
        self.assertEqual(profile.total_spent, 410)

        # Second order → repeat customer.
        Sale.objects.create(
            user=self.user,
            conversation=self.conv,
            customer_id=self.conv.customer_id,
            customer_name="Rahim",
            customer_phone="01711111111",
            amount=200,
            status="pending",
        )
        recompute(self.conv)
        profile.refresh_from_db()
        self.assertEqual(profile.lifecycle_stage, "repeat_customer")

    def test_facts_vs_inferences_separation(self):
        record_fact(self.conv, "budget", "500 taka", source="customer", confidence=1.0)
        profile = recompute(self.conv)
        profile.refresh_from_db()
        # recompute must never wipe customer facts.
        self.assertEqual(profile.facts["budget"]["value"], "500 taka")
        self.assertEqual(profile.facts["budget"]["source"], "customer")
        # lead_score is derived — lives on the profile, not in facts.
        self.assertNotIn("lead_score", profile.facts)

    def test_sentiment_heuristic(self):
        self.assertEqual(sentiment_from_text("ঠিক আছে ধন্যবাদ"), "positive")
        self.assertEqual(sentiment_from_text("খুব দেরি হচ্ছে সমস্যা"), "negative")
        self.assertEqual(sentiment_from_text("কত দাম?"), "neutral")

    def test_infer_lifecycle(self):
        self.assertEqual(infer_lifecycle(None, 0), "lead")
        self.assertEqual(infer_lifecycle(None, 1), "customer")
        self.assertEqual(infer_lifecycle(None, 3), "repeat_customer")


class OrderDraftTests(CrmBaseTestCase):
    def _items(self):
        return [{"pid": self.product.pid, "quantity": 2}]

    def test_compute_order_totals_inside(self):
        totals = compute_order_totals(self.user, self._items(), "inside_dhaka")
        self.assertTrue(totals["ok"])
        self.assertEqual(totals["item_total"], 700)
        self.assertEqual(totals["delivery_charge"], 60)
        self.assertEqual(totals["grand_total"], 760)

    def test_compute_order_totals_outside(self):
        totals = compute_order_totals(self.user, self._items(), "outside_dhaka")
        self.assertEqual(totals["delivery_charge"], 120)
        self.assertEqual(totals["grand_total"], 820)

    def test_compute_order_totals_stock_error(self):
        totals = compute_order_totals(self.user, [{"pid": self.product.pid, "quantity": 99}])
        self.assertFalse(totals["ok"])
        self.assertIn("left in stock", totals["errors"][0])

    def test_compute_order_totals_unknown_product(self):
        totals = compute_order_totals(self.user, [{"pid": "sku_nope"}])
        self.assertFalse(totals["ok"])
        self.assertIn("not found", totals["errors"][0])

    def test_confirm_draft_order_requires_confirmation(self):
        totals = compute_order_totals(self.user, self._items(), "inside_dhaka")
        save_draft(
            self.user, self.conv,
            resolved=totals["resolved"],
            item_total=totals["item_total"],
            delivery_charge=totals["delivery_charge"],
            grand_total=totals["grand_total"],
            delivery_zone="inside_dhaka",
            confirmation_status="awaiting_confirmation",
        )
        sync_session_state(self.conv, "awaiting_confirmation")
        # No phone/address yet → confirm refuses.
        result = confirm_draft_order(self.conv)
        self.assertIn("missing_fields", result)
        self.assertFalse(result.get("confirmable"))

    def test_confirm_draft_order_success(self):
        self.conv.customer_phone = "01711111111"
        self.conv.customer_address = "Mirpur, Dhaka"
        self.conv.customer_city = "Dhaka"
        self.conv.save()
        totals = compute_order_totals(self.user, self._items(), "inside_dhaka")
        save_draft(
            self.user, self.conv,
            resolved=totals["resolved"],
            item_total=totals["item_total"],
            delivery_charge=totals["delivery_charge"],
            grand_total=totals["grand_total"],
            delivery_zone="inside_dhaka",
            confirmation_status="awaiting_confirmation",
        )
        sync_session_state(self.conv, "awaiting_confirmation")
        set_current_product(self.conv, self.product.pid)  # creates the open opportunity
        result = confirm_draft_order(self.conv)
        self.assertTrue(result.get("order_id"))
        self.assertEqual(result["total"], "760")

        draft = OrderDraft.objects.get(conversation=self.conv)
        self.assertEqual(draft.confirmation_status, "confirmed")
        self.assertIsNotNone(draft.converted_order)
        self.assertEqual(draft.grand_total, 760)

        session = SessionContext.objects.get(conversation=self.conv)
        self.assertEqual(session.state, "completed")

        # CRM updates.
        opp = SalesOpportunity.objects.get(conversation=self.conv)
        self.assertEqual(opp.stage, "won")
        profile = CustomerProfile.objects.get(conversation=self.conv)
        self.assertEqual(profile.lifecycle_stage, "customer")
        self.assertEqual(profile.order_count, 1)
        self.assertTrue(CrmEvent.objects.filter(conversation=self.conv, type="order_created").exists())

        # Stock decremented exactly once.
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 8)

    def test_confirm_draft_order_idempotent(self):
        """Second confirm attempt must not create a second Sale."""
        self.conv.customer_phone = "01711111111"
        self.conv.customer_address = "Mirpur, Dhaka"
        self.conv.customer_city = "Dhaka"
        self.conv.save()
        totals = compute_order_totals(self.user, self._items(), "inside_dhaka")
        save_draft(
            self.user, self.conv,
            resolved=totals["resolved"],
            item_total=totals["item_total"],
            delivery_charge=totals["delivery_charge"],
            grand_total=totals["grand_total"],
            delivery_zone="inside_dhaka",
            confirmation_status="awaiting_confirmation",
        )
        confirm_draft_order(self.conv)
        confirm_draft_order(self.conv)  # draft now "confirmed" → refuse
        self.assertEqual(Sale.objects.filter(conversation=self.conv).count(), 1)


class SnapshotTests(CrmBaseTestCase):
    def test_snapshot_empty_without_profile(self):
        self.assertEqual(build_crm_snapshot(self.conv), "")

    def test_snapshot_renders_crm_block(self):
        record_signal(self.conv, "asked_price")
        record_fact(self.conv, "budget", "500 taka")
        text = build_crm_snapshot(self.conv)
        self.assertIn("## CUSTOMER CRM", text)
        self.assertIn("budget", text)
        # Un-greeted before any bot message; greeted afterwards.
        self.assertIn("Greeted: no", text)

    def test_snapshot_greeted_after_bot_reply(self):
        from back.models import Message
        Message.objects.create(conversation=self.conv, sender="bot", text="আসসালামু!")
        record_signal(self.conv, "asked_price")
        text = build_crm_snapshot(self.conv)
        self.assertIn("Greeted: yes", text)

    def test_snapshot_draft_missing_note_for_bare_yes(self):
        self.conv.customer_phone = "01711111111"
        self.conv.customer_city = "Dhaka"
        self.conv.save()
        set_current_product(self.conv, self.product.pid)
        totals = compute_order_totals(
            self.user, [{"pid": self.product.pid, "quantity": 1}], "inside_dhaka"
        )
        save_draft(
            self.user, self.conv,
            resolved=totals["resolved"],
            item_total=totals["item_total"],
            delivery_charge=totals["delivery_charge"],
            grand_total=totals["grand_total"],
            delivery_zone="inside_dhaka",
            confirmation_status="draft",
            missing_fields=["customer_address"],
        )
        text = build_crm_snapshot(self.conv)
        self.assertIn("creates NOTHING yet", text)
        self.assertIn("customer_address", text)

    def test_snapshot_shows_draft_and_missing_info(self):
        self.conv.customer_phone = "01711111111"
        self.conv.save()
        set_current_product(self.conv, self.product.pid)  # open opportunity → sales context
        totals = compute_order_totals(
            self.user, [{"pid": self.product.pid, "quantity": 2}], "inside_dhaka"
        )
        save_draft(
            self.user, self.conv,
            resolved=totals["resolved"],
            item_total=totals["item_total"],
            delivery_charge=totals["delivery_charge"],
            grand_total=totals["grand_total"],
            delivery_zone="inside_dhaka",
            confirmation_status="awaiting_confirmation",
        )
        text = build_crm_snapshot(self.conv)
        self.assertIn("## ACTIVE ORDER DRAFT", text)
        self.assertIn("delivery address", text)  # missing info surfaced


class FitPromptTests(CrmBaseTestCase):
    def test_core_never_truncated(self):
        self.assertLess(len(CORE_PROMPT), MAX_PROMPT_LENGTH)

    def test_low_priority_sections_dropped_first(self):
        core = CORE_PROMPT.format(agent_name="A", store_name="S")
        sections = [
            (100, core),
            (95, "STORE_SECTION"),
            (50, "HINT_SECTION"),
        ]
        small = fit_prompt(sections, max_chars=len(core) + 30)
        self.assertIn(core, small)
        self.assertIn("STORE_SECTION", small)
        self.assertNotIn("HINT_SECTION", small)

    def test_no_section_sliced(self):
        core = CORE_PROMPT.format(agent_name="A", store_name="S")
        big = "X" * 500
        fitted = fit_prompt([(100, core), (90, big)], max_chars=len(core) + 100)
        # The big section is dropped whole; core remains intact.
        self.assertIn(core, fitted)
        self.assertNotIn(big, fitted)


class CrmModelsTests(CrmBaseTestCase):
    def test_profile_creation_scoped_to_user(self):
        other = User.objects.create_user(username="other1", password="x")
        conv2 = Conversation.objects.create(user=other, platform="telegram", customer_id="t1")
        p1 = get_or_create_profile(self.conv)[0]
        p2 = get_or_create_profile(conv2)[0]
        self.assertNotEqual(p1.user, p2.user)
        self.assertEqual(p1.conversation, self.conv)
