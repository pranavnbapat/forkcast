from django.db import migrations


BACKFILL_NOTE = "primary=albert-heijn; contributors=albert-heijn (backfilled)"


def backfill_nutrition_provenance(apps, schema_editor):
    """Attribute pre-existing nutrition rows to Albert Heijn.

    Every NutritionFacts row that existed before multi-source support was added
    came from the AH importer, so recording that is accurate rather than a
    guess. `resolved_at` is deliberately left NULL: the source is being
    asserted, but no resolution pass has actually run over these rows.
    """
    DataSource = apps.get_model("catalog", "DataSource")
    NutritionFacts = apps.get_model("catalog", "NutritionFacts")

    ah_source = DataSource.objects.filter(slug="albert-heijn").first()
    if ah_source is None:
        return

    NutritionFacts.objects.filter(resolved_from_source__isnull=True).update(
        resolved_from_source=ah_source,
        resolution_note=BACKFILL_NOTE,
    )


def unbackfill_nutrition_provenance(apps, schema_editor):
    NutritionFacts = apps.get_model("catalog", "NutritionFacts")
    NutritionFacts.objects.filter(resolution_note=BACKFILL_NOTE).update(
        resolved_from_source=None,
        resolution_note="",
    )


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0021_nutritionfacts_resolution_note_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill_nutrition_provenance, unbackfill_nutrition_provenance),
    ]
