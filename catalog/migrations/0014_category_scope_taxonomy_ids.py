from django.db import migrations, models


TAXONOMY_BY_SLUG = {
    "pasen": 21024,
    "groente-aardappelen": 6401,
    "fruit-verse-sappen": 20885,
    "bakkerij": 1355,
    "zuivel-eieren": 1730,
    "vlees": 1745,
    "vis": 1757,
    "vegetarisch-vegan-plantaardig": 4614,
    "maaltijden-salades": 1814,
    "kaas": 1897,
    "vleeswaren": 1735,
    "diepvries": 1826,
    "borrel-chips-snacks": 706,
    "koek-snoep-chocolade": 684,
    "koffie-thee": 1734,
    "frisdrank-sappen-water": 1719,
    "bier-wijn-aperitieven": 1190,
    "ontbijtgranen-beleg": 618,
    "pasta-rijst-wereldkeuken": 626,
    "soepen-sauzen-kruiden-olie": 1692,
    "tussendoortjes": 1704,
    "glutenvrij": 20883,
    "koken-tafelen-vrije-tijd": 1057,
    "baby-en-kind": 18521,
}


def populate_taxonomy_ids(apps, schema_editor):
    CategoryScope = apps.get_model("catalog", "CategoryScope")
    for scope in CategoryScope.objects.all():
        taxonomy_id = TAXONOMY_BY_SLUG.get(scope.slug)
        if taxonomy_id is None:
            continue
        scope.taxonomy_id = taxonomy_id
        scope.save(update_fields=["taxonomy_id", "updated_at"])


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0013_category_scope_food_only"),
    ]

    operations = [
        migrations.AddField(
            model_name="categoryscope",
            name="taxonomy_id",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.RunPython(populate_taxonomy_ids, migrations.RunPython.noop),
    ]
