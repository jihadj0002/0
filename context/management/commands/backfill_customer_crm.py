"""Backfill per-user CRM rows (CustomerProfile + SalesOpportunity) for all
existing conversations. Bulk-optimized for large datasets.

Usage: python manage.py backfill_customer_crm [--dry-run]
"""
from django.core.management.base import BaseCommand
from django.db.models import Count, Min, Sum
from django.utils import timezone

_INTENT_TO_STAGE = {
    "purchase": "product_interest",
    "buy": "product_interest",
    "order": "product_interest",
    "checkout": "ready_to_buy",
    "price": "considering",
    "inquiry": "considering",
    "support": "discovery",
    "complaint": "discovery",
}


def infer_stage(intent):
    return _INTENT_TO_STAGE.get((intent or "").lower(), "discovery")


class Command(BaseCommand):
    help = "Create CustomerProfile + SalesOpportunity rows for existing conversations"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report counts without writing")

    def handle(self, *args, **options):
        from back.models import Conversation, Sale
        from context.crm_models import CustomerProfile, SalesOpportunity

        conversations = list(
            Conversation.objects.values(
                "id", "user_id", "customer_id", "platform", "customer_name",
                "customer_phone", "customer_city", "customer_address",
                "detected_intent", "updated_at",
            )
        )

        existing_profile_conv = set(
            CustomerProfile.objects.values_list("conversation_id", flat=True)
        )
        existing_opp_conv = set(
            SalesOpportunity.objects.values_list("conversation_id", flat=True)
        )

        # One aggregate query for all order stats.
        order_stats = {
            row["conversation_id"]: row
            for row in Sale.objects
            .exclude(status="refunded")
            .values("conversation_id")
            .annotate(
                cnt=Count("id"),
                spent=Sum("amount"),
                first=Min("created_at"),
            )
        }

        to_create_p = []
        to_create_o = []
        to_update_p = []

        for c in conversations:
            conv_id = c["id"]
            stats = order_stats.get(conv_id)
            if stats:
                cnt = stats["cnt"]
                spent = stats["spent"] or 0
                first = stats["first"]
            else:
                cnt, spent, first = 0, 0, None
            lifecycle = "lead"
            if cnt == 1:
                lifecycle = "customer"
            elif cnt > 1:
                lifecycle = "repeat_customer"
            if spent is not None and spent >= 50000 and cnt >= 1:
                lifecycle = "vip"

            if conv_id not in existing_profile_conv:
                to_create_p.append(
                    CustomerProfile(
                        user_id=c["user_id"],
                        conversation_id=conv_id,
                        customer_id=c["customer_id"] or "",
                        platform=c["platform"],
                        name=c["customer_name"] or "",
                        phone=c["customer_phone"] or "",
                        city=c["customer_city"] or "",
                        address=c["customer_address"] or "",
                        last_contact_at=c["updated_at"] or timezone.now(),
                        order_count=cnt,
                        total_spent=spent or 0,
                        first_order_at=first,
                        lifecycle_stage=lifecycle,
                    )
                )
            elif cnt:
                to_update_p.append((conv_id, cnt, spent or 0, first, lifecycle))

            if conv_id not in existing_opp_conv:
                stage = "won" if lifecycle in ("customer", "repeat_customer", "vip") else infer_stage(c["detected_intent"])
                to_create_o.append(
                    SalesOpportunity(
                        user_id=c["user_id"],
                        conversation_id=conv_id,
                        stage=stage,
                        status="won" if stage == "won" else "open",
                        intent=c["detected_intent"] or "",
                    )
                )

        if options["dry_run"]:
            self.stdout.write(
                f"[dry-run] {len(conversations)} conversations → would create "
                f"{len(to_create_p)} profiles, {len(to_create_o)} opportunities, "
                f"update {len(to_update_p)} profiles"
            )
            return

        if to_create_p:
            CustomerProfile.objects.bulk_create(to_create_p, batch_size=500)
        if to_create_o:
            SalesOpportunity.objects.bulk_create(to_create_o, batch_size=500)

        updated_p = 0
        for conv_id, cnt, spent, first, lifecycle in to_update_p:
            updated_p += CustomerProfile.objects.filter(conversation_id=conv_id).update(
                order_count=cnt,
                total_spent=spent,
                first_order_at=first,
                lifecycle_stage=lifecycle,
            )

        self.stdout.write(self.style.SUCCESS(
            f"Done: {len(conversations)} conversations → created "
            f"{len(to_create_p)} profiles, {len(to_create_o)} opportunities, "
            f"updated {updated_p} profiles"
        ))