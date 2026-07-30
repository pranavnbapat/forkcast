from django.db import migrations, models
import django.db.models.deletion


def seed_additional_options(apps, schema_editor):
    Goal = apps.get_model("catalog", "Goal")
    CultureOption = apps.get_model("catalog", "CultureOption")
    CuisineOption = apps.get_model("catalog", "CuisineOption")

    Goal.objects.get_or_create(
        slug="eat-more-protein",
        defaults={
            "name": "Eat More Protein",
            "description": "Prioritize meals with higher protein density for satiety, recovery, and body-composition support.",
        },
    )

    cultures = [
        ("Dutch", "dutch", "Europe"),
        ("Belgian", "belgian", "Europe"),
        ("French", "french", "Europe"),
        ("Italian", "italian", "Europe"),
        ("Spanish", "spanish", "Europe"),
        ("Portuguese", "portuguese", "Europe"),
        ("Greek", "greek", "Europe"),
        ("Turkish", "turkish", "Europe / West Asia"),
        ("Indian", "indian", "South Asia"),
        ("Pakistani", "pakistani", "South Asia"),
        ("Bangladeshi", "bangladeshi", "South Asia"),
        ("Sri Lankan", "sri-lankan", "South Asia"),
        ("Chinese", "chinese", "East Asia"),
        ("Japanese", "japanese", "East Asia"),
        ("Korean", "korean", "East Asia"),
        ("Thai", "thai", "Southeast Asia"),
        ("Vietnamese", "vietnamese", "Southeast Asia"),
        ("Indonesian", "indonesian", "Southeast Asia"),
        ("Filipino", "filipino", "Southeast Asia"),
        ("Middle Eastern", "middle-eastern", "Middle East"),
        ("Lebanese", "lebanese", "Middle East"),
        ("Persian", "persian", "Middle East"),
        ("Ethiopian", "ethiopian", "Africa"),
        ("Nigerian", "nigerian", "Africa"),
        ("Ghanaian", "ghanaian", "Africa"),
        ("Moroccan", "moroccan", "North Africa"),
        ("Mexican", "mexican", "Latin America"),
        ("Brazilian", "brazilian", "Latin America"),
        ("Caribbean", "caribbean", "Caribbean"),
        ("American", "american", "North America"),
    ]
    cuisines = [
        ("Soups and Stews", "soups-stews", "Universal"),
        ("Curries", "curries", "South Asia"),
        ("Stir-Fry", "stir-fry", "East Asia"),
        ("Rice Bowls", "rice-bowls", "Universal"),
        ("Noodle Dishes", "noodle-dishes", "Asia"),
        ("Salads", "salads", "Universal"),
        ("Wraps and Rolls", "wraps-rolls", "Universal"),
        ("Sandwiches", "sandwiches", "Universal"),
        ("One-Pot Meals", "one-pot-meals", "Universal"),
        ("Traybakes", "traybakes", "Europe"),
        ("Pasta", "pasta", "Europe"),
        ("Risotto", "risotto", "Europe"),
        ("Casseroles", "casseroles", "Europe"),
        ("Grills and Roasts", "grills-roasts", "Universal"),
        ("Street Food Style", "street-food-style", "Universal"),
        ("Breakfast Meals", "breakfast-meals", "Universal"),
        ("Meal Prep Bowls", "meal-prep-bowls", "Modern"),
        ("High-Protein Meals", "high-protein-meals", "Fitness"),
        ("Budget Meals", "budget-meals", "Universal"),
        ("Comfort Food", "comfort-food", "Universal"),
        ("Dutch Home Cooking", "dutch-home-cooking", "Europe"),
        ("Indian Home Cooking", "indian-home-cooking", "South Asia"),
        ("Chinese Home Cooking", "chinese-home-cooking", "East Asia"),
        ("Mediterranean", "mediterranean", "Mediterranean"),
        ("West African", "west-african", "Africa"),
    ]

    for name, slug, region in cultures:
        CultureOption.objects.get_or_create(slug=slug, defaults={"name": name, "region": region})
    for name, slug, region in cuisines:
        CuisineOption.objects.get_or_create(slug=slug, defaults={"name": name, "region": region})


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0008_planner_models_and_goals"),
    ]

    operations = [
        migrations.CreateModel(
            name="CultureOption",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=120, unique=True)),
                ("slug", models.SlugField(max_length=60, unique=True)),
                ("region", models.CharField(blank=True, max_length=120)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="CuisineOption",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=120, unique=True)),
                ("slug", models.SlugField(max_length=60, unique=True)),
                ("region", models.CharField(blank=True, max_length=120)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.AddField(
            model_name="plannerprofile",
            name="culture_option",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="profiles", to="catalog.cultureoption"),
        ),
        migrations.AddField(
            model_name="plannerprofile",
            name="cuisine_option",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="profiles", to="catalog.cuisineoption"),
        ),
        migrations.AddField(
            model_name="plannerprofile",
            name="height_cm",
            field=models.DecimalField(blank=True, decimal_places=1, max_digits=5, null=True),
        ),
        migrations.AddField(
            model_name="plannerprofile",
            name="weight_kg",
            field=models.DecimalField(blank=True, decimal_places=1, max_digits=5, null=True),
        ),
        migrations.RunPython(seed_additional_options, migrations.RunPython.noop),
    ]
