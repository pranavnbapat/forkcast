from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from catalog.models import DataSource, Product, ProductIdentifier


class Command(BaseCommand):
    help = (
        "Record the identifiers already present on stored products into ProductIdentifier. "
        "Reads Product.external_id (the AH webshop id) and, with --from-snapshots, the hqId "
        "found in the latest product snapshot payload."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--source-slug",
            default="albert-heijn",
            help="DataSource slug to attribute the identifiers to.",
        )
        parser.add_argument(
            "--supermarket-slug",
            default="albert-heijn",
            help="Only process products belonging to this supermarket.",
        )
        parser.add_argument(
            "--from-snapshots",
            action="store_true",
            help="Also extract hqId from the latest snapshot payload (slower).",
        )
        parser.add_argument("--batch-size", type=int, default=1000)
        parser.add_argument("--limit", type=int, default=0, help="0 means no limit.")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be written without writing anything.",
        )

    def handle(self, *args, **options):
        try:
            source = DataSource.objects.get(slug=options["source_slug"])
        except DataSource.DoesNotExist as exc:
            raise CommandError(
                f"DataSource '{options['source_slug']}' does not exist. Run migrations first."
            ) from exc

        queryset = Product.objects.filter(
            supermarket__slug=options["supermarket_slug"]
        ).exclude(external_id="").order_by("id")
        if options["limit"]:
            queryset = queryset[: options["limit"]]

        existing = set(
            ProductIdentifier.objects.filter(source=source).values_list(
                "product_id", "id_type", "value"
            )
        )

        pending = []
        created = 0
        skipped_existing = 0
        no_numeric = 0

        for product in queryset.iterator(chunk_size=options["batch_size"]):
            rows = [(ProductIdentifier.IdType.AH_WEBSHOP_ID, product.external_id)]

            numeric = "".join(char for char in product.external_id if char.isdigit())
            if not numeric:
                no_numeric += 1

            if options["from_snapshots"]:
                snapshot = product.snapshots.order_by("-scraped_at", "-id").first()
                card = (snapshot.payload or {}).get("product_card", {}) if snapshot else {}
                hq_id = card.get("hqId")
                if hq_id not in (None, ""):
                    rows.append((ProductIdentifier.IdType.AH_HQ_ID, str(hq_id)))

            for id_type, value in rows:
                if (product.id, id_type, value) in existing:
                    skipped_existing += 1
                    continue
                pending.append(
                    ProductIdentifier(
                        product=product,
                        source=source,
                        id_type=id_type,
                        value=value,
                        is_primary=id_type == ProductIdentifier.IdType.AH_WEBSHOP_ID,
                        match_method=ProductIdentifier.MatchMethod.API,
                    )
                )

            if len(pending) >= options["batch_size"]:
                created += self._flush(pending, dry_run=options["dry_run"])
                pending = []

        created += self._flush(pending, dry_run=options["dry_run"])

        prefix = "Would create" if options["dry_run"] else "Created"
        self.stdout.write(
            self.style.SUCCESS(
                f"{prefix} {created} identifiers for source '{source.slug}'. "
                f"Skipped {skipped_existing} already recorded. "
                f"{no_numeric} products had no numeric part in external_id."
            )
        )

    def _flush(self, pending, *, dry_run: bool) -> int:
        if not pending:
            return 0
        if dry_run:
            return len(pending)
        with transaction.atomic():
            ProductIdentifier.objects.bulk_create(pending, ignore_conflicts=True)
        return len(pending)
