"""Management command: sync products from external sources into local cache.

    python manage.py sync_products                 # all active non-internal sources
    python manage.py sync_products --user alice     # only this user's active source
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from api.products.sync import sync_products

User = get_user_model()


class Command(BaseCommand):
    help = "Sync products from active external ProductSources into the local cache."

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            dest="username",
            default=None,
            help="Limit sync to a single username.",
        )

    def handle(self, *args, **options):
        from back.models import ProductSource

        qs = ProductSource.objects.filter(is_active=True).exclude(provider="internal")

        username = options.get("username")
        if username:
            try:
                user = User.objects.get(username=username)
            except User.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"User '{username}' not found."))
                return
            qs = qs.filter(user=user)

        sources = list(qs)
        if not sources:
            self.stdout.write("No active non-internal sources to sync.")
            return

        for source in sources:
            self.stdout.write(f"Syncing {source.sid} ({source.provider}) for {source.user}...")
            result = sync_products(source)
            self.stdout.write(
                self.style.SUCCESS(
                    f"  created={result['created']} updated={result['updated']} "
                    f"errors={len(result['errors'])}"
                )
            )
            for err in result["errors"][:10]:
                self.stderr.write(f"  ! {err}")
