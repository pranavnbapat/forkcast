from __future__ import annotations

import json
from decimal import Decimal

from django.core.files.base import ContentFile
from django.utils import timezone

from catalog.models import Goal, IngredientImageAnalysis, IngredientPlan, IngredientPlanItem, PlannerProfile, Product, RecipeSuggestionRun
from catalog.services.health import calculate_profile_metrics
from catalog.services.llm import LLMServiceError, VLLMClient, VisionVLLMClient
from catalog.services.nutrition import to_decimal
from catalog.services.pricing import aggregate_price_profiles, build_price_profile


SPICE_HINT = (
    "Spices, herbs, salt, pepper, oil, and common condiments are assumed to already be available "
    "unless the user explicitly wants them costed. Do not include them in missing-items cost logic."
)


def get_default_profile(name: str = "Default Planner Profile") -> PlannerProfile:
    return PlannerProfile.objects.get_or_create(name=name)[0]


def get_profile_for_user(user) -> PlannerProfile:
    profile, _ = PlannerProfile.objects.get_or_create(
        user=user,
        defaults={"name": f"{user.username} Profile"},
    )
    if profile.name != f"{user.username} Profile":
        profile.name = f"{user.username} Profile"
        profile.save(update_fields=["name", "updated_at"])
    return profile


def get_default_plan(profile: PlannerProfile | None = None, name: str = "Default Ingredient Plan") -> IngredientPlan:
    defaults = {}
    if profile is not None:
        defaults["profile"] = profile
    plan, _ = IngredientPlan.objects.get_or_create(name=name, defaults=defaults)
    if profile and plan.profile_id != profile.id:
        plan.profile = profile
        plan.save(update_fields=["profile", "updated_at"])
    return plan


def get_plan_for_profile(profile: PlannerProfile, plan_key: str) -> IngredientPlan:
    plan_names = {
        "manual": f"{profile.name} Manual Ingredient Plan",
        "image": f"{profile.name} Image Ingredient Plan",
    }
    return get_default_plan(profile=profile, name=plan_names[plan_key])


def save_plan_items(plan: IngredientPlan, item_payloads: list[dict]) -> None:
    plan.items.all().delete()
    IngredientPlanItem.objects.bulk_create(
        [
            IngredientPlanItem(
                plan=plan,
                product=item["product"],
                quantity=item["quantity"],
                unit=item["unit"],
                is_pantry_staple=item["is_pantry_staple"],
                notes=item.get("notes", ""),
            )
            for item in item_payloads
        ]
    )


def clear_plan_items(plan: IngredientPlan) -> None:
    plan.items.all().delete()


def merge_plan_items(plan: IngredientPlan, item_payloads: list[dict]) -> None:
    existing_by_product = {item.product_id: item for item in plan.items.all()}
    for item in item_payloads:
        existing = existing_by_product.get(item["product"].id)
        if existing is None:
            IngredientPlanItem.objects.create(
                plan=plan,
                product=item["product"],
                quantity=item["quantity"],
                unit=item["unit"],
                is_pantry_staple=item["is_pantry_staple"],
                notes=item.get("notes", ""),
            )
            continue
        if existing.unit == item["unit"]:
            existing.quantity = existing.quantity + item["quantity"]
        else:
            existing.notes = ", ".join(filter(None, [existing.notes, f"vision: +{item['quantity']} {item['unit']}"]))
        existing.is_pantry_staple = existing.is_pantry_staple or item["is_pantry_staple"]
        existing.save(update_fields=["quantity", "notes", "is_pantry_staple", "updated_at"])


def update_profile_from_payload(*, profile: PlannerProfile, payload: dict) -> None:
    profile.primary_goal = Goal.objects.filter(id=payload.get("primary_goal")).first()
    profile.secondary_goal = Goal.objects.filter(id=payload.get("secondary_goal")).first()
    profile.gender = (payload.get("gender") or "").strip()
    profile.age = int(payload.get("age")) if str(payload.get("age") or "").isdigit() else None
    profile.height_cm = to_decimal(payload.get("height_cm"))
    profile.weight_kg = to_decimal(payload.get("weight_kg"))
    profile.culture_option_id = payload.get("culture_option") or None
    profile.cuisine_option_id = payload.get("cuisine_option") or None
    profile.culture = (payload.get("culture") or "").strip()
    profile.lifestyle = (payload.get("lifestyle") or "").strip()
    profile.fasting_pattern = (payload.get("fasting_pattern") or "").strip()
    profile.diet_style = (payload.get("diet_style") or "").strip()
    profile.allergies = (payload.get("allergies") or "").strip()
    profile.notes = (payload.get("notes") or "").strip()
    profile.save()

    
