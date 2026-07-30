from django.core.management.base import BaseCommand

from catalog.models import CategoryScope, ImportRun, Product, Supermarket
from catalog.services.ah_api import AHAPIError, AHAPIImporter


FALLBACK_QUERIES_BY_SLUG = {
    "bakkerij": ["brood", "croissant", "bakkerij"],
    "bier-wijn-aperitieven": ["bier", "wijn", "aperitief"],
    "borrel-chips-snacks": ["chips", "noten", "snack"],
    "diepvries": ["diepvries", "pizza", "ijs"],
    "frisdrank-sappen-water": ["frisdrank", "sap", "water"],
    "fruit-verse-sappen": ["fruit", "appel", "banaan"],
    "glutenvrij": ["glutenvrij"],
    "groente-aardappelen": ["groente", "aardappel", "sla"],
    "kaas": ["kaas"],
    "koek-snoep-chocolade": ["koek", "snoep", "chocolade"],
    "koffie-thee": ["koffie", "thee"],
    "maaltijden-salades": ["maaltijd", "salade", "pizza"],
    "ontbijtgranen-beleg": ["muesli", "cornflakes", "beleg"],
    "pasen": ["pasen", "paasbrood", "paasei"],
    "pasta-rijst-wereldkeuken": ["pasta", "rijst", "noodles"],
    "soepen-sauzen-kruiden-olie": ["soep", "saus", "olie", "kruiden"],
    "tussendoortjes": ["reep", "cracker", "tussendoortje"],
    "vegetarisch-vegan-plantaardig": ["vegetarisch", "vegan", "tofu"],
    "vis": ["vis", "zalm", "tonijn"],
    "vlees": ["vlees", "kip", "gehakt"],
    "vleeswaren": ["vleeswaren", "ham", "salami"],
    "zuivel-eieren": ["melk", "yoghurt", "eieren"],
}


class Command(BaseCommand):
    help = "Import AH products category by category using the DB-backed food category scope."

    def add_arguments(self, parser):
        parser.add_argument("--max-pages-per-category", type=int, default=50)
        parser.add_argument("--page-size", type=int, default=100)
        parser.add_argument("--sort-on", default="RELEVANCE")
        parser.add_argument("--category-slug", action="append")
        parser.add_argument("--disable-fallback-queries", action="store_true")

    def handle(self, *args, **options):
        supermarket = Supermarket.objects.get(slug="albert-heijn")
        importer = AHAPIImporter(supermarket=supermarket)
        category_slugs = options["category_slug"] or []
        scopes = CategoryScope.objects.filter(is_food=True, is_active=True).order_by("name")
        if category_slugs:
            scopes = scopes.filter(slug__in=category_slugs)

        total_rows = 0
        total_pages = 0
        before = Product.objects.filter(supermarket=supermarket).count()
        try:
            for scope in scopes:
                if not scope.taxonomy_id:
                    self.stdout.write(self.style.WARNING(f"{scope.name}: no taxonomy_id stored, using fallback queries only."))
                    result = {"imported_products": 0, "visited_pages": 0}
                else:
                    result = importer.import_search_pages(
                        query="",
                        start_page=0,
                        max_pages=options["max_pages_per_category"],
                        page_size=options["page_size"],
                        sort_on=options["sort_on"],
                        taxonomy_id=scope.taxonomy_id,
                        target_category_name=scope.name,
                    )

                fallback_rows = 0
                fallback_pages = 0
                fallback_queries = []
                if not options["disable_fallback_queries"] and (
                    result["imported_products"] == 0 or scope.slug in FALLBACK_QUERIES_BY_SLUG
                ):
                    for query in FALLBACK_QUERIES_BY_SLUG.get(scope.slug, []):
                        fallback_queries.append(query)
                        fallback_result = importer.import_search_pages(
                            query=query,
                            start_page=0,
                            max_pages=min(10, options["max_pages_per_category"]),
                            page_size=options["page_size"],
                            sort_on=options["sort_on"],
                            target_category_name=scope.name,
                        )
                        fallback_rows += fallback_result["imported_products"]
                        fallback_pages += fallback_result["visited_pages"]

                category_rows = result["imported_products"] + fallback_rows
                category_pages = result["visited_pages"] + fallback_pages
                total_rows += category_rows
                total_pages += category_pages
                ImportRun.objects.create(
                    supermarket=supermarket,
                    importer="ah_api",
                    mode="category_scope_import",
                    query=scope.slug,
                    sort_on=options["sort_on"],
                    start_page=0,
                    pages_visited=category_pages,
                    rows_imported=category_rows,
                    unique_products_added=0,
                    status="completed",
                    notes=(
                        f"taxonomy_id={scope.taxonomy_id}; category_name={scope.name}; "
                        f"fallback_queries={','.join(fallback_queries) if fallback_queries else '-'}"
                    ),
                )
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{scope.name}: visited {category_pages} pages and imported {category_rows} products."
                    )
                )
        except AHAPIError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return
        finally:
            importer.close()

        after = Product.objects.filter(supermarket=supermarket).count()
        ImportRun.objects.create(
            supermarket=supermarket,
            importer="ah_api",
            mode="category_scope_import",
            query="all_food_categories" if not category_slugs else ",".join(category_slugs),
            sort_on=options["sort_on"],
            start_page=0,
            pages_visited=total_pages,
            rows_imported=total_rows,
            unique_products_added=max(0, after - before),
            status="completed",
            notes=f"max_pages_per_category={options['max_pages_per_category']}; page_size={options['page_size']}",
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Completed category scope import: visited {total_pages} pages and imported {total_rows} products."
            )
        )
