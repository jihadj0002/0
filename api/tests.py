from unittest.mock import MagicMock, patch

import json
from datetime import timedelta

from django.test import TestCase
from django.contrib.auth.models import User
from django.utils import timezone

from back.models import Conversation, Integration, Message, Product, Sale, ToolCallLog
from context.crm_models import OrderDraft
from context.models import SessionContext, StoreConfig
from api.ai.pipeline import _images_recently_sent, _maybe_auto_confirm_order, run
from api.ai.tools import execute_tool


class PipelineTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="seller2", password="x")
        self.conv = Conversation.objects.create(
            user=self.user,
            platform="messenger",
            customer_id="psid_abc",
            customer_name="Karim",
        )
        self.integration = Integration.objects.create(
            user=self.user,
            platform="messenger",
            access_token="tok",
            is_connected=True,
            is_enabled=True,
        )
        self.product = Product.objects.create(
            user=self.user,
            name="Mishti Doi",
            price=120,
            stock_quantity=20,
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
        self.conv.customer_phone = "01700000000"
        self.conv.customer_city = "Dhaka"
        self.conv.save()

    def _order_args(self, **overrides):
        args = {
            "customer_name": "Karim",
            "customer_phone": "01700000000",
            "customer_address": "Dhanmondi, Dhaka",
            "customer_city": "Dhaka",
            "delivery_zone": "inside_dhaka",
            "items": [{"pid": self.product.pid, "quantity": 2}],
        }
        args.update(overrides)
        return args


class CreateOrderToolTests(PipelineTestCase):
    def test_missing_address_drafts_and_waits_for_details(self):
        result = execute_tool("create_order", self._order_args(customer_address=""), self.user, self.conv)
        self.assertIn("missing_fields", result)
        self.assertIn("customer_address", result["missing_fields"])
        self.assertEqual(result["order_summary"]["grand_total"], "300")
        draft = OrderDraft.objects.get(conversation=self.conv)
        self.assertEqual(draft.confirmation_status, "draft")
        session = SessionContext.objects.get(conversation=self.conv)
        self.assertEqual(session.state, "awaiting_details")
        self.assertFalse(Sale.objects.filter(conversation=self.conv).exists())

    def test_qty_exceeding_stock_rejected_without_draft(self):
        result = execute_tool(
            "create_order", self._order_args(items=[{"pid": self.product.pid, "quantity": 99}]),
            self.user, self.conv,
        )
        self.assertEqual(result["error"], "Cannot create order")
        self.assertFalse(OrderDraft.objects.filter(conversation=self.conv).exists())

    def test_unknown_product_rejected(self):
        result = execute_tool(
            "create_order", self._order_args(items=[{"pid": "sku_nope", "quantity": 1}]),
            self.user, self.conv,
        )
        self.assertEqual(result["error"], "Cannot create order")

    def test_unconfirmed_returns_summary_and_waits(self):
        result = execute_tool("create_order", self._order_args(customer_confirmed=False),
                              self.user, self.conv)
        self.assertTrue(result["confirmation_required"])
        self.assertEqual(result["order_summary"]["item_total"], "240")
        self.assertEqual(result["order_summary"]["delivery_charge"], "60")
        self.assertEqual(result["order_summary"]["grand_total"], "300")
        self.assertFalse(Sale.objects.filter(conversation=self.conv).exists())
        draft = OrderDraft.objects.get(conversation=self.conv)
        self.assertEqual(draft.confirmation_status, "awaiting_confirmation")
        session = SessionContext.objects.get(conversation=self.conv)
        self.assertEqual(session.state, "awaiting_confirmation")

    def test_confirmed_creates_order_with_backend_totals(self):
        execute_tool("create_order", self._order_args(customer_confirmed=False),
                     self.user, self.conv)
        result = execute_tool("create_order", self._order_args(customer_confirmed=True),
                              self.user, self.conv)
        self.assertTrue(result["order_id"])
        self.assertEqual(result["total"], "300")
        sale = Sale.objects.get(conversation=self.conv)
        self.assertEqual(sale.amount, 300)
        self.assertEqual(sale.items.count(), 1)
        self.assertEqual(sale.items.first().quantity, 2)
        # stock decremented exactly once
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 18)
        draft = OrderDraft.objects.get(conversation=self.conv)
        self.assertEqual(draft.confirmation_status, "confirmed")
        self.assertEqual(draft.converted_order, sale)
        # workflow finished
        session = SessionContext.objects.get(conversation=self.conv)
        self.assertEqual(session.state, "completed")

    def test_confirmed_without_prior_draft_recreates_then_confirms(self):
        result = execute_tool("create_order", self._order_args(customer_confirmed=True),
                              self.user, self.conv)
        self.assertTrue(result["order_id"])
        self.assertEqual(Sale.objects.filter(conversation=self.conv).count(), 1)

    def test_outside_dhaka_delivery_charge(self):
        result = execute_tool(
            "create_order",
            self._order_args(delivery_zone="outside_dhaka", customer_confirmed=True),
            self.user, self.conv,
        )
        self.assertEqual(result["total"], "360")

    def test_duplicate_confirmed_call_is_idempotent(self):
        execute_tool("create_order", self._order_args(customer_confirmed=True),
                     self.user, self.conv)
        execute_tool("create_order", self._order_args(customer_confirmed=True),
                     self.user, self.conv)
        self.assertEqual(Sale.objects.filter(conversation=self.conv).count(), 1)

    def test_city_derived_from_address(self):
        # Address contains "ঢাকা" but no customer_city → backend derives it.
        self.conv.customer_city = ""
        self.conv.save()
        result = execute_tool(
            "create_order",
            self._order_args(customer_city="", customer_address="মিরপুর ১০, ঢাকা"),
            self.user, self.conv,
        )
        self.assertTrue(result.get("confirmation_required") or result.get("order_id"))
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.customer_city, "ঢাকা")

    def test_city_not_derived_from_non_city_address(self):
        self.conv.customer_city = ""
        self.conv.save()
        result = execute_tool(
            "create_order",
            self._order_args(customer_city="", customer_address="মিরপুর ১০"),
            self.user, self.conv,
        )
        self.assertIn("customer_city", result.get("missing_fields", []))
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.customer_city, "")