def update_plan_from_payload(*, plan: IngredientPlan, payload: dict) -> list[str]:
    warnings = []
    plan.horizon = payload.get("horizon") or plan.horizon
    plan.notes = (payload.get("plan_notes") or "").strip()
    plan.save()

    product_ids = payload.getlist("product_id")
    quantities = payload.getlist("quantity")
    units = payload.getlist("unit")
    pantry_flags = payload.getlist("is_pantry_staple")
    notes = payload.getlist("item_note")

    item_payloads = []
    for index, product_id in enumerate(product_ids):
        if not product_id:
            continue
        quantity = to_decimal(quantities[index] if index < len(quantities) else "") or Decimal("0")
        if quantity <= 0:
            warnings.append("Skipped a row with a non-positive quantity.")
            continue
        try:
            product = Product.objects.get(id=product_id, supermarket__slug="albert-heijn")
        except Product.DoesNotExist:
            warnings.append(f"Skipped unknown product id {product_id}.")
            continue
        item_payloads.append(
            {
                "product": product,
                "quantity": quantity,
                "unit": units[index] if index < len(units) and units[index] else "unit",
                "is_pantry_staple": str(index) in pantry_flags,
                "notes": notes[index] if index < len(notes) else "",
            }
        )
    save_plan_items(plan, item_payloads)
    return warnings


def update_profile_and_plan_from_payload(*, profile: PlannerProfile, plan: IngredientPlan, payload: dict) -> list[str]:
    update_profile_from_payload(profile=profile, payload=payload)
    return update_plan_from_payload(plan=plan, payload=payload)


def find_bonus_substitutes(product: Product, limit: int = 3) -> list[dict]:
    tokens = [
        token.strip(".,()%").lower()
        for token in product.name.split()
        if len(token.strip(".,()%")) >= 4
    ]
    if not tokens:
        return []
    queryset = Product.objects.filter(supermarket=product.supermarket, is_active=True).exclude(id=product.id)
    for token in tokens[:3]:
        queryset = queryset.filter(name__icontains=token)
    queryset = queryset[:20]
    candidates = []
    current_profile = build_price_profile(product, Decimal("1"))
    current_price = current_profile["current_total"]
    for candidate in queryset:
        price_profile = build_price_profile(candidate, Decimal("1"))
        if price_profile["current_total"] is None:
            continue
        if not price_profile["is_bonus"] and current_price is not None and price_profile["current_total"] >= current_price:
            continue
        candidates.append(
            {
                "name": candidate.name,
                "package_size": candidate.package_size,
                "current_price": str(price_profile["current_total"]),
                "is_bonus": price_profile["is_bonus"],
                "bonus_label": price_profile["bonus_label"],
            }
        )
        if len(candidates) >= limit:
            break
    return candidates


def match_product_candidate(name: str) -> Product | None:
    tokens = [token.strip(".,()%").lower() for token in name.split() if len(token.strip(".,()%")) >= 3]
    queryset = Product.objects.filter(supermarket__slug="albert-heijn", is_active=True)
    for token in tokens[:4]:
        queryset = queryset.filter(name__icontains=token)
    return queryset.order_by("name", "id").first()


