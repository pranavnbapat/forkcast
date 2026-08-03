from django.core.management.base import BaseCommand, CommandError

from catalog.models import Product
from catalog.services.nutrition_resolution import NutritionResolutionError, resolve_queryset
from catalog.services.product_filters import food_candidate_queryset


class Command(BaseCommand):
    help = (
        "Resolve one canonical NutritionFacts row per product from all available "
        "sources, trust-ordered and field by field. Higher-trust values are never "
        "overwritten; gaps are filled from lower-trust sources and recorded."
    )

    def add_arguments(self, parser):
        parser.add_argument("--supermarket-slug", default="albert-heijn")
        parser.add_argument(
            "--only-linked",
            action="store_true",
            help="Only products that have an OpenFoodFacts link.",
        )
        parser.add_argument(
            "--trusted-matches-only",
            action="store_true",
            help="Ignore fuzzy OFF links; use only deterministic ones.",
        )
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument("--progress-every", type=int, default=2000)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        queryset = food_candidate_queryset(
            Product.objects.filter(
                supermarket__slug=options["supermarket_slug"], is_active=True
            ).select_related("nutrition_facts")
        )
        if options["only_linked"]:
            queryset = queryset.filter(identifiers__source__slug="openfoodfacts").distinct()
        queryset = queryset.order_by("id")
        if options["limit"]:
            queryset = queryset[: options["limit"]]

        try:
            stats = resolve_queryset(
                queryset,
                trusted_matches_only=options["trusted_matches_only"],
                dry_run=options["dry_run"],
                progress_every=options["progress_every"],
                stdout=self.stdout,
            )
        except NutritionResolutionError as exc:
            raise CommandError(str(exc)) from exc

        prefix = "Would resolve" if options["dry_run"] else "Resolved"
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix}: considered={stats['considered']} created={stats['created']} "
                f"updated={stats['updated']} no_data={stats['no_data']} "
                f"gap_filled_products={stats['gap_filled_products']} "
                f"gap_filled_fields={stats['gap_filled_fields']}"
            )
        )
