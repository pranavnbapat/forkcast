from django.core.management.base import BaseCommand, CommandError

from catalog.services.matching import DEFAULT_THRESHOLD, match_by_barcode, match_by_name
from catalog.services.openfoodfacts import OpenFoodFactsError


class Command(BaseCommand):
    help = (
        "Link staged OpenFoodFacts records to local products. Runs the deterministic "
        "GTIN join first, then optionally the fuzzy name matcher. Fuzzy matches are "
        "recorded with match_method=fuzzy_name and a confidence label so they stay "
        "distinguishable from deterministic ones."
    )

    def add_arguments(self, parser):
        parser.add_argument("--supermarket-slug", default="albert-heijn")
        parser.add_argument(
            "--threshold",
            type=float,
            default=DEFAULT_THRESHOLD,
            help=f"Similarity floor for fuzzy matching (default {DEFAULT_THRESHOLD}).",
        )
        parser.add_argument(
            "--country-tag",
            default="",
            help="Restrict OFF candidates to this countries_tag.",
        )
        parser.add_argument("--limit", type=int, default=0)
        parser.add_argument(
            "--barcode-only",
            action="store_true",
            help="Run only the deterministic GTIN join, skipping fuzzy matching.",
        )
        parser.add_argument(
            "--rematch",
            action="store_true",
            help="Also consider products that already have an OFF link.",
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        try:
            barcode_stats = match_by_barcode(
                supermarket_slug=options["supermarket_slug"],
                dry_run=options["dry_run"],
            )
        except OpenFoodFactsError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            "Barcode join: "
            f"gtin_identifiers={barcode_stats['candidates']} "
            f"matched={barcode_stats['matched']} created={barcode_stats['created']}"
            + (f" ({barcode_stats['note']})" if barcode_stats["note"] else "")
        )

        if options["barcode_only"]:
            self.stdout.write(self.style.SUCCESS("Barcode-only run complete."))
            return

        name_stats = match_by_name(
            supermarket_slug=options["supermarket_slug"],
            threshold=options["threshold"],
            country_tag=options["country_tag"],
            limit=options["limit"],
            skip_already_matched=not options["rematch"],
            dry_run=options["dry_run"],
        )

        if name_stats["note"]:
            self.stdout.write(self.style.WARNING(f"Fuzzy match skipped: {name_stats['note']}"))
            return

        self.stdout.write(
            self.style.SUCCESS(
                "Fuzzy match: "
                f"off_rows={name_stats['off_rows']} considered={name_stats['considered']} "
                f"matched={name_stats['matched']} created={name_stats['created']} "
                f"no_candidate={name_stats['no_candidate']} "
                f"below_threshold={name_stats['below_threshold']}"
            )
        )
