from decimal import Decimal

from django.core.management.base import BaseCommand

PLANS = [
    {
        "name": "free",
        "monthly_credits": Decimal("50.0000"),
        "max_messages_per_month": 100,
        "allowed_models": ["openai/gpt-4o-mini"],
        "price_per_month": Decimal("0.00"),
    },
    {
        "name": "basic",
        "monthly_credits": Decimal("500.0000"),
        "max_messages_per_month": 1000,
        "allowed_models": ["openai/gpt-4o-mini", "openai/gpt-4o"],
        "price_per_month": Decimal("9.99"),
    },
    {
        "name": "pro",
        "monthly_credits": Decimal("2000.0000"),
        "max_messages_per_month": 0,
        "allowed_models": ["openai/gpt-4o-mini", "openai/gpt-4o", "anthropic/claude-3-5-sonnet", "anthropic/claude-3-haiku"],
        "price_per_month": Decimal("29.99"),
    },
    {
        "name": "enterprise",
        "monthly_credits": Decimal("10000.0000"),
        "max_messages_per_month": 0,
        "allowed_models": [],
        "price_per_month": Decimal("99.99"),
    },
]

# Credits per 1K tokens (1 credit ≈ roughly $0.001 for easy mental math)
MODEL_PRICING = [
    {"model_id": "openai/gpt-4o-mini",                           "credits_per_1k_input": Decimal("0.000150"), "credits_per_1k_output": Decimal("0.000600")},
    {"model_id": "openai/gpt-4o",                                "credits_per_1k_input": Decimal("0.002500"), "credits_per_1k_output": Decimal("0.010000")},
    {"model_id": "anthropic/claude-3-5-sonnet",                  "credits_per_1k_input": Decimal("0.003000"), "credits_per_1k_output": Decimal("0.015000")},
    {"model_id": "anthropic/claude-3-haiku",                     "credits_per_1k_input": Decimal("0.000250"), "credits_per_1k_output": Decimal("0.001250")},
    {"model_id": "anthropic/claude-3-5-haiku",                   "credits_per_1k_input": Decimal("0.000800"), "credits_per_1k_output": Decimal("0.004000")},
    {"model_id": "meta-llama/llama-3.1-70b-instruct",            "credits_per_1k_input": Decimal("0.000350"), "credits_per_1k_output": Decimal("0.000400")},
    {"model_id": "meta-llama/llama-3.1-8b-instruct",             "credits_per_1k_input": Decimal("0.000050"), "credits_per_1k_output": Decimal("0.000080")},
    {"model_id": "google/gemini-flash-1.5",                      "credits_per_1k_input": Decimal("0.000075"), "credits_per_1k_output": Decimal("0.000300")},
    {"model_id": "google/gemini-pro-1.5",                        "credits_per_1k_input": Decimal("0.001250"), "credits_per_1k_output": Decimal("0.005000")},
    {"model_id": "mistralai/mistral-nemo",                       "credits_per_1k_input": Decimal("0.000130"), "credits_per_1k_output": Decimal("0.000130")},
]


class Command(BaseCommand):
    help = "Seed default Plans and ModelPricing entries"

    def handle(self, *args, **options):
        from billing.models import ModelPricing, Plan

        for plan_data in PLANS:
            plan, created = Plan.objects.update_or_create(
                name=plan_data["name"],
                defaults=plan_data,
            )
            action = "Created" if created else "Updated"
            self.stdout.write(f"  {action} plan: {plan}")

        self.stdout.write("")

        for mp_data in MODEL_PRICING:
            mp, created = ModelPricing.objects.update_or_create(
                model_id=mp_data["model_id"],
                defaults=mp_data,
            )
            action = "Created" if created else "Updated"
            self.stdout.write(f"  {action} pricing: {mp}")

        self.stdout.write(self.style.SUCCESS("\nBilling setup complete."))
