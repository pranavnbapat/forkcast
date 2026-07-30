from django.core.management.base import BaseCommand

from catalog.models import Supermarket
from catalog.services.dedupe import dedupe_products_by_external_id


class Command(BaseCommand):
    help = "Merge duplicate Albert Heijn products by external_id."

    def handle(self, *args, **options):
        supermarket = Supermarket.objects.get(slug="albert-heijn")
        deleted = dedupe_products_by_external_id(supermarket)
        self.stdout.write(self.style.SUCCESS(f"Merged and removed {deleted} duplicate products."))
