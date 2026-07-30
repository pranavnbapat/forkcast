from django.core.management.base import BaseCommand, CommandError

from catalog.models import ImportRun, Product, ProductQualityProfile, Supermarket
from catalog.services.product_quality import ProductQualityEnricher, ProductQualityError
from catalog.services.product_filters import food_candidate_queryset


class Command(BaseCommand):
    help = "Use the configured LLM to estimate shelf-life, spoilage, and sensor-relevant quality signals for products."

    def add_arguments(self, parser):
        parser.add_argument("--supermarket-slug", default="albert-heijn")
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--product-id", type=int)
        parser.add_argument("--name-contains", default="")
        parser.add_argument("--source-name", default="vllm_default")
        parser.add_argument("--missing-only", action="store_true", default=False)
        parser.add_argument("--force", action="store_true", default=False)

    def handle(self, *args, **options):
        try:
            supermarket = Supermarket.objects.get(slug=options["supermarket_slug"])
        except Supermarket.DoesNotExist as exc:
            raise CommandError(f"Unknown supermarket slug: {options['supermarket_slug']}") from exc

        queryset = food_candidate_queryset(
            Product.objects.filter(supermarket=supermarket, is_active=True)
        ).select_related("nutrition_facts")

        if options["product_id"]:
            queryset = queryset.filter(id=options["product_id"])
        if options["name_contains"]:
            queryset = queryset.filter(name__icontains=options["name_contains"])
        if options["missing_only"] and not options["force"]:
            queryset = queryset.exclude(
                quality_profiles__source_type=ProductQualityProfile.SourceType.LLM,
                quality_profiles__source_name=options["source_name"],
            )

        products = list(queryset.order_by("id")[: options["limit"]])
        if not products:
            self.stdout.write("No matching products to enrich.")
            return

        enricher = ProductQualityEnricher(source_name=options["source_name"])
        completed = 0

        for product in products:
            try:
                enricher.enrich_product(product)
                completed += 1
                self.stdout.write(f"[{completed}/{len(products)}] enriched {product.id} {product.name}")
            except ProductQualityError as exc:
                ImportRun.objects.create(
                    supermarket=supermarket,
                    importer="vllm",
                    mode="product_quality_enrichment",
                    query=options["name_contains"],
                    sort_on="",
                    start_page=0,
                    pages_visited=0,
                    rows_imported=completed,
                    unique_products_added=0,
                    status="failed",
                    notes=f"source_name={options['source_name']}; product_id={product.id}; error={exc}",
                )
                raise CommandError(f"Failed on product {product.id} {product.name}: {exc}") from exc

        ImportRun.objects.create(
            supermarket=supermarket,
            importer="vllm",
            mode="product_quality_enrichment",
            query=options["name_contains"],
            sort_on="",
            start_page=0,
            pages_visited=0,
            rows_imported=completed,
            unique_products_added=0,
            status="completed",
            notes=(
                f"source_name={options['source_name']}; "
                f"missing_only={options['missing_only']}; force={options['force']}; "
                f"product_id={options['product_id'] or ''}"
            ),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Enriched {completed} product quality profiles from LLM source '{options['source_name']}'."
            )
        )
