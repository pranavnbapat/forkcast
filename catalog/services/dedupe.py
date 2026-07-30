from __future__ import annotations

from django.db import transaction
from django.db.models import Count

from catalog.models import NutritionFacts, Product, ProductSnapshot, Supermarket


def product_completeness_score(product: Product) -> tuple[int, int, int, int, int]:
    nutrition = getattr(product, "nutrition_facts", None)
    entry_count = nutrition.entries.count() if nutrition else 0
    field_score = sum(
        1
        for value in [
            product.name and not product.name.startswith("wi"),
            product.brand,
            product.package_size,
            product.description,
            product.image_url,
            product.nutri_score_grade,
        ]
        if value
    )
    snapshot_count = product.snapshots.count()
    return (entry_count, field_score, snapshot_count, int(bool(product.last_scraped_at)), -product.id)


@transaction.atomic
def dedupe_products_by_external_id(supermarket: Supermarket) -> int:
    duplicate_groups = (
        Product.objects.filter(supermarket=supermarket)
        .values("external_id")
        .annotate(count=Count("id"))
        .filter(count__gt=1)
    )
    deleted_count = 0

    for group in duplicate_groups:
        external_id = group["external_id"]
        products = list(
            Product.objects.filter(supermarket=supermarket, external_id=external_id)
            .select_related("nutrition_facts")
            .prefetch_related("nutrition_facts__entries", "snapshots")
        )
        canonical = max(products, key=product_completeness_score)

        for duplicate in products:
            if duplicate.id == canonical.id:
                continue

            duplicate_nutrition = getattr(duplicate, "nutrition_facts", None)
            canonical_nutrition = getattr(canonical, "nutrition_facts", None)
            if duplicate_nutrition:
                duplicate_entries = duplicate_nutrition.entries.count()
                canonical_entries = canonical_nutrition.entries.count() if canonical_nutrition else 0
                if canonical_nutrition is None:
                    duplicate_nutrition.product = canonical
                    duplicate_nutrition.save(update_fields=["product"])
                    canonical_nutrition = duplicate_nutrition
                elif duplicate_entries > canonical_entries:
                    canonical_nutrition.entries.all().delete()
                    canonical_nutrition.delete()
                    duplicate_nutrition.product = canonical
                    duplicate_nutrition.save(update_fields=["product"])
                    canonical_nutrition = duplicate_nutrition

            ProductSnapshot.objects.filter(product=duplicate).update(product=canonical)

            if canonical.name.startswith("wi") and duplicate.name and not duplicate.name.startswith("wi"):
                canonical.name = duplicate.name
            if not canonical.brand and duplicate.brand:
                canonical.brand = duplicate.brand
            if not canonical.package_size and duplicate.package_size:
                canonical.package_size = duplicate.package_size
            if not canonical.description and duplicate.description:
                canonical.description = duplicate.description
            if not canonical.ingredients and duplicate.ingredients:
                canonical.ingredients = duplicate.ingredients
            if not canonical.allergen_info and duplicate.allergen_info:
                canonical.allergen_info = duplicate.allergen_info
            if not canonical.image_url and duplicate.image_url:
                canonical.image_url = duplicate.image_url
            if not canonical.nutri_score_grade and duplicate.nutri_score_grade:
                canonical.nutri_score_grade = duplicate.nutri_score_grade
            if not canonical.nutri_score_label and duplicate.nutri_score_label:
                canonical.nutri_score_label = duplicate.nutri_score_label
            if not canonical.last_scraped_at and duplicate.last_scraped_at:
                canonical.last_scraped_at = duplicate.last_scraped_at
            canonical.save()

            duplicate.delete()
            deleted_count += 1

    return deleted_count
