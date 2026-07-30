from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from catalog.models import Product
from catalog.services.diet_metrics import derive_diet_metrics


NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")
ENERGY_RE = re.compile(r"(-?\d+(?:[.,]\d+)?)\s*kJ.*?\((-?\d+(?:[.,]\d+)?)\s*kcal\)", re.IGNORECASE)


@dataclass
class SelectedProductInput:
    product: Product
    quantity: Decimal
    unit: str


def to_decimal(value: str | int | float | Decimal | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", ".")
    match = NUMBER_RE.search(text)
    if not match:
        return None
    try:
        return Decimal(match.group(0))
    except InvalidOperation:
        return None


def infer_density_g_per_ml(product: Product) -> Decimal:
    haystack = " ".join(
        part.lower()
        for part in [
            product.name,
            product.brand,
            product.description,
            product.package_size,
        ]
        if part
    )
    if "olijfolie" in haystack or "olive oil" in haystack or "olie" in haystack:
        return Decimal("0.91")
    if "melk" in haystack or "yoghurt" in haystack or "kwark" in haystack:
        return Decimal("1.03")
    return Decimal("1.00")


def convert_to_grams(product: Product, quantity: Decimal, unit: str) -> tuple[Decimal | None, str]:
    unit = (unit or "g").lower()
    if unit == "g":
        return quantity, ""
    if unit == "kg":
        return quantity * Decimal("1000"), ""
    if unit == "ml":
        density = infer_density_g_per_ml(product)
        return quantity * density, f"{quantity} ml converted using density {density} g/ml."
    if unit == "tsp":
        density = infer_density_g_per_ml(product)
        grams = quantity * Decimal("5") * density
        return grams, f"{quantity} tsp converted as 5 ml each using density {density} g/ml."
    if unit == "tbsp":
        density = infer_density_g_per_ml(product)
        grams = quantity * Decimal("15") * density
        return grams, f"{quantity} tbsp converted as 15 ml each using density {density} g/ml."
    return None, f"Unsupported unit '{unit}' for {product.name}."


def parse_value_and_unit(value_text: str) -> tuple[Decimal | None, str]:
    if not value_text:
        return None, ""
    match = NUMBER_RE.search(value_text)
    if not match:
        return None, ""
    value = to_decimal(match.group(0))
    tail = value_text[match.end() :].strip()
    unit = tail.split()[0] if tail else ""
    return value, unit


def parse_energy_pair(value_text: str) -> tuple[Decimal | None, Decimal | None]:
    if not value_text:
        return None, None
    match = ENERGY_RE.search(value_text)
    if not match:
        return None, None
    return to_decimal(match.group(1)), to_decimal(match.group(2))


def build_product_profile(product: Product, grams: Decimal) -> dict:
    factor = grams / Decimal("100")
    nutrition = getattr(product, "nutrition_facts", None)
    rows = []
    if nutrition:
        for entry in nutrition.entries.all():
            base_value, unit = parse_value_and_unit(entry.value_text)
            scaled_value = base_value * factor if base_value is not None else None
            energy_kj = None
            energy_kcal = None
            scaled_energy_kj = None
            scaled_energy_kcal = None
            if entry.label.strip().lower() == "energie":
                energy_kj, energy_kcal = parse_energy_pair(entry.value_text)
                if energy_kj is not None:
                    scaled_energy_kj = energy_kj * factor
                if energy_kcal is not None:
                    scaled_energy_kcal = energy_kcal * factor
            rows.append(
                {
                    "label": entry.label,
                    "per_100": entry.value_text,
                    "scaled_value": scaled_value,
                    "unit": unit,
                    "energy_kj": energy_kj,
                    "energy_kcal": energy_kcal,
                    "scaled_energy_kj": scaled_energy_kj,
                    "scaled_energy_kcal": scaled_energy_kcal,
                    "reference_intake_text": entry.reference_intake_text,
                }
            )

    macro_summary = build_macro_summary(product, grams)
    return {
        "product": product,
        "grams": grams,
        "factor": factor,
        "rows": rows,
        "macro_summary": macro_summary,
        "nutri_score_grade": product.nutri_score_grade,
        "nutri_score_label": product.nutri_score_label,
    }


def aggregate_profiles(profiles: list[dict]) -> list[dict]:
    totals: dict[tuple[str, str], Decimal] = {}
    order: list[tuple[str, str]] = []
    energy_totals = {"Energie": {"kj": Decimal("0"), "kcal": Decimal("0"), "has_pair": False}}
    for profile in profiles:
        for row in profile["rows"]:
            if row["scaled_value"] is None:
                continue
            if row["label"].strip().lower() == "energie" and row.get("scaled_energy_kj") is not None:
                energy_totals["Energie"]["kj"] += row["scaled_energy_kj"]
                if row.get("scaled_energy_kcal") is not None:
                    energy_totals["Energie"]["kcal"] += row["scaled_energy_kcal"]
                energy_totals["Energie"]["has_pair"] = True
                continue
            key = (row["label"], row["unit"])
            if key not in totals:
                totals[key] = Decimal("0")
                order.append(key)
            totals[key] += row["scaled_value"]

    aggregated = []
    if energy_totals["Energie"]["has_pair"]:
        aggregated.append(
            {
                "label": "Energie",
                "value": energy_totals["Energie"]["kj"],
                "unit": "kJ",
                "display_value": (
                    f"{energy_totals['Energie']['kj']:.2f} kJ "
                    f"({energy_totals['Energie']['kcal']:.2f} kcal)"
                ),
            }
        )
    for label, unit in order:
        if label.strip().lower() == "energie" and unit == "kJ":
            continue
        aggregated.append(
            {
                "label": label,
                "value": totals[(label, unit)],
                "unit": unit,
            }
        )
    return aggregated


def build_macro_summary(product: Product, grams: Decimal) -> dict:
    nutrition = getattr(product, "nutrition_facts", None)
    if not nutrition:
        return {}
    factor = grams / Decimal("100")
    summary = {}
    for source_field, target_key in (
        ("energy_kj", "declared_energy_kj"),
        ("energy_kcal", "declared_energy_kcal"),
        ("estimated_energy_kj", "estimated_energy_kj"),
        ("estimated_energy_kcal", "estimated_energy_kcal"),
        ("protein_g", "protein_g"),
        ("fat_g", "fat_g"),
        ("saturates_g", "saturates_g"),
        ("unsaturated_g", "unsaturated_g"),
        ("carbohydrates_g", "carbohydrates_g"),
        ("sugars_g", "sugars_g"),
        ("fiber_g", "fiber_g"),
        ("starch_g", "starch_g"),
        ("salt_g", "salt_g"),
    ):
        value = getattr(nutrition, source_field, None)
        if value is not None:
            summary[target_key] = value * factor
    if nutrition.balanced_score is not None:
        summary["balanced_score_per_100"] = nutrition.balanced_score
    return summary


def aggregate_macro_summaries(profiles: list[dict]) -> dict:
    totals = {}
    for profile in profiles:
        summary = profile.get("macro_summary") or {}
        for key, value in summary.items():
            if key == "balanced_score_per_100":
                continue
            totals[key] = totals.get(key, Decimal("0")) + value
    if totals and totals.get("estimated_energy_kcal") is None:
        derived = derive_diet_metrics(
            fat_g=totals.get("fat_g"),
            saturates_g=totals.get("saturates_g"),
            carbohydrates_g=totals.get("carbohydrates_g"),
            sugars_g=totals.get("sugars_g"),
            fiber_g=totals.get("fiber_g"),
            protein_g=totals.get("protein_g"),
        )
        for key in ("estimated_energy_kcal", "estimated_energy_kj", "unsaturated_g", "starch_g"):
            if totals.get(key) is None and derived.get(key) is not None:
                totals[key] = derived[key]
    return totals
