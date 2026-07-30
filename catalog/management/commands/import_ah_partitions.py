from django.core.management.base import BaseCommand

from catalog.models import ImportRun, Product, Supermarket
from catalog.services.ah_api import AHAPIError, AHAPIImporter


class Command(BaseCommand):
    help = "Import Albert Heijn catalog slices via API query partitions."

    def add_arguments(self, parser):
        parser.add_argument("--query", action="append")
        parser.add_argument("--single-chars", action="store_true")
        parser.add_argument("--sort-on", default="RELEVANCE")
        parser.add_argument("--max-pages-per-partition", type=int, default=25)
        parser.add_argument("--page-size", type=int, default=100)

    def handle(self, *args, **options):
        supermarket = Supermarket.objects.get(slug="albert-heijn")
        importer = AHAPIImporter(supermarket=supermarket)
        before = Product.objects.filter(supermarket=supermarket).count()

        partitions = options["query"] or []
        if options["single_chars"]:
            partitions.extend(importer.default_single_char_partitions())
        partitions = list(dict.fromkeys(partitions))
        if not partitions:
            self.stderr.write(self.style.ERROR("Provide --query values or use --single-chars."))
            importer.close()
            return

        try:
            result = importer.import_query_partitions(
                partitions=partitions,
                max_pages_per_partition=options["max_pages_per_partition"],
                page_size=options["page_size"],
                sort_on=options["sort_on"],
            )
        except AHAPIError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return
        finally:
            importer.close()

        after = Product.objects.filter(supermarket=supermarket).count()
        for partition_result in result["partitions"]:
            self.stdout.write(
                f"{partition_result['query']}\tpages={partition_result['visited_pages']}\timported={partition_result['imported_products']}"
            )
        self.stdout.write(
            self.style.SUCCESS(
                (
                    f"Completed {len(result['partitions'])} partitions with sort {options['sort_on']}. "
                    f"Visited {result['total_pages']} pages, imported {result['total_imported']} rows, "
                    f"and increased stored AH products by {after - before} to {after}."
                )
            )
        )
        for partition_result in result["partitions"]:
            ImportRun.objects.create(
                supermarket=supermarket,
                importer="ah_api",
                mode="partition_import",
                query=partition_result["query"],
                sort_on=options["sort_on"],
                start_page=0,
                pages_visited=partition_result["visited_pages"],
                rows_imported=partition_result["imported_products"],
                unique_products_added=0,
                status="completed",
            )