class AutoConfirmGuardTests(PipelineTestCase):
    def _set_awaiting_confirmation(self):
        result = execute_tool("create_order", self._order_args(customer_confirmed=False),
                              self.user, self.conv)
        self.assertTrue(result["confirmation_required"])

    def test_affirmative_creates_order_when_draft_pending(self):
        self._set_awaiting_confirmation()
        reply = _maybe_auto_confirm_order(self.conv, "হ্যাঁ", create_order_called=False)
        self.assertIsNotNone(reply)
        self.assertIn("অর্ডারটি তৈরি হয়েছে", reply)
        self.assertEqual(Sale.objects.filter(conversation=self.conv).count(), 1)
        draft = OrderDraft.objects.get(conversation=self.conv)
        self.assertEqual(draft.confirmation_status, "confirmed")

    def test_english_ok_creates_order(self):
        self._set_awaiting_confirmation()
        reply = _maybe_auto_confirm_order(self.conv, "ok", create_order_called=False)
        self.assertIsNotNone(reply)
        self.assertEqual(Sale.objects.filter(conversation=self.conv).count(), 1)

    def test_non_affirmative_never_triggers(self):
        self._set_awaiting_confirmation()
        for text in ("আমি ok আছি", "হ্যাঁ ভাই ঠিক আছে", "ok but can you reduce the price?",
                     "দাম কত?", "আচ্ছা", "ঠিক আছে তাহলে পরে করবো"):
            self.assertIsNone(_maybe_auto_confirm_order(self.conv, text, create_order_called=False))
        self.assertFalse(Sale.objects.filter(conversation=self.conv).exists())

    def test_long_text_never_triggers(self):
        self._set_awaiting_confirmation()
        long_yes = "হ্যাঁ" + "!" * 50
        self.assertIsNone(_maybe_auto_confirm_order(self.conv, long_yes, create_order_called=False))

    def test_no_order_when_llm_called_create_order(self):
        self._set_awaiting_confirmation()
        self.assertIsNone(_maybe_auto_confirm_order(self.conv, "হ্যাঁ", create_order_called=True))
        self.assertFalse(Sale.objects.filter(conversation=self.conv).exists())

    def test_no_order_without_pending_draft(self):
        self.assertIsNone(_maybe_auto_confirm_order(self.conv, "হ্যাঁ", create_order_called=False))
        self.assertFalse(Sale.objects.filter(conversation=self.conv).exists())

    def test_no_order_after_draft_confirmed_already(self):
        self._set_awaiting_confirmation()
        _maybe_auto_confirm_order(self.conv, "হ্যাঁ", create_order_called=False)
        # Second "yes" — draft now confirmed → guard refuses, no new order.
        self.assertIsNone(_maybe_auto_confirm_order(self.conv, "হ্যাঁ", create_order_called=False))
        self.assertEqual(Sale.objects.filter(conversation=self.conv).count(), 1)


