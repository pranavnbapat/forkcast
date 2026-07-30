from django.core.management.base import BaseCommand

from catalog.models import CrawlSource, ImportRun, Product, Supermarket
from catalog.services.ah_api import AHAPIError, AHAPIImporter


class Command(BaseCommand):
    help = "Discover Albert Heijn product URLs from crawl source pages."

    def add_arguments(self, parser):
        parser.add_argument("--max-pages", type=int, default=100)
        parser.add_argument("--source-id", type=int, action="append")
        parser.add_argument("--query", default="")
        parser.add_argument("--start-page", type=int, default=0)
        parser.add_argument("--sort-on", default="RELEVANCE")

    def handle(self, *args, **options):
        supermarket = Supermarket.objects.get(slug="albert-heijn")
        source_ids = options["source_id"] or []
        sources = CrawlSource.objects.filter(supermarket=supermarket, is_active=True, id__in=source_ids)

        importer = AHAPIImporter(supermarket=supermarket)
        before = Product.objects.filter(supermarket=supermarket).count()
        try:
            if source_ids and sources.exists():
                result = importer.discover_from_sources(
                    sources=sources,
                    max_pages=options["max_pages"],
                )
                after = Product.objects.filter(supermarket=supermarket).count()
                ImportRun.objects.create(
                    supermarket=supermarket,
                    importer="ah_api",
                    mode="source_discovery",
                    query=",".join(str(source_id) for source_id in source_ids),
                    sort_on="",
                    start_page=0,
                    pages_visited=result.visited_pages,
                    rows_imported=result.discovered_products,
                    unique_products_added=max(0, after - before),
                    status="completed",
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        (
                            f"Visited {result.visited_pages} pages, discovered "
                            f"{result.discovered_products} products, {result.discovered_sources} new sources, "
                            f"and {result.failed_pages} blocked/failed pages."
                        )
                    )
                )
                return

            result = importer.import_search_pages(
                query=options["query"],
                start_page=options["start_page"],
                max_pages=options["max_pages"],
                page_size=100,
                sort_on=options["sort_on"],
            )
            after = Product.objects.filter(supermarket=supermarket).count()
            ImportRun.objects.create(
                supermarket=supermarket,
                importer="ah_api",
                mode="search_import",
                query=options["query"],
                sort_on=options["sort_on"],
                start_page=options["start_page"],
                pages_visited=result["visited_pages"],
                rows_imported=result["imported_products"],
                unique_products_added=max(0, after - before),
                status="completed",
            )
            self.stdout.write(
                self.style.SUCCESS(
                    (
                        f"Visited {result['visited_pages']} search pages with sort "
                        f"{options['sort_on']} and imported {result['imported_products']} products."
                    )
                )
            )
        except AHAPIError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return
        finally:
            importer.close()
