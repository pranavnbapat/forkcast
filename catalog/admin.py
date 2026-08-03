from django.contrib import admin
from django.contrib import messages

from .models import (
    CategoryScope,
    CultureOption,
    CuisineOption,
    CrawlSource,
    DataSource,
    Goal,
    IngredientImageAnalysis,
    ImportRun,
    IngredientPlan,
    IngredientPlanItem,
    NutritionEntry,
    NutritionFacts,
    OpenFoodFactsProduct,
    PlannerProfile,
    Product,
    ProductIdentifier,
    ProductQualityProfile,
    ProductSnapshot,
    RecipeSuggestionRun,
    Supermarket,
)
from .services.ah_api import AHAPIError, AHAPIImporter
from .services.product_filters import is_food_candidate
from .services.product_quality import ProductQualityEnricher, ProductQualityError


class NutritionFactsInline(admin.StackedInline):
    model = NutritionFacts
    extra = 0


class ProductIdentifierInline(admin.TabularInline):
    model = ProductIdentifier
    extra = 0
    fields = ("source", "id_type", "value", "is_primary", "match_method", "confidence_label")
    autocomplete_fields = ("source",)


class NutritionEntryInline(admin.TabularInline):
    model = NutritionEntry
    extra = 0
    fields = ("position", "label", "value_text", "reference_intake_text")
    ordering = ("position",)


class ProductSnapshotInline(admin.TabularInline):
    model = ProductSnapshot
    extra = 0
    ordering = ["-scraped_at"]
    fields = ("scraped_at", "price_amount", "price_text")
    readonly_fields = ("scraped_at",)


class IngredientPlanItemInline(admin.TabularInline):
    model = IngredientPlanItem
    extra = 0
    autocomplete_fields = ("product",)


def _display_range(min_value, max_value):
    if min_value is None and max_value is None:
        return "-"
    if min_value == max_value:
        return str(min_value)
    if min_value is None:
        return f"<= {max_value}"
    if max_value is None:
        return f">= {min_value}"
    return f"{min_value}-{max_value}"


@admin.action(description="Seed default Albert Heijn crawl sources")
def seed_default_ah_sources(modeladmin, request, queryset):
    total_created = 0
    for supermarket in queryset.filter(slug="albert-heijn"):
        for source_type, name, url in (
            (CrawlSource.SourceType.CATALOG, "AH catalog", "https://www.ah.nl/producten"),
            (CrawlSource.SourceType.BONUS, "AH bonus", "https://www.ah.nl/bonus"),
        ):
            _, created = CrawlSource.objects.get_or_create(
                supermarket=supermarket,
                url=url,
                defaults={"name": name, "source_type": source_type},
            )
            total_created += int(created)
    modeladmin.message_user(
        request,
        f"Created {total_created} AH crawl sources.",
        level=messages.INFO,
    )


@admin.action(description="Scrape selected products from Albert Heijn")
def scrape_selected_products(modeladmin, request, queryset):
    importer = AHAPIImporter()
    scraped = 0
    try:
        for product in queryset.select_related("supermarket"):
            product_id = (
                int(product.external_id.removeprefix("wi"))
                if product.external_id.startswith("wi")
                else importer.parse_product_id_from_url(product.source_url)
            )
            if product_id is None:
                raise AHAPIError(f"Could not determine AH product id for {product.source_url}")
            importer.sync_product_by_id(product_id)
            scraped += 1
    except AHAPIError as exc:
        modeladmin.message_user(request, str(exc), level=messages.ERROR)
        return
    finally:
        importer.close()
    modeladmin.message_user(request, f"Scraped {scraped} products.", level=messages.INFO)


@admin.action(description="Discover AH product URLs from selected crawl sources")
def discover_products_from_sources(modeladmin, request, queryset):
    importer = AHAPIImporter()
    try:
        result = importer.discover_from_sources(queryset.select_related("supermarket"))
    except AHAPIError as exc:
        modeladmin.message_user(request, str(exc), level=messages.ERROR)
        return
    finally:
        importer.close()
    modeladmin.message_user(
        request,
        (
            f"Visited {result.visited_pages} pages, discovered "
            f"{result.discovered_products} products, {result.discovered_sources} new sources, "
            f"and {result.failed_pages} blocked/failed pages."
        ),
        level=messages.INFO,
    )


