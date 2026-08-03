from django.core.management.base import BaseCommand, CommandError

from catalog.services.openfoodfacts import (
    OpenFoodFactsError,
    OpenFoodFactsIngestor,
    iter_jsonl,
)


class Command(BaseCommand):
    help = (
        "Stage OpenFoodFacts products from a JSONL dump into OpenFoodFactsProduct. "
        "Records are keyed by barcode and are not linked to local products here; "
        "matching is a separate step. Use the published dumps rather than the API "
        "for bulk loads."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dump",
            required=True,
            help="Path to an OFF JSONL dump (.jsonl or .jsonl.gz).",
        )
        parser.add_argument(
            "--country-tag",
            default="en:netherlands",
            help=(
                "Only stage products carrying this countries_tag. "
                "Pass an empty string to stage everything."
            ),
        )
        parser.add_argument("--limit", type=int, default=0, help="Stop after N input records. 0 means no limit.")
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument(
            "--store-raw",
            action="store_true",
            help="Also persist the full OFF payload per record (much larger rows).",
        )
        parser.add_argument("--progress-every", type=int, default=20000)

    def handle(self, *args, **options):
        limit = options["limit"]
        progress_every = max(1, options["progress_every"])

        def source_records():
            for index, payload in enumerate(iter_jsonl(options["dump"]), start=1):
                if limit and index > limit:
                    return
                if index % progress_every == 0:
                    self.stdout.write(f"  read {index} records...")
                yield payload

        ingestor = OpenFoodFactsIngestor(store_raw=options["store_raw"])
        try:
            stats = ingestor.ingest(
                source_records(),
                country_tag=options["country_tag"],
                batch_size=options["batch_size"],
            )
        except OpenFoodFactsError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                "OpenFoodFacts staging complete: "
                f"seen={stats['seen']} created={stats['created']} "
                f"updated={stats['updated']} unchanged={stats['unchanged']} "
                f"skipped={stats['skipped']}"
            )
        )
