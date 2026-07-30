from django.core.management.base import BaseCommand, CommandError

from catalog.models import Product
from catalog.services.opensearch import OpenSearchError, ProductOpenSearchIndex
from catalog.services.product_filters import food_candidate_queryset


class Command(BaseCommand):
    help = "Index local food-candidate products into OpenSearch for fast autocomplete and typo-tolerant search."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=1000)
        parser.add_argument("--offset", type=int, default=0)
        parser.add_argument("--product-id", type=int)
        parser.add_argument("--name-contains", default="")
        parser.add_argument("--batch-size", type=int, default=1000)
        parser.add_argument("--all", action="store_true", dest="index_all", default=False)
        parser.add_argument("--recreate", action="store_true", default=False)

    def handle(self, *args, **options):
        index = ProductOpenSearchIndex()
        if not index.is_configured():
            raise CommandError("OpenSearch is not configured. Set OPENSEARCH_ENABLED=1 and OPENSEARCH_URL.")
        if options["recreate"]:
            try:
                index.recreate_index()
            except OpenSearchError as exc:
                raise CommandError(str(exc)) from exc

        queryset = food_candidate_queryset(
            Product.objects.filter(supermarket__slug="albert-heijn", is_active=True).select_related("nutrition_facts")
        ).order_by("id")
        if options["product_id"]:
            queryset = queryset.filter(id=options["product_id"])
        if options["name_contains"]:
            queryset = queryset.filter(name__icontains=options["name_contains"])
        if options["offset"]:
            queryset = queryset[options["offset"] :]
        try:
            if options["index_all"]:
                count = index.bulk_index_queryset(queryset, batch_size=options["batch_size"])
            else:
                products = list(queryset[: options["limit"]])
                if not products:
                    self.stdout.write("No products selected for indexing.")
                    return
                count = index.index_products(products)
        except OpenSearchError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(f"Indexed {count} products into OpenSearch index '{index.index_name}'.")) 
