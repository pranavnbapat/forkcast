from django.db import migrations, models
import django.db.models.deletion


def seed_goals(apps, schema_editor):
    Goal = apps.get_model("catalog", "Goal")
    goals = [
        ("Lose Weight", "lose-weight", "Prioritize lower calorie density, satiety, and better food choices for fat loss."),
        ("Maintain Weight", "maintain-weight", "Prioritize balanced meals and sustainable intake."),
        ("Gain Muscle", "gain-muscle", "Prioritize protein adequacy, energy sufficiency, and recovery-friendly meals."),
        ("Save Money", "save-money", "Prefer cheaper ingredients, bonus items, and lower-cost substitutes."),
        ("Improve Energy", "improve-energy", "Prefer steady meals with better fibre, protein, and micronutrient density."),
        ("Eat More Plants", "eat-more-plants", "Prefer plant-forward meals with legumes, vegetables, fruits, and whole foods."),
    ]
    for name, slug, description in goals:
        Goal.objects.get_or_create(
            slug=slug,
            defaults={"name": name, "description": description},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0007_dedupe_product_external_ids"),
    ]

    operations = [
        migrations.CreateModel(
            name="Goal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=120, unique=True)),
                ("slug", models.SlugField(max_length=50, unique=True)),
                ("description", models.TextField(blank=True)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="PlannerProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(default="Default Planner Profile", max_length=120, unique=True)),
                ("gender", models.CharField(blank=True, choices=[("female", "Female"), ("male", "Male"), ("non_binary", "Non-binary"), ("other", "Other"), ("prefer_not_to_say", "Prefer not to say")], max_length=30)),
                ("age", models.PositiveIntegerField(blank=True, null=True)),
                ("culture", models.CharField(blank=True, max_length=120)),
                ("lifestyle", models.CharField(blank=True, choices=[("sedentary", "Sedentary"), ("lightly_active", "Lightly active"), ("active", "Active"), ("very_active", "Very active")], max_length=30)),
                ("fasting_pattern", models.CharField(blank=True, max_length=120)),
                ("diet_style", models.CharField(blank=True, choices=[("omnivore", "Omnivore"), ("vegetarian", "Vegetarian"), ("vegan", "Vegan"), ("pescatarian", "Pescatarian"), ("poultry", "Poultry-focused"), ("meat_based", "Meat-based")], max_length=30)),
                ("allergies", models.TextField(blank=True)),
                ("notes", models.TextField(blank=True)),
                ("primary_goal", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="primary_profiles", to="catalog.goal")),
                ("secondary_goal", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="secondary_profiles", to="catalog.goal")),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="IngredientPlan",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(default="Default Ingredient Plan", max_length=120, unique=True)),
                ("horizon", models.CharField(choices=[("tomorrow", "Tomorrow"), ("few_days", "Coming days"), ("week", "Week"), ("month", "Month")], default="week", max_length=20)),
                ("notes", models.TextField(blank=True)),
                ("last_generated_at", models.DateTimeField(blank=True, null=True)),
                ("profile", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="ingredient_plans", to="catalog.plannerprofile")),
            ],
            options={"ordering": ["-updated_at"]},
        ),
        migrations.CreateModel(
            name="RecipeSuggestionRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("model_name", models.CharField(blank=True, max_length=120)),
                ("status", models.CharField(default="completed", max_length=20)),
                ("prompt_text", models.TextField(blank=True)),
                ("response_text", models.TextField(blank=True)),
                ("response_json", models.JSONField(blank=True, default=dict)),
                ("error_text", models.TextField(blank=True)),
                ("plan", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="recipe_runs", to="catalog.ingredientplan")),
                ("profile", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="recipe_runs", to="catalog.plannerprofile")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="IngredientPlanItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("quantity", models.DecimalField(decimal_places=2, default=1, max_digits=8)),
                ("unit", models.CharField(default="unit", max_length=30)),
                ("is_pantry_staple", models.BooleanField(default=False)),
                ("notes", models.CharField(blank=True, max_length=255)),
                ("plan", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="catalog.ingredientplan")),
                ("product", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ingredient_plan_items", to="catalog.product")),
            ],
            options={"ordering": ["product__name", "id"]},
        ),
        migrations.AddConstraint(
            model_name="ingredientplanitem",
            constraint=models.UniqueConstraint(fields=("plan", "product"), name="unique_product_per_ingredient_plan"),
        ),
        migrations.RunPython(seed_goals, migrations.RunPython.noop),
    ]
