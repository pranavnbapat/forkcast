from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0010_nutritionfacts_diet_metrics"),
    ]

    operations = [
        migrations.CreateModel(
            name="IngredientImageAnalysis",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("image", models.FileField(upload_to="planner_uploads/%Y/%m/%d")),
                ("model_name", models.CharField(blank=True, max_length=120)),
                ("status", models.CharField(default="completed", max_length=20)),
                ("prompt_text", models.TextField(blank=True)),
                ("response_text", models.TextField(blank=True)),
                ("response_json", models.JSONField(blank=True, default=dict)),
                ("extracted_items", models.JSONField(blank=True, default=list)),
                ("error_text", models.TextField(blank=True)),
                (
                    "plan",
                    models.ForeignKey(on_delete=models.deletion.CASCADE, related_name="image_analyses", to="catalog.ingredientplan"),
                ),
                (
                    "profile",
                    models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name="image_analyses", to="catalog.plannerprofile"),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
