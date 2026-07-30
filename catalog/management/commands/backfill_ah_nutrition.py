import time

from django.core.management.base import BaseCommand

from catalog.models import ImportRun, NutritionFacts, Product, Supermarket
from catalog.services.ah_api import AHAPIError, AHAPIImporter
from catalog.services.product_filters import food_candidate_queryset


class Command(BaseCommand):
    help = "Continuously backfill Albert Heijn product nutrition in batches until exhausted."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=1000)
        parser.add_argument("--pause-seconds", type=float, default=1.0)
        parser.add_argument("--max-batches", type=int, default=0)
        parser.add_argument("--progress-every", type=int, default=25)

    def handle(self, *args, **options):
        supermarket = Supermarket.objects.get(slug="albert-heijn")
        batch_size = options["batch_size"]
        pause_seconds = options["pause_seconds"]
        max_batches = options["max_batches"]
        progress_every = max(1, options["progress_every"])
        importer = AHAPIImporter(supermarket=supermarket)

        batch_number = 0
        total_scraped = 0
        total_missing_at_start = self._missing_queryset(supermarket).count()

        self.stdout.write(
            f"Starting AH nutrition backfill with batch_size={batch_size}, pause_seconds={pause_seconds}."
        )

        try:
            while True:
                if max_batches and batch_number >= max_batches:
                    break

                product_ids = list(
                    self._missing_queryset(supermarket)
                    .values_list("id", flat=True)
                    .order_by("id")[:batch_size]
                )
                if not product_ids:
                    break

                before = Product.objects.filter(supermarket=supermarket).count()
                scraped = 0
                batch_number += 1

                try:
                    for product in Product.objects.filter(id__in=product_ids).order_by("id"):
                        importer.sync_product_from_url(product.source_url)
                        scraped += 1
                        if scraped % progress_every == 0 or scraped == len(product_ids):
                            self.stdout.write(
                                (
                                    f"Batch {batch_number}: {scraped}/{len(product_ids)} "
                                    f"processed, current={product.id} {product.name}"
                                )
                            )
                except AHAPIError as exc:
                    ImportRun.objects.create(
                        supermarket=supermarket,
                        importer="ah_api",
                        mode="detail_backfill",
                        query="",
                        sort_on="",
                        start_page=0,
                        pages_visited=0,
                        rows_imported=scraped,
                        unique_products_added=max(
                            0, Product.objects.filter(supermarket=supermarket).count() - before
                        ),
                        status="failed",
                        notes=(
                            f"background=True; batch_number={batch_number}; "
                            f"batch_size={batch_size}; pause_seconds={pause_seconds}; error={exc}"
                        ),
                    )
                    raise

                total_scraped += scraped
                remaining = self._missing_queryset(supermarket).count()
                ImportRun.objects.create(
                    supermarket=supermarket,
                    importer="ah_api",
                    mode="detail_backfill",
                    query="",
                    sort_on="",
                    start_page=0,
                    pages_visited=0,
                    rows_imported=scraped,
                    unique_products_added=max(
                        0, Product.objects.filter(supermarket=supermarket).count() - before
                    ),
                    status="completed",
                    notes=(
                        f"background=True; batch_number={batch_number}; "
                        f"batch_size={batch_size}; pause_seconds={pause_seconds}; remaining_missing={remaining}"
                    ),
                )
                self.stdout.write(
                    f"Batch {batch_number}: scraped {scraped} products, remaining missing nutrition={remaining}."
                )
                if pause_seconds > 0:
                    time.sleep(pause_seconds)
        finally:
            importer.close()

        self.stdout.write(
            self.style.SUCCESS(
                (
                    f"Finished AH nutrition backfill after {batch_number} batches. "
                    f"Started with {total_missing_at_start} missing products and scraped {total_scraped}."
                )
            )
        )

    def _missing_queryset(self, supermarket: Supermarket):
        return food_candidate_queryset(
            Product.objects.filter(
                supermarket=supermarket,
                is_active=True,
                nutrition_unavailable=False,
            )
        ).exclude(
            id__in=NutritionFacts.objects.filter(
                product__supermarket=supermarket,
                entries__isnull=False,
            ).values_list("product_id", flat=True)
        )