def build_planner_context(plan: IngredientPlan, profile: PlannerProfile) -> dict:
    items = []
    price_profiles = []
    for item in plan.items.select_related("product", "product__supermarket"):
        price_profile = build_price_profile(item.product, item.quantity)
        price_profiles.append(price_profile)
        items.append(
            {
                "product_name": item.product.name,
                "brand": item.product.brand,
                "package_size": item.product.package_size,
                "quantity": str(item.quantity),
                "unit": item.unit,
                "is_pantry_staple": item.is_pantry_staple,
                "current_total": str(price_profile["current_total"]) if price_profile["current_total"] is not None else "",
                "regular_total": str(price_profile["regular_total"]) if price_profile["regular_total"] is not None else "",
                "is_bonus": price_profile["is_bonus"],
                "bonus_label": price_profile["bonus_label"],
                "pricing_note": price_profile["pricing_note"],
                "substitutes": [] if item.is_pantry_staple else find_bonus_substitutes(item.product),
            }
        )

    metrics = calculate_profile_metrics(profile)
    return {
        "plan_name": plan.name,
        "horizon": plan.get_horizon_display(),
        "plan_notes": plan.notes,
        "profile": {
            "primary_goal": profile.primary_goal.name if profile.primary_goal else "",
            "secondary_goal": profile.secondary_goal.name if profile.secondary_goal else "",
            "gender": profile.get_gender_display() if profile.gender else "",
            "age": profile.age or "",
            "height_cm": str(profile.height_cm) if profile.height_cm is not None else "",
            "weight_kg": str(profile.weight_kg) if profile.weight_kg is not None else "",
            "culture_option": profile.culture_option.name if profile.culture_option else "",
            "cuisine_option": profile.cuisine_option.name if profile.cuisine_option else "",
            "culture": profile.culture,
            "lifestyle": profile.get_lifestyle_display() if profile.lifestyle else "",
            "fasting_pattern": profile.fasting_pattern,
            "diet_style": profile.get_diet_style_display() if profile.diet_style else "",
            "allergies": profile.allergies,
            "notes": profile.notes,
        },
        "estimated_metrics": {key: str(value) for key, value in metrics.items()},
        "items": items,
        "price_summary": aggregate_price_profiles(price_profiles),
        "spice_hint": SPICE_HINT,
    }


def build_recipe_prompt(context: dict) -> tuple[str, str]:
    system_prompt = (
        "You are a pragmatic meal-planning assistant. "
        "Return only valid JSON. "
        "Use the user's primary and secondary goals. "
        "If BMR and TDEE are available, explicitly reason from them. "
        "Give direct guidance for maintenance, fat loss, muscle gain, or higher-protein eating depending on the goal. "
        "Prefer recipes matching the stated culture and diet style. "
        "Spices and condiments are assumed available and should not be treated as missing shopping items unless essential specialty ingredients are absent."
    )
    user_prompt = (
        "Generate recipe suggestions from the saved ingredient plan.\n"
        "Return JSON with this shape:\n"
        "{"
        '"overview": {"primary_goal_fit": string, "secondary_goal_fit": string},'
        '"metabolic_context": {"bmi": string, "bmr_kcal": string, "tdee_kcal": string, "planning_note": string},'
        '"goal_explanation": {"summary": string, "reasoning": [string]},'
        '"daily_targets": {"energy_kcal": string, "protein_g": string, "fat_g": string, "carb_g": string, "fiber_g": string, "sugar_guidance": string, "salt_guidance": string, "meal_distribution": string},'
        '"recipes": ['
        '{"title": string, "why_it_fits": string, "servings": string, "uses_ingredients": [string], '
        '"missing_items": [string], "cheaper_bonus_substitutes": [string], "steps": [string], '
        '"estimated_cost_note": string}'
        "],"
        '"shopping_gaps": [string],'
        '"money_saving_notes": [string],'
        '"nutrition_notes": [string],'
        '"questions_to_clarify": [string]'
        "}\n\n"
        "Context:\n"
        f"{json.dumps(context, ensure_ascii=False, indent=2, default=str)}"
    )
    return system_prompt, user_prompt


def build_image_analysis_prompt(plan: IngredientPlan, profile: PlannerProfile) -> tuple[str, str]:
    context = build_planner_context(plan, profile)
    system_prompt = (
        "You are an ingredient-vision assistant. Return only valid JSON. "
        "Identify visible grocery ingredients or packaged products in the image. "
        "Estimate quantities conservatively. If quantity is uncertain, say so in notes. "
        "Prefer food items. Ignore plates, furniture, and non-food objects unless they are obviously edible or packaged groceries."
    )
    user_prompt = (
        "Analyze this image of ingredients or groceries.\n"
        "Return JSON with this shape:\n"
        "{"
        '"summary": string,'
        '"detected_items": ['
        '{"name": string, "estimated_quantity": string, "unit": string, "confidence": string, "notes": string}'
        "],"
        '"questions_to_clarify": [string]'
        "}\n\n"
        "Planner context for diet and pantry assumptions:\n"
        f"{json.dumps(context, ensure_ascii=False, indent=2, default=str)}"
    )
    return system_prompt, user_prompt