@admin.action(description="Generate LLM quality profiles for selected products")
def enrich_selected_product_quality(modeladmin, request, queryset):
    products = list(queryset.select_related("supermarket", "nutrition_facts").order_by("id"))
    if not products:
        modeladmin.message_user(request, "No products selected.", level=messages.WARNING)
        return

    enricher = ProductQualityEnricher(source_name="vllm_default")
    completed = 0
    failed_product = None
    try:
        for product in products:
            enricher.enrich_product(product)
            completed += 1
    except ProductQualityError as exc:
        failed_product = product
        supermarket = failed_product.supermarket
        ImportRun.objects.create(
            supermarket=supermarket,
            importer="vllm",
            mode="product_quality_enrichment_admin",
            query="admin_selection",
            sort_on="",
            start_page=0,
            pages_visited=0,
            rows_imported=completed,
            unique_products_added=0,
            status="failed",
            notes=f"selected_count={len(products)}; failed_product_id={failed_product.id}; error={exc}",
        )
        modeladmin.message_user(
            request,
            f"Stopped after {completed} products. Failed on {failed_product.name}: {exc}",
            level=messages.ERROR,
        )
        return

    supermarkets = {product.supermarket_id: product.supermarket for product in products}
    supermarket = next(iter(supermarkets.values()))
    ImportRun.objects.create(
        supermarket=supermarket,
        importer="vllm",
        mode="product_quality_enrichment_admin",
        query="admin_selection",
        sort_on="",
        start_page=0,
        pages_visited=0,
        rows_imported=completed,
        unique_products_added=0,
        status="completed",
        notes=f"selected_count={len(products)}",
    )
    modeladmin.message_user(
        request,
        f"Generated or updated quality profiles for {completed} products.",
        level=messages.INFO,
    )


@admin.register(DataSource)
class DataSourceAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "kind", "trust_rank", "attribution_required", "is_active")
    list_filter = ("kind", "is_active", "attribution_required")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("trust_rank", "name")


@admin.register(ProductIdentifier)
class ProductIdentifierAdmin(admin.ModelAdmin):
    list_display = ("product", "source", "id_type", "value", "is_primary", "match_method", "confidence_label")
    list_filter = ("source", "id_type", "match_method", "is_primary")
    search_fields = ("value", "product__name", "product__external_id")
    autocomplete_fields = ("product", "source")


@admin.register(Supermarket)
class SupermarketAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "data_source", "is_active", "updated_at")
    list_filter = ("is_active", "data_source")
    search_fields = ("name", "slug")
    autocomplete_fields = ("data_source",)
    prepopulated_fields = {"slug": ("name",)}
    actions = (seed_default_ah_sources,)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "supermarket",
        "brand",
        "category_name",
        "food_candidate",
        "nutrition_unavailable",
        "nutri_score_grade",
        "is_active",
        "last_scraped_at",
    )
    list_filter = ("supermarket", "is_active", "category_name", "nutrition_unavailable")
    search_fields = ("name", "brand", "source_url", "external_id", "category_name", "subcategory_name")
    autocomplete_fields = ("supermarket",)
    inlines = (NutritionFactsInline, ProductIdentifierInline, ProductSnapshotInline)
    actions = (scrape_selected_products, enrich_selected_product_quality)

    @admin.display(boolean=True, description="Food Candidate")
    def food_candidate(self, obj):
        return is_food_candidate(obj)


@admin.register(ProductSnapshot)
class ProductSnapshotAdmin(admin.ModelAdmin):
    list_display = ("product", "scraped_at", "price_amount", "price_text")
    list_filter = ("product__supermarket",)
    search_fields = ("product__name", "product__source_url", "price_text")
    autocomplete_fields = ("product",)


@admin.register(ProductQualityProfile)
class ProductQualityProfileAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "source_type",
        "source_name",
        "confidence_label",
        "ambient_days_range",
        "refrigerated_days_range",
        "frozen_days_range",
        "last_generated_at",
    )
    list_filter = ("source_type", "confidence_label", "product__supermarket")
    search_fields = ("product__name", "product__brand", "storage_notes", "spoilage_summary", "odor_notes")
    autocomplete_fields = ("product",)
    readonly_fields = ("raw_response_text", "raw_response_json", "last_generated_at")

    @admin.display(description="Ambient")
    def ambient_days_range(self, obj):
        return _display_range(obj.ambient_days_min, obj.ambient_days_max)

    @admin.display(description="Fridge")
    def refrigerated_days_range(self, obj):
        return _display_range(obj.refrigerated_days_min, obj.refrigerated_days_max)

    @admin.display(description="Frozen")
    def frozen_days_range(self, obj):
        return _display_range(obj.frozen_days_min, obj.frozen_days_max)


