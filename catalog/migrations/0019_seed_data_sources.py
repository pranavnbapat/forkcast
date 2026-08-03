from django.db import migrations


# trust_rank: lower wins when two sources describe the same field.
DATA_SOURCES = [
    {
        "slug": "albert-heijn",
        "name": "Albert Heijn",
        "kind": "retailer",
        "base_url": "https://www.ah.nl",
        "license_name": "",
        "license_url": "",
        "attribution_required": False,
        "attribution_text": "",
        "trust_rank": 10,
        "notes": (
            "Authoritative for price, Bonus promotion mechanics, and NL assortment. "
            "Nutrition comes from the manufacturer declaration exposed by the mobile API. "
            "Proprietary data accessed through an undocumented API; not redistributable."
        ),
    },
    {
        "slug": "openfoodfacts",
        "name": "OpenFoodFacts",
        "kind": "reference_db",
        "base_url": "https://world.openfoodfacts.org",
        "license_name": "Open Database License (ODbL) v1.0",
        "license_url": "https://opendatacommons.org/licenses/odbl/1-0/",
        "attribution_required": True,
        "attribution_text": (
            "Contains information from OpenFoodFacts, made available under the Open Database "
            "License (ODbL) v1.0. Product images are licensed separately under CC-BY-SA."
        ),
        "trust_rank": 20,
        "notes": (
            "Crowd-sourced reference database keyed by GTIN/EAN barcode. Rich on ingredients, "
            "additives, and labels; carries no price or promotion data. Coverage and accuracy "
            "vary by product. Bulk loads must use the published dumps/deltas, not the API."
        ),
    },
    {
        "slug": "vllm-default",
        "name": "vLLM (default model)",
        "kind": "llm",
        "base_url": "",
        "license_name": "",
        "license_url": "",
        "attribution_required": False,
        "attribution_text": "",
        "trust_rank": 90,
        "notes": (
            "Self-hosted vLLM endpoint used for shelf-life, spoilage, and sensor-target "
            "estimation. Estimated output, not measured; least trusted of the sources."
        ),
    },
]


def seed_data_sources(apps, schema_editor):
    DataSource = apps.get_model("catalog", "DataSource")
    Supermarket = apps.get_model("catalog", "Supermarket")

    for entry in DATA_SOURCES:
        DataSource.objects.get_or_create(slug=entry["slug"], defaults=entry)

    ah_source = DataSource.objects.filter(slug="albert-heijn").first()
    if ah_source is not None:
        Supermarket.objects.filter(slug="albert-heijn", data_source__isnull=True).update(
            data_source=ah_source
        )


def unseed_data_sources(apps, schema_editor):
    DataSource = apps.get_model("catalog", "DataSource")
    Supermarket = apps.get_model("catalog", "Supermarket")

    Supermarket.objects.filter(data_source__slug__in=[e["slug"] for e in DATA_SOURCES]).update(
        data_source=None
    )
    DataSource.objects.filter(slug__in=[e["slug"] for e in DATA_SOURCES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0018_datasource_supermarket_data_source_productidentifier"),
    ]

    operations = [
        migrations.RunPython(seed_data_sources, unseed_data_sources),
    ]
