from django.db import migrations


def seed_environment_goals(apps, schema_editor):
    Goal = apps.get_model("catalog", "Goal")
    goals = [
        (
            "Sustainable Eating",
            "sustainable-eating",
            "Prefer meals with lower environmental burden, more plant-forward choices, and less waste.",
        ),
        (
            "Lower Environmental Impact",
            "lower-environmental-impact",
            "Prefer ingredients and meal patterns that reduce estimated climate and resource impact.",
        ),
        (
            "Reduce Food Waste",
            "reduce-food-waste",
            "Prefer recipes that use what is already available and reduce spoilage or leftovers going unused.",
        ),
        (
            "Seasonal Eating",
            "seasonal-eating",
            "Prefer meals built around simpler, seasonal, and less resource-intensive ingredients when practical.",
        ),
    ]
    for name, slug, description in goals:
        Goal.objects.get_or_create(
            slug=slug,
            defaults={"name": name, "description": description},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0011_ingredientimageanalysis"),
    ]

    operations = [
        migrations.RunPython(seed_environment_goals, migrations.RunPython.noop),
    ]