class PipelineRunTests(PipelineTestCase):
    """End-to-end: customer says 'yes' → LLM replies without create_order →
    backend auto-confirm creates the order and the bot reply announces it."""

    def _fake_llm(self, content="ঠিক আছে!", tool_calls=None):
        def fake_call_llm(messages, tools=None, model=None, temperature=0.7, max_tokens=1024):
            msg = MagicMock()
            msg.content = content
            msg.tool_calls = tool_calls
            return msg, {"model": model or "fake", "input_tokens": 10, "output_tokens": 5}
        return fake_call_llm

    def _run_pipeline(self, customer_text):
        incoming = Message.objects.create(
            conversation=self.conv, sender="customer", text=customer_text,
        )
        run(self.conv, incoming)

    def test_affirmative_yes_auto_confirm_end_to_end(self):
        execute_tool("create_order", self._order_args(customer_confirmed=False),
                     self.user, self.conv)
        with patch("api.ai.pipeline.call_llm", self._fake_llm(content="ঠিক আছে!")):
            with patch("api.ai.pipeline.send_reply", return_value=None):
                with patch("billing.deductions.deduct_for_reply", return_value=None):
                    self._run_pipeline("হ্যাঁ")
        self.assertEqual(Sale.objects.filter(conversation=self.conv).count(), 1)
        bot_msg = Message.objects.filter(conversation=self.conv, sender="bot").latest("timestamp")
        self.assertIn("অর্ডারটি তৈরি হয়েছে", bot_msg.text)
        self.assertIn(Sale.objects.get(conversation=self.conv).oid, bot_msg.text)

    def test_non_affirmative_llm_reply_not_overridden(self):
        execute_tool("create_order", self._order_args(customer_confirmed=False),
                     self.user, self.conv)
        reply_text = "আর কিছু জানতে চান?"
        with patch("api.ai.pipeline.call_llm", self._fake_llm(content=reply_text)):
            with patch("api.ai.pipeline.send_reply", return_value=None):
                with patch("billing.deductions.deduct_for_reply", return_value=None):
                    self._run_pipeline("আমি ok আছি")
        self.assertFalse(Sale.objects.filter(conversation=self.conv).exists())
        bot_msg = Message.objects.filter(conversation=self.conv, sender="bot").latest("timestamp")
        self.assertEqual(bot_msg.text, reply_text)

    def test_llm_calling_create_order_confirmed_wins(self):
        # Documented: the guard skips when the LLM itself called create_order.
        self._set_awaiting_confirmation = None  # noqa
        tool_call = MagicMock()
        tool_call.id = "call_1"
        tool_call.function.name = "create_order"
        tool_call.function.arguments = (
            '{"customer_name":"Karim","customer_phone":"01700000000",'
            '"customer_address":"Dhanmondi, Dhaka","customer_city":"Dhaka",'
            '"delivery_zone":"inside_dhaka","items":[{"pid":"' + self.product.pid + '","quantity":1}],'
            '"customer_confirmed":true}'
        )
        tool_call_2 = MagicMock()
        tool_call_2.id = "call_2"
        tool_call_2.function.name = "think"
        tool_call_2.function.arguments = '{"notes":"done"}'

        calls = [
            self._fake_llm("", tool_calls=[tool_call, tool_call_2])(None),
            self._fake_llm("অর্ডারটি তৈরি হয়েছে!")(None),
        ]

        def fake_llm(messages, tools=None, model=None, temperature=0.7, max_tokens=1024):
            return calls.pop(0)

        with patch("api.ai.pipeline.call_llm", fake_llm):
            with patch("api.ai.pipeline.send_reply", return_value=None):
                with patch("billing.deductions.deduct_for_reply", return_value=None):
                    self._run_pipeline("হ্যাঁ অর্ডার কনফার্ম করলাম")
        self.assertEqual(Sale.objects.filter(conversation=self.conv).count(), 1)


class SendImagesRepeatGuardTests(PipelineTestCase):
    def test_no_history_means_not_recently_sent(self):
        self.assertFalse(_images_recently_sent(self.conv, [self.product.pid]))

    def test_recent_send_flagged(self):
        # Production stores arguments as a dict (JSONField) — must be handled.
        ToolCallLog.objects.create(
            conversation=self.conv,
            user=self.user,
            reply_id="r" * 32,
            iteration=0,
            tool_name="send_images",
            execution_time_ms=1,
            result_summary="sent",
            arguments={"pid": self.product.pid},
        )
        self.assertTrue(_images_recently_sent(self.conv, [self.product.pid]))
        # A different pid is not flagged.
        self.assertFalse(_images_recently_sent(self.conv, ["sku_other"]))

    def test_recent_send_flagged_pids_list(self):
        ToolCallLog.objects.create(
            conversation=self.conv,
            user=self.user,
            reply_id="r" * 32,
            iteration=0,
            tool_name="send_images",
            execution_time_ms=1,
            result_summary="sent",
            arguments={"pids": ["a", "b", self.product.pid]},
        )
        self.assertTrue(_images_recently_sent(self.conv, [self.product.pid]))

    def test_old_send_not_flagged(self):
        log = ToolCallLog.objects.create(
            conversation=self.conv,
            user=self.user,
            reply_id="r" * 32,
            iteration=0,
            tool_name="send_images",
            execution_time_ms=1,
            result_summary="sent",
            arguments={"pid": self.product.pid},
        )
        # timestamp is auto_now_add — backdate to 1h ago via update().
        ToolCallLog.objects.filter(pk=log.pk).update(
            timestamp=timezone.now() - timedelta(hours=1)
        )
        self.assertFalse(_images_recently_sent(self.conv, [self.product.pid]))