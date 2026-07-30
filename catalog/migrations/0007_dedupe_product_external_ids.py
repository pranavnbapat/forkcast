from django.db import migrations, models


def dedupe_products(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    NutritionFacts = apps.get_model("catalog", "NutritionFacts")
    ProductSnapshot = apps.get_model("catalog", "ProductSnapshot")

    duplicate_external_ids = (
        Product.objects.values("supermarket_id", "external_id")
        .annotate(count=models.Count("id"))
        .filter(count__gt=1)
    )

    for group in duplicate_external_ids:
        products = list(
            Product.objects.filter(
                supermarket_id=group["supermarket_id"],
                external_id=group["external_id"],
            ).order_by("id")
        )
        if len(products) < 2:
            continue

        def score(product):
            nutrition = NutritionFacts.objects.filter(product_id=product.id).first()
            entry_count = nutrition.entries.count() if nutrition else 0
            field_score = sum(
                1
                for value in [
                    bool(product.name and not product.name.startswith("wi")),
                    bool(product.brand),
                    bool(product.package_size),
                    bool(product.description),
                    bool(product.image_url),
                    bool(product.nutri_score_grade),
                ]
                if value
            )
            snapshot_count = ProductSnapshot.objects.filter(product_id=product.id).count()
            return (entry_count, field_score, snapshot_count, bool(product.last_scraped_at), -product.id)

        canonical = max(products, key=score)
        for duplicate in products:
            if duplicate.id == canonical.id:
                continue

            duplicate_nutrition = NutritionFacts.objects.filter(product_id=duplicate.id).first()
            canonical_nutrition = NutritionFacts.objects.filter(product_id=canonical.id).first()
            if duplicate_nutrition:
                duplicate_entries = duplicate_nutrition.entries.count()
                canonical_entries = canonical_nutrition.entries.count() if canonical_nutrition else 0
                if canonical_nutrition is None:
                    duplicate_nutrition.product_id = canonical.id
                    duplicate_nutrition.save(update_fields=["product"])
                    canonical_nutrition = duplicate_nutrition
                elif duplicate_entries > canonical_entries:
                    canonical_nutrition.entries.all().delete()
                    canonical_nutrition.delete()
                    duplicate_nutrition.product_id = canonical.id
                    duplicate_nutrition.save(update_fields=["product"])
                    canonical_nutrition = duplicate_nutrition

            ProductSnapshot.objects.filter(product_id=duplicate.id).update(product_id=canonical.id)

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


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0006_importrun"),
    ]

    operations = [
        migrations.RunPython(dedupe_products, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="product",
            constraint=models.UniqueConstraint(
                fields=("supermarket", "external_id"),
                name="unique_product_external_id_per_supermarket",
            ),
        ),
    ]
