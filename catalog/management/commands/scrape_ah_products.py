from django.core.management.base import BaseCommand

from catalog.models import ImportRun, NutritionFacts, Product, Supermarket
from catalog.services.ah_api import AHAPIError, AHAPIImporter


class Command(BaseCommand):
    help = "Scrape Albert Heijn product detail pages into the database."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)
        parser.add_argument("--product-id", type=int, action="append")
        parser.add_argument("--source-url", action="append")
        parser.add_argument("--stale-only", action="store_true")
        parser.add_argument("--missing-nutrition", action="store_true")
        parser.add_argument("--missing-description", action="store_true")

    def handle(self, *args, **options):
        supermarket = Supermarket.objects.get(slug="albert-heijn")
        products = Product.objects.filter(supermarket=supermarket, is_active=True).order_by("id")
        before = Product.objects.filter(supermarket=supermarket).count()

        product_ids = options["product_id"] or []
        source_urls = options["source_url"] or []
        if product_ids:
            products = products.filter(id__in=product_ids)
        elif source_urls:
            for source_url in source_urls:
                Product.objects.get_or_create(
                    supermarket=supermarket,
                    source_url=source_url,
                    defaults={"name": source_url.rsplit("/", 1)[-1]},
                )
            products = products.filter(source_url__in=source_urls)
        if options["stale_only"]:
            products = products.filter(last_scraped_at__isnull=True)
        if options["missing_nutrition"]:
            products = products.exclude(
                id__in=NutritionFacts.objects.filter(
                    product__supermarket=supermarket,
                    entries__isnull=False,
                ).values_list("product_id", flat=True)
            )
        if options["missing_description"]:
            products = products.filter(description="")

        importer = AHAPIImporter(supermarket=supermarket)
        try:
            scraped = 0
            for product in products:
                if options["limit"] is not None and scraped >= options["limit"]:
                    break
                importer.sync_product_from_url(product.source_url)
                scraped += 1
        except AHAPIError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return
        finally:
            importer.close()
        ImportRun.objects.create(
            supermarket=supermarket,
            importer="ah_api",
            mode="detail_backfill",
            query="",
            sort_on="",
            start_page=0,
            pages_visited=0,
            rows_imported=scraped,
            unique_products_added=max(0, Product.objects.filter(supermarket=supermarket).count() - before),
            status="completed",
            notes=(
                f"stale_only={options['stale_only']}; "
                f"missing_nutrition={options['missing_nutrition']}; "
                f"missing_description={options['missing_description']}"
            ),
        )
        self.stdout.write(self.style.SUCCESS(f"Scraped {scraped} products."))
