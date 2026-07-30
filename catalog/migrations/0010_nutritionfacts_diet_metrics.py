from decimal import Decimal

from django.db import migrations, models


ZERO = Decimal("0")


def _has_all(*values):
    return all(value is not None for value in values)


def _clamped_non_negative(value):
    return value if value >= ZERO else ZERO


def _derive_metrics(facts):
    estimated_energy_kcal = None
    if _has_all(facts.fat_g, facts.protein_g, facts.carbohydrates_g):
        estimated_energy_kcal = (facts.fat_g * Decimal("9")) + (facts.protein_g * Decimal("4")) + (facts.carbohydrates_g * Decimal("4"))

    estimated_energy_kj = estimated_energy_kcal * Decimal("4.184") if estimated_energy_kcal is not None else None
    unsaturated_g = _clamped_non_negative(facts.fat_g - facts.saturates_g) if _has_all(facts.fat_g, facts.saturates_g) else None
    starch_g = (
        _clamped_non_negative(facts.carbohydrates_g - facts.sugars_g - facts.fiber_g)
        if _has_all(facts.carbohydrates_g, facts.sugars_g, facts.fiber_g)
        else None
    )
    calorie_score = ((Decimal("400") - estimated_energy_kcal) / Decimal("400")) if estimated_energy_kcal is not None else None
    protein_score = (facts.protein_g / Decimal("20")) if facts.protein_g is not None else None
    carbohydrates_score = ((Decimal("50") - facts.carbohydrates_g) / Decimal("50")) if facts.carbohydrates_g is not None else None
    fibre_score = (facts.fiber_g / Decimal("15")) if facts.fiber_g is not None else None
    saturated_fats_score = ((Decimal("10") - facts.saturates_g) / Decimal("10")) if facts.saturates_g is not None else None
    unsaturated_fats_score = (unsaturated_g / Decimal("10")) if unsaturated_g is not None else None
    balanced_score = None
    if _has_all(
        calorie_score,
        protein_score,
        carbohydrates_score,
        fibre_score,
        saturated_fats_score,
        unsaturated_fats_score,
    ):
        balanced_score = (
            (Decimal("-0.3") * calorie_score)
            + (Decimal("0.4") * protein_score)
            + (Decimal("-0.1") * carbohydrates_score)
            + (Decimal("-0.2") * saturated_fats_score)
            + (Decimal("0.1") * unsaturated_fats_score)
            + (Decimal("0.3") * fibre_score)
        )
    return {
        "estimated_energy_kcal": estimated_energy_kcal,
        "estimated_energy_kj": estimated_energy_kj,
        "unsaturated_g": unsaturated_g,
        "starch_g": starch_g,
        "calorie_score": calorie_score,
        "protein_score": protein_score,
        "carbohydrates_score": carbohydrates_score,
        "fibre_score": fibre_score,
        "saturated_fats_score": saturated_fats_score,
        "unsaturated_fats_score": unsaturated_fats_score,
        "balanced_score": balanced_score,
    }


def populate_diet_metrics(apps, schema_editor):
    NutritionFacts = apps.get_model("catalog", "NutritionFacts")
    for facts in NutritionFacts.objects.all().iterator():
        for field_name, field_value in _derive_metrics(facts).items():
            setattr(facts, field_name, field_value)
        facts.save(
            update_fields=[
                "estimated_energy_kcal",
                "estimated_energy_kj",
                "unsaturated_g",
                "starch_g",
                "calorie_score",
                "protein_score",
                "carbohydrates_score",
                "fibre_score",
                "saturated_fats_score",
                "unsaturated_fats_score",
                "balanced_score",
                "updated_at",
            ]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0009_profile_options_and_metrics"),
    ]

    operations = [
        migrations.AddField(
            model_name="nutritionfacts",
            name="calorie_score",
            field=models.DecimalField(blank=True, decimal_places=5, max_digits=8, null=True),
        ),
        migrations.AddField(
            model_name="nutritionfacts",
            name="carbohydrates_score",
            field=models.DecimalField(blank=True, decimal_places=5, max_digits=8, null=True),
        ),
        migrations.AddField(
            model_name="nutritionfacts",
            name="estimated_energy_kcal",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True),
        ),
        migrations.AddField(
            model_name="nutritionfacts",
            name="estimated_energy_kj",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True),
        ),
        migrations.AddField(
            model_name="nutritionfacts",
            name="fibre_score",
            field=models.DecimalField(blank=True, decimal_places=5, max_digits=8, null=True),
        ),
        migrations.AddField(
            model_name="nutritionfacts",
            name="protein_score",
            field=models.DecimalField(blank=True, decimal_places=5, max_digits=8, null=True),
        ),
        migrations.AddField(
            model_name="nutritionfacts",
            name="balanced_score",
            field=models.DecimalField(blank=True, decimal_places=5, max_digits=8, null=True),
        ),
        migrations.AddField(
            model_name="nutritionfacts",
            name="saturated_fats_score",
            field=models.DecimalField(blank=True, decimal_places=5, max_digits=8, null=True),
        ),
        migrations.AddField(
            model_name="nutritionfacts",
            name="starch_g",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True),
        ),
        migrations.AddField(
            model_name="nutritionfacts",
            name="unsaturated_fats_score",
            field=models.DecimalField(blank=True, decimal_places=5, max_digits=8, null=True),
        ),
        migrations.AddField(
            model_name="nutritionfacts",
            name="unsaturated_g",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True),
        ),
        migrations.RunPython(populate_diet_metrics, migrations.RunPython.noop),
    ]