def analyze_ingredient_image(
    *,
    plan: IngredientPlan,
    profile: PlannerProfile,
    image_name: str,
    image_content_type: str,
    image_bytes: bytes,
):
    system_prompt, user_prompt = build_image_analysis_prompt(plan, profile)
    client = VisionVLLMClient()
    analysis = IngredientImageAnalysis.objects.create(
        plan=plan,
        profile=profile,
        model_name=client.model,
        status="running",
        prompt_text=user_prompt,
    )
    analysis.image.save(image_name, ContentFile(image_bytes), save=True)
    try:
        response_text, response_json = client.chat_json_with_image(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            image_bytes=image_bytes,
            image_content_type=image_content_type or "image/jpeg",
        )
        detected_items = response_json.get("detected_items") or []
        matched_payloads = []
        unmatched = []
        normalized_detected = []
        for item in detected_items:
            name = (item.get("name") or "").strip()
            quantity = to_decimal(item.get("estimated_quantity")) or Decimal("1")
            unit = (item.get("unit") or "unit").strip() or "unit"
            notes = (item.get("notes") or "").strip()
            matched_product = match_product_candidate(name) if name else None
            normalized_detected.append(
                {
                    "name": name,
                    "estimated_quantity": str(quantity),
                    "unit": unit,
                    "confidence": item.get("confidence", ""),
                    "notes": notes,
                    "matched_product_id": matched_product.id if matched_product else None,
                    "matched_product_name": matched_product.name if matched_product else "",
                }
            )
            if matched_product is None:
                unmatched.append(name)
                continue
            matched_payloads.append(
                {
                    "product": matched_product,
                    "quantity": quantity,
                    "unit": unit,
                    "is_pantry_staple": False,
                    "notes": notes or "detected from image",
                }
            )
        if matched_payloads:
            merge_plan_items(plan, matched_payloads)
        analysis.response_text = response_text
        analysis.response_json = response_json
        analysis.extracted_items = normalized_detected
        analysis.status = "completed"
        analysis.save(update_fields=["response_text", "response_json", "extracted_items", "status", "updated_at"])
        return analysis, unmatched
    except LLMServiceError as exc:
        analysis.status = "failed"
        analysis.error_text = str(exc)
        analysis.save(update_fields=["status", "error_text", "updated_at"])
        raise


def generate_recipe_suggestions(plan: IngredientPlan, profile: PlannerProfile) -> RecipeSuggestionRun:
    context = build_planner_context(plan, profile)
    system_prompt, user_prompt = build_recipe_prompt(context)
    client = VLLMClient()
    run = RecipeSuggestionRun.objects.create(
        plan=plan,
        profile=profile,
        model_name=client.model,
        status="running",
        prompt_text=user_prompt,
    )
    try:
        response_text, response_json = client.chat_json(system_prompt=system_prompt, user_prompt=user_prompt)
        run.response_text = response_text
        run.response_json = response_json
        run.status = "completed"
        run.save(update_fields=["response_text", "response_json", "status", "updated_at"])
        plan.last_generated_at = timezone.now()
        plan.save(update_fields=["last_generated_at", "updated_at"])
    except LLMServiceError as exc:
        run.status = "failed"
        run.error_text = str(exc)
        run.save(update_fields=["status", "error_text", "updated_at"])
        raise
    return run


def stream_recipe_suggestions(plan: IngredientPlan, profile: PlannerProfile):
    context = build_planner_context(plan, profile)
    system_prompt, user_prompt = build_recipe_prompt(context)
    client = VLLMClient()
    run = RecipeSuggestionRun.objects.create(
        plan=plan,
        profile=profile,
        model_name=client.model,
        status="running",
        prompt_text=user_prompt,
    )
    try:
        for event_type, payload in client.chat_json_stream(system_prompt=system_prompt, user_prompt=user_prompt):
            if event_type == "chunk":
                yield ("chunk", payload)
                continue
            full_text = payload["text"]
            response_json = payload["json"]
            run.response_text = full_text
            run.response_json = response_json
            run.status = "completed"
            run.save(update_fields=["response_text", "response_json", "status", "updated_at"])
            plan.last_generated_at = timezone.now()
            plan.save(update_fields=["last_generated_at", "updated_at"])
            yield ("complete", {"run_id": run.id, "response_json": response_json})
    except LLMServiceError as exc:
        run.status = "failed"
        run.error_text = str(exc)
        run.save(update_fields=["status", "error_text", "updated_at"])
        raise
