from __future__ import annotations

from decimal import Decimal

from catalog.models import NutritionFacts


ZERO = Decimal("0")


def _has_all(*values) -> bool:
    return all(value is not None for value in values)


def _clamped_non_negative(value: Decimal) -> Decimal:
    return value if value >= ZERO else ZERO


def derive_diet_metrics(*, fat_g, saturates_g, carbohydrates_g, sugars_g, fiber_g, protein_g) -> dict:
    estimated_energy_kcal = None
    if _has_all(fat_g, protein_g, carbohydrates_g):
        estimated_energy_kcal = (fat_g * Decimal("9")) + (protein_g * Decimal("4")) + (carbohydrates_g * Decimal("4"))

    estimated_energy_kj = None
    if estimated_energy_kcal is not None:
        estimated_energy_kj = estimated_energy_kcal * Decimal("4.184")

    unsaturated_g = None
    if _has_all(fat_g, saturates_g):
        unsaturated_g = _clamped_non_negative(fat_g - saturates_g)

    starch_g = None
    if _has_all(carbohydrates_g, sugars_g, fiber_g):
        starch_g = _clamped_non_negative(carbohydrates_g - sugars_g - fiber_g)

    calorie_score = None
    if estimated_energy_kcal is not None:
        calorie_score = (Decimal("400") - estimated_energy_kcal) / Decimal("400")

    protein_score = None
    if protein_g is not None:
        protein_score = protein_g / Decimal("20")

    carbohydrates_score = None
    if carbohydrates_g is not None:
        carbohydrates_score = (Decimal("50") - carbohydrates_g) / Decimal("50")

    fibre_score = None
    if fiber_g is not None:
        fibre_score = fiber_g / Decimal("15")

    saturated_fats_score = None
    if saturates_g is not None:
        saturated_fats_score = (Decimal("10") - saturates_g) / Decimal("10")

    unsaturated_fats_score = None
    if unsaturated_g is not None:
        unsaturated_fats_score = unsaturated_g / Decimal("10")

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


def apply_diet_metrics(nutrition_facts: NutritionFacts) -> NutritionFacts:
    metrics = derive_diet_metrics(
        fat_g=nutrition_facts.fat_g,
        saturates_g=nutrition_facts.saturates_g,
        carbohydrates_g=nutrition_facts.carbohydrates_g,
        sugars_g=nutrition_facts.sugars_g,
        fiber_g=nutrition_facts.fiber_g,
        protein_g=nutrition_facts.protein_g,
    )
    for field_name, field_value in metrics.items():
        setattr(nutrition_facts, field_name, field_value)
    return nutrition_facts