@admin.register(NutritionFacts)
class NutritionFactsAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "energy_kcal",
        "estimated_energy_kcal",
        "balanced_score",
        "amount_column_label",
        "reference_intake_column_label",
        "updated_at",
    )
    search_fields = ("product__name", "product__source_url", "declaration_basis")
    autocomplete_fields = ("product",)
    readonly_fields = (
        "energy_kj",
        "energy_kcal",
        "fat_g",
        "saturates_g",
        "unsaturated_g",
        "carbohydrates_g",
        "sugars_g",
        "fiber_g",
        "starch_g",
        "protein_g",
        "salt_g",
        "estimated_energy_kj",
        "estimated_energy_kcal",
        "calorie_score",
        "protein_score",
        "carbohydrates_score",
        "fibre_score",
        "saturated_fats_score",
        "unsaturated_fats_score",
        "balanced_score",
    )
    inlines = (NutritionEntryInline,)


@admin.register(CrawlSource)
class CrawlSourceAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "supermarket",
        "source_type",
        "is_active",
        "last_crawled_at",
    )
    list_filter = ("supermarket", "source_type", "is_active")
    search_fields = ("name", "url")
    autocomplete_fields = ("supermarket",)
    actions = (discover_products_from_sources,)


@admin.register(ImportRun)
class ImportRunAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "supermarket",
        "importer",
        "mode",
        "query",
        "sort_on",
        "pages_visited",
        "rows_imported",
        "unique_products_added",
        "status",
    )
    list_filter = ("supermarket", "importer", "mode", "sort_on", "status")
    search_fields = ("query", "notes")


@admin.register(Goal)
class GoalAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(CultureOption)
class CultureOptionAdmin(admin.ModelAdmin):
    list_display = ("name", "region", "is_active", "updated_at")
    list_filter = ("region", "is_active")
    search_fields = ("name", "slug", "region")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(CuisineOption)
class CuisineOptionAdmin(admin.ModelAdmin):
    list_display = ("name", "region", "is_active", "updated_at")
    list_filter = ("region", "is_active")
    search_fields = ("name", "slug", "region")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(CategoryScope)
class CategoryScopeAdmin(admin.ModelAdmin):
    list_display = ("name", "taxonomy_id", "is_food", "is_active", "source_url", "updated_at")
    list_filter = ("is_food", "is_active")
    search_fields = ("name", "slug", "source_url", "taxonomy_id")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(PlannerProfile)
class PlannerProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "primary_goal", "secondary_goal", "diet_style", "lifestyle", "updated_at")
    list_filter = ("diet_style", "lifestyle", "primary_goal", "secondary_goal", "culture_option", "cuisine_option")
    search_fields = ("name", "user__username", "culture", "allergies", "notes")
    autocomplete_fields = ("primary_goal", "secondary_goal", "culture_option", "cuisine_option")


@admin.register(IngredientPlan)
class IngredientPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "profile", "horizon", "last_generated_at", "updated_at")
    list_filter = ("horizon",)
    search_fields = ("name", "notes")
    autocomplete_fields = ("profile",)
    inlines = (IngredientPlanItemInline,)


@admin.register(RecipeSuggestionRun)
class RecipeSuggestionRunAdmin(admin.ModelAdmin):
    list_display = ("created_at", "plan", "profile", "model_name", "status")
    list_filter = ("status", "model_name")
    search_fields = ("plan__name", "profile__name", "error_text", "response_text")
    autocomplete_fields = ("plan", "profile")


@admin.register(IngredientImageAnalysis)
class IngredientImageAnalysisAdmin(admin.ModelAdmin):
    list_display = ("created_at", "plan", "profile", "model_name", "status")
    list_filter = ("status", "model_name")
    search_fields = ("plan__name", "profile__name", "error_text", "response_text")
    autocomplete_fields = ("plan", "profile")


@admin.register(OpenFoodFactsProduct)
class OpenFoodFactsProductAdmin(admin.ModelAdmin):
    list_display = (
        "barcode",
        "product_name",
        "brands",
        "quantity",
        "nutriscore_grade",
        "energy_kcal",
        "protein_g",
        "completeness",
        "off_last_modified_at",
    )
    list_filter = ("source", "nutriscore_grade", "nova_group")
    search_fields = ("barcode", "product_name", "brands")
    autocomplete_fields = ("source",)
    readonly_fields = ("content_hash", "raw_payload")
