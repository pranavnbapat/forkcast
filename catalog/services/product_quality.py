from __future__ import annotations

import json
from collections.abc import Iterable

from django.utils import timezone

from catalog.models import Product, ProductQualityProfile
from catalog.services.llm import LLMServiceError, VLLMClient


class ProductQualityError(Exception):
    pass


def _build_product_payload(product: Product) -> dict:
    facts = getattr(product, "nutrition_facts", None)
    return {
        "id": product.id,
        "name": product.name,
        "brand": product.brand,
        "category_name": product.category_name,
        "subcategory_name": product.subcategory_name,
        "package_size": product.package_size,
        "description": product.description,
        "ingredients": product.ingredients,
        "allergen_info": product.allergen_info,
        "nutrition_summary": {
            "energy_kcal": str(facts.energy_kcal) if facts and facts.energy_kcal is not None else "",
            "protein_g": str(facts.protein_g) if facts and facts.protein_g is not None else "",
            "fat_g": str(facts.fat_g) if facts and facts.fat_g is not None else "",
            "carbohydrates_g": str(facts.carbohydrates_g) if facts and facts.carbohydrates_g is not None else "",
            "fiber_g": str(facts.fiber_g) if facts and facts.fiber_g is not None else "",
            "salt_g": str(facts.salt_g) if facts and facts.salt_g is not None else "",
        },
    }


def _system_prompt() -> str:
    return (
        "You are a food quality and spoilage estimation assistant. "
        "Return one compact JSON object only. "
        "Do not invent certainty where the product metadata is incomplete. "
        "Use realistic ranges, not single-point certainty, for shelf life. "
        "Assume natural storage conditions for a consumer kitchen. "
        "Distinguish ambient, refrigerated, and frozen storage. "
        "State assumptions clearly for whole vs cut, opened vs unopened, ripe vs unripe, and cooked vs raw when relevant. "
        "Focus on food safety, likely spoilage signals, volatile compounds, and sensor-relevant outputs. "
        "If a field is unknown, use an empty string, empty list, or null instead of hallucinating specifics."
    )


def _user_prompt(product: Product) -> str:
    schema = {
        "confidence_label": "low|medium|high",
        "assumptions_text": "Plain-language assumptions and caveats.",
        "storage_notes": "Important storage conditions, humidity, packaging, opened/unopened, ripe/unripe, whole/cut, cooked/raw.",
        "ambient_days_min": 0,
        "ambient_days_max": 0,
        "refrigerated_days_min": 0,
        "refrigerated_days_max": 0,
        "frozen_days_min": 0,
        "frozen_days_max": 0,
        "nutrient_degradation_summary": "How nutrients degrade over time and under what conditions.",
        "nutrient_degradation_json": {
            "sensitive_nutrients": ["vitamin c"],
            "mechanisms": ["oxidation"],
            "timeline_notes": ["text"],
        },
        "spoilage_summary": "High-level spoilage description.",
        "odor_notes": "Expected smell changes.",
        "color_change_notes": "Expected color changes.",
        "texture_change_notes": "Expected texture changes.",
        "visible_signs_json": ["mold growth", "surface darkening"],
        "spoilage_processes_json": ["oxidation", "microbial spoilage"],
        "airborne_molecules_json": [
            {
                "name": "ethanol",
                "type": "VOC|gas|other",
                "why_it_matters": "Produced during fermentation/spoilage.",
                "sensor_relevance": "MOS gas sensor, PID, e-nose",
            }
        ],
        "sensor_targets_json": [
            {
                "target": "ammonia",
                "sensor_type": "electrochemical|MOS|NDIR|colorimetric|other",
                "signal_pattern": "increase/decrease or qualitative note",
                "notes": "why it may be relevant",
            }
        ],
        "safety_risk_notes": "Likely risks if spoiled and consumed.",
        "discard_guidance": "When to discard, with practical consumer guidance.",
    }
    return (
        "Estimate a product quality profile for this supermarket item.\n"
        "This output will be stored as an LLM-derived source and may be replaced or compared with later scientific/manual sources.\n"
        "The product data is:\n"
        f"{json.dumps(_build_product_payload(product), ensure_ascii=True, indent=2)}\n\n"
        "Return valid JSON matching this schema:\n"
        f"{json.dumps(schema, ensure_ascii=True, indent=2)}"
    )


def _int_or_none(value):
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class ProductQualityEnricher:
    def __init__(self, *, source_name: str = "vllm_default", client: VLLMClient | None = None):
        self.source_name = source_name
        self.client = client or VLLMClient()

    def enrich_product(self, product: Product) -> ProductQualityProfile:
        try:
            raw_text, payload = self.client.chat_json(
                system_prompt=_system_prompt(),
                user_prompt=_user_prompt(product),
                temperature=0.2,
            )
        except LLMServiceError as exc:
            raise ProductQualityError(str(exc)) from exc
        return self._save_profile(product, payload, raw_text)

    def enrich_products(self, products: Iterable[Product]) -> list[ProductQualityProfile]:
        return [self.enrich_product(product) for product in products]

    def _save_profile(self, product: Product, payload: dict, raw_text: str) -> ProductQualityProfile:
        defaults = {
            "source_reference": "",
            "is_estimated": True,
            "confidence_label": str(payload.get("confidence_label", "")).strip()[:20],
            "assumptions_text": str(payload.get("assumptions_text", "")).strip(),
            "storage_notes": str(payload.get("storage_notes", "")).strip(),
            "ambient_days_min": _int_or_none(payload.get("ambient_days_min")),
            "ambient_days_max": _int_or_none(payload.get("ambient_days_max")),
            "refrigerated_days_min": _int_or_none(payload.get("refrigerated_days_min")),
            "refrigerated_days_max": _int_or_none(payload.get("refrigerated_days_max")),
            "frozen_days_min": _int_or_none(payload.get("frozen_days_min")),
            "frozen_days_max": _int_or_none(payload.get("frozen_days_max")),
            "nutrient_degradation_summary": str(payload.get("nutrient_degradation_summary", "")).strip(),
            "nutrient_degradation_json": payload.get("nutrient_degradation_json") or {},
            "spoilage_summary": str(payload.get("spoilage_summary", "")).strip(),
            "odor_notes": str(payload.get("odor_notes", "")).strip(),
            "color_change_notes": str(payload.get("color_change_notes", "")).strip(),
            "texture_change_notes": str(payload.get("texture_change_notes", "")).strip(),
            "visible_signs_json": payload.get("visible_signs_json") or [],
            "spoilage_processes_json": payload.get("spoilage_processes_json") or [],
            "airborne_molecules_json": payload.get("airborne_molecules_json") or [],
            "sensor_targets_json": payload.get("sensor_targets_json") or [],
            "safety_risk_notes": str(payload.get("safety_risk_notes", "")).strip(),
            "discard_guidance": str(payload.get("discard_guidance", "")).strip(),
            "raw_response_text": raw_text,
            "raw_response_json": payload,
            "last_generated_at": timezone.now(),
        }
        profile, _ = ProductQualityProfile.objects.update_or_create(
            product=product,
            source_type=ProductQualityProfile.SourceType.LLM,
            source_name=self.source_name,
            defaults=defaults,
        )
        return profile
