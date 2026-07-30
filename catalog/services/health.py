from __future__ import annotations

from decimal import Decimal

from catalog.models import PlannerProfile


ACTIVITY_FACTORS = {
    PlannerProfile.Lifestyle.SEDENTARY: Decimal("1.20"),
    PlannerProfile.Lifestyle.LIGHTLY_ACTIVE: Decimal("1.375"),
    PlannerProfile.Lifestyle.ACTIVE: Decimal("1.55"),
    PlannerProfile.Lifestyle.VERY_ACTIVE: Decimal("1.725"),
}


def calculate_profile_metrics(profile: PlannerProfile) -> dict:
    if not profile.height_cm or not profile.weight_kg:
        return {}

    height_cm = Decimal(profile.height_cm)
    weight_kg = Decimal(profile.weight_kg)
    height_m = height_cm / Decimal("100")
    bmi = weight_kg / (height_m * height_m) if height_m > 0 else None

    bmr = None
    if profile.age and profile.gender in {PlannerProfile.Gender.MALE, PlannerProfile.Gender.FEMALE}:
        if profile.gender == PlannerProfile.Gender.MALE:
            bmr = Decimal("10") * weight_kg + Decimal("6.25") * height_cm - Decimal("5") * Decimal(profile.age) + Decimal("5")
        else:
            bmr = Decimal("10") * weight_kg + Decimal("6.25") * height_cm - Decimal("5") * Decimal(profile.age) - Decimal("161")

    tdee = None
    if bmr is not None and profile.lifestyle in ACTIVITY_FACTORS:
        tdee = bmr * ACTIVITY_FACTORS[profile.lifestyle]

    return {
        "height_cm": height_cm,
        "weight_kg": weight_kg,
        "bmi": bmi.quantize(Decimal("0.1")) if bmi is not None else None,
        "bmr_kcal": bmr.quantize(Decimal("1")) if bmr is not None else None,
        "tdee_kcal": tdee.quantize(Decimal("1")) if tdee is not None else None,
    }
