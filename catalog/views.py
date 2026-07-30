from decimal import Decimal
from difflib import SequenceMatcher
import json
import re

from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.db.models import Case, Exists, IntegerField, OuterRef, Q, Value, When
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import redirect, render
from django.views import View
from django.views.decorators.http import require_GET, require_http_methods

from catalog.forms import EmailLikeAuthenticationForm, PlannerProfileForm
from catalog.models import CuisineOption, CultureOption, Goal, IngredientImageAnalysis, NutritionEntry, PlannerProfile, Product
from catalog.services.health import calculate_profile_metrics
from catalog.services.llm import LLMServiceError
from catalog.services.nutrition import (
    SelectedProductInput,
    aggregate_profiles,
    aggregate_macro_summaries,
    build_product_profile,
    convert_to_grams,
    to_decimal,
)
from catalog.services.opensearch import OpenSearchError, ProductOpenSearchIndex
from catalog.services.pricing import (
    SelectedBuyProductInput,
    aggregate_price_profiles,
    build_price_profile,
    latest_snapshot_for_product,
)
from catalog.services.recipe_planner import (
    analyze_ingredient_image,
    clear_plan_items,
    get_plan_for_profile,
    get_profile_for_user,
    stream_recipe_suggestions,
    update_plan_from_payload,
)


def _planner_context(*, profile, plan, warnings, template_name):
    page_key = "recipe" if template_name == "manual" else "image_recipe"
    return {
        "template_name": template_name,
        "page_kind": template_name,
        "page_key": page_key,
        "cultures": CultureOption.objects.filter(is_active=True),
        "cuisines": CuisineOption.objects.filter(is_active=True),
        "goals": Goal.objects.filter(is_active=True),
        "profile": profile,
        "plan": plan,
        "plan_items": plan.items.select_related("product"),
        "latest_run": plan.recipe_runs.first(),
        "latest_image_analysis": plan.image_analyses.first(),
        "warnings": warnings,
        "metrics": calculate_profile_metrics(profile),
        "gender_choices": PlannerProfile.Gender.choices,
        "lifestyle_choices": PlannerProfile.Lifestyle.choices,
        "diet_style_choices": PlannerProfile.DietStyle.choices,
        "horizon_choices": plan.Horizon.choices,
    }


class CatalogLoginView(LoginView):
    authentication_form = EmailLikeAuthenticationForm
    template_name = "catalog/login.html"

    def get_success_url(self):
        return self.get_redirect_url() or "/profile/"


class CatalogLogoutView(View):
    http_method_names = ["get", "post"]

    def get(self, request, *args, **kwargs):
        logout(request)
        return redirect("/login/")

    def post(self, request, *args, **kwargs):
        logout(request)
        return redirect("/login/")


def _normalize_search_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def _search_tokens(query: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _normalize_search_text(query))


def _base_search_queryset():
    nutrition_entries = NutritionEntry.objects.filter(nutrition_facts__product=OuterRef("pk"))
    return Product.objects.filter(supermarket__slug="albert-heijn", is_active=True).annotate(
        has_nutrition_entries=Exists(nutrition_entries)
    )


def _search_queryset(query: str):
    normalized = _normalize_search_text(query)
    tokens = _search_tokens(query)
    if not normalized:
        return Product.objects.none()

    query_filter = Q()
    if tokens:
        for token in tokens:
            query_filter &= (
                Q(name__icontains=token)
                | Q(brand__icontains=token)
                | Q(package_size__icontains=token)
                | Q(description__icontains=token)
            )
    else:
        query_filter = (
            Q(name__icontains=normalized)
            | Q(brand__icontains=normalized)
            | Q(package_size__icontains=normalized)
            | Q(description__icontains=normalized)
        )

    return (
        _base_search_queryset()
        .filter(query_filter)
        .annotate(
            exact_name=Case(
                When(name__iexact=normalized, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
            starts_with_name=Case(
                When(name__istartswith=normalized, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            ),
        )
        .order_by("-has_nutrition_entries", "-exact_name", "-starts_with_name", "name", "id")[:12]
    )


def _fuzzy_search_queryset(query: str):
    normalized = _normalize_search_text(query)
    tokens = _search_tokens(query)
    if len(normalized) < 2:
        return Product.objects.none()

    candidate_filter = Q()
    seeds = set()
    for token in tokens or [normalized]:
        if not token:
            continue
        seeds.add(token[:1])
        if len(token) >= 2:
            seeds.add(token[:2])
        if len(token) >= 3:
            seeds.add(token[:3])
    for seed in seeds:
        candidate_filter |= (
            Q(name__icontains=seed)
            | Q(brand__icontains=seed)
            | Q(package_size__icontains=seed)
            | Q(description__icontains=seed)
        )

    if not candidate_filter:
        return Product.objects.none()

    scored = []
    candidates = (
        _base_search_queryset()
        .filter(candidate_filter)
        .only("id", "name", "brand", "package_size", "description")
        .order_by("-has_nutrition_entries", "name", "id")[:400]
    )
    for product in candidates:
        haystacks = [
            _normalize_search_text(product.name),
            _normalize_search_text(product.brand),
            _normalize_search_text(product.package_size),
            _normalize_search_text(product.description),
        ]
        token_score = 0.0
        if tokens:
            per_token_scores = []
            for token in tokens:
                best = max(
                    SequenceMatcher(None, token, candidate_token).ratio()
                    for haystack in haystacks
                    for candidate_token in re.findall(r"[a-z0-9]+", haystack)
                ) if any(re.findall(r"[a-z0-9]+", haystack) for haystack in haystacks) else 0.0
                per_token_scores.append(best)
            token_score = sum(per_token_scores) / len(per_token_scores)
        full_score = max(SequenceMatcher(None, normalized, haystack).ratio() for haystack in haystacks)
        score = max(token_score, full_score)
        if score >= 0.72:
            scored.append((score, int(product.has_nutrition_entries), product.id))

    if not scored:
        return Product.objects.none()

    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    product_ids = [product_id for _, _, product_id in scored[:12]]
    ordering = Case(*[When(id=product_id, then=Value(position)) for position, product_id in enumerate(product_ids)], output_field=IntegerField())
    return _base_search_queryset().filter(id__in=product_ids).order_by(ordering)


def _combined_search_queryset(query: str):
    direct_results = list(_search_queryset(query))
    if len(direct_results) >= 12:
        return direct_results

    seen_ids = {product.id for product in direct_results}
    fuzzy_results = [product for product in _fuzzy_search_queryset(query) if product.id not in seen_ids]
    return (direct_results + fuzzy_results)[:12]


PLANNER_NAME_STOPWORDS = {
    "ah",
    "biologisch",
    "biologische",
    "bio",
    "eetrijp",
    "pack",
}


def _planner_name_family_key(name: str) -> str:
    tokens = _search_tokens(name)
    filtered = [
        token
        for token in tokens
        if token not in PLANNER_NAME_STOPWORDS
        and not token.isdigit()
        and not re.fullmatch(r"\d+x\d+", token)
        and not re.fullmatch(r"\d+[-]?pack", token)
    ]
    return " ".join(filtered) or _normalize_search_text(name)


def _nutrition_signature_from_product(product: Product):
    facts = getattr(product, "nutrition_facts", None)
    if not facts:
        return None
    return (
        facts.energy_kcal,
        facts.protein_g,
        facts.fat_g,
        facts.saturates_g,
        facts.unsaturated_g,
        facts.carbohydrates_g,
        facts.sugars_g,
        facts.fiber_g,
        facts.starch_g,
        facts.salt_g,
    )


def _nutrition_signature_from_payload(item: dict):
    nutrition = item.get("nutrition_summary") or {}
    values = (
        nutrition.get("energy_kcal"),
        nutrition.get("protein_g"),
        nutrition.get("fat_g"),
        nutrition.get("saturates_g"),
        nutrition.get("unsaturated_g"),
        nutrition.get("carbohydrates_g"),
        nutrition.get("sugars_g"),
        nutrition.get("fiber_g"),
        nutrition.get("starch_g"),
        nutrition.get("salt_g"),
    )
    if all(value is None for value in values):
        return None
    return values


def _collapse_planner_product_results(products: list[Product]) -> list[Product]:
    seen = set()
    collapsed = []
    for product in products:
        key = (_planner_name_family_key(product.name), _nutrition_signature_from_product(product))
        if key in seen:
            continue
        seen.add(key)
        collapsed.append(product)
    return collapsed[:12]


def _collapse_planner_payload_results(results: list[dict]) -> list[dict]:
    seen = set()
    collapsed = []
    for item in results:
        key = (_planner_name_family_key(item.get("name", "")), _nutrition_signature_from_payload(item))
        if key in seen:
            continue
        seen.add(key)
        collapsed.append(item)
    return collapsed[:12]


def _opensearch_results(query: str):
    index = ProductOpenSearchIndex()
    if not index.is_configured():
        return None
    try:
        return index.search(query, size=12)
    except OpenSearchError:
        return None


@login_required(login_url="/login/")
@require_GET
def product_search_api(request):
    query = (request.GET.get("q") or "").strip()
    mode = (request.GET.get("mode") or "").strip().lower()
    if len(query) < 2:
        return JsonResponse({"results": []})

    results = []
    opensearch_results = _opensearch_results(query)
    if opensearch_results is not None:
        if mode == "planner":
            opensearch_results = _collapse_planner_payload_results(opensearch_results)
        return JsonResponse({"results": opensearch_results, "backend": "opensearch"})

    db_results = _combined_search_queryset(query)
    if mode == "planner":
        db_results = _collapse_planner_product_results(db_results)

    for product in db_results:
        snapshot = latest_snapshot_for_product(product)
        product_card = (snapshot.payload or {}).get("product_card", {}) if snapshot else {}
        results.append(
            {
                "id": product.id,
                "name": product.name,
                "brand": product.brand,
                "package_size": product.package_size,
                "image_url": product.image_url,
                "nutri_score_grade": product.nutri_score_grade,
                "has_nutrition": product.has_nutrition_entries,
                "price_amount": float(snapshot.price_amount) if snapshot and snapshot.price_amount is not None else None,
                "is_bonus": bool(product_card.get("isBonus")),
                "nutrition_summary": {
                    "energy_kcal": float(product.nutrition_facts.energy_kcal) if getattr(product, "nutrition_facts", None) and product.nutrition_facts.energy_kcal is not None else None,
                    "protein_g": float(product.nutrition_facts.protein_g) if getattr(product, "nutrition_facts", None) and product.nutrition_facts.protein_g is not None else None,
                    "fat_g": float(product.nutrition_facts.fat_g) if getattr(product, "nutrition_facts", None) and product.nutrition_facts.fat_g is not None else None,
                    "saturates_g": float(product.nutrition_facts.saturates_g) if getattr(product, "nutrition_facts", None) and product.nutrition_facts.saturates_g is not None else None,
                    "unsaturated_g": float(product.nutrition_facts.unsaturated_g) if getattr(product, "nutrition_facts", None) and product.nutrition_facts.unsaturated_g is not None else None,
                    "carbohydrates_g": float(product.nutrition_facts.carbohydrates_g) if getattr(product, "nutrition_facts", None) and product.nutrition_facts.carbohydrates_g is not None else None,
                    "sugars_g": float(product.nutrition_facts.sugars_g) if getattr(product, "nutrition_facts", None) and product.nutrition_facts.sugars_g is not None else None,
                    "fiber_g": float(product.nutrition_facts.fiber_g) if getattr(product, "nutrition_facts", None) and product.nutrition_facts.fiber_g is not None else None,
                    "starch_g": float(product.nutrition_facts.starch_g) if getattr(product, "nutrition_facts", None) and product.nutrition_facts.starch_g is not None else None,
                    "salt_g": float(product.nutrition_facts.salt_g) if getattr(product, "nutrition_facts", None) and product.nutrition_facts.salt_g is not None else None,
                },
            }
        )
    return JsonResponse({"results": results, "backend": "db"})


@login_required(login_url="/login/")
@require_http_methods(["GET", "POST"])
def nutrition_search_view(request):
    selected_rows = []
    combined_rows = []
    combined_macro_summary = {}
    warnings = []

    if request.method == "POST":
        product_ids = request.POST.getlist("product_id")
        quantities = request.POST.getlist("quantity")
        units = request.POST.getlist("unit")

        selected_inputs: list[SelectedProductInput] = []
        for product_id, quantity_text, unit in zip(product_ids, quantities, units):
            if not product_id:
                continue
            quantity = to_decimal(quantity_text) or Decimal("0")
            if quantity <= 0:
                warnings.append("Skipped a row with a non-positive quantity.")
                continue
            try:
                product = Product.objects.get(id=product_id, supermarket__slug="albert-heijn")
            except Product.DoesNotExist:
                warnings.append(f"Skipped unknown product id {product_id}.")
                continue
            selected_inputs.append(SelectedProductInput(product=product, quantity=quantity, unit=unit))

        profiles = []
        for item in selected_inputs:
            grams, note = convert_to_grams(item.product, item.quantity, item.unit)
            if note:
                warnings.append(note)
            if grams is None:
                continue
            profile = build_product_profile(item.product, grams)
            if not profile["rows"]:
                alternative = (
                    Product.objects.filter(
                        supermarket__slug="albert-heijn",
                        name=item.product.name,
                        nutrition_facts__entries__isnull=False,
                    )
                    .exclude(id=item.product.id)
                    .order_by("package_size", "id")
                    .first()
                )
                if alternative:
                    warnings.append(
                        f"No nutrition rows stored yet for {item.product.name}. Try {alternative.name} {alternative.package_size}."
                    )
                else:
                    warnings.append(f"No nutrition rows stored yet for {item.product.name}.")
            profiles.append(profile)

        selected_rows = profiles
        combined_rows = aggregate_profiles(profiles)
        combined_macro_summary = aggregate_macro_summaries(profiles)

    return render(
        request,
        "catalog/nutrition_search.html",
        {
            "selected_rows": selected_rows,
            "combined_rows": combined_rows,
            "combined_macro_summary": combined_macro_summary,
            "warnings": warnings,
            "page_key": "nutrition",
        },
    )


@login_required(login_url="/login/")
@require_http_methods(["GET", "POST"])
def shopping_list_view(request):
    selected_rows = []
    totals = {}
    warnings = []

    if request.method == "POST":
        product_ids = request.POST.getlist("product_id")
        quantities = request.POST.getlist("quantity")

        selected_inputs: list[SelectedBuyProductInput] = []
        for product_id, quantity_text in zip(product_ids, quantities):
            if not product_id:
                continue
            quantity = to_decimal(quantity_text) or Decimal("0")
            if quantity <= 0:
                warnings.append("Skipped a row with a non-positive quantity.")
                continue
            try:
                product = Product.objects.get(id=product_id, supermarket__slug="albert-heijn")
            except Product.DoesNotExist:
                warnings.append(f"Skipped unknown product id {product_id}.")
                continue
            selected_inputs.append(SelectedBuyProductInput(product=product, quantity=quantity))

        for item in selected_inputs:
            profile = build_price_profile(item.product, item.quantity)
            if profile["current_price"] is None:
                warnings.append(f"No price snapshot stored yet for {item.product.name}.")
            selected_rows.append(profile)

        totals = aggregate_price_profiles(selected_rows)

    return render(
        request,
        "catalog/shopping_list.html",
        {
            "selected_rows": selected_rows,
            "totals": totals,
            "warnings": warnings,
            "page_key": "shopping",
        },
    )


@login_required(login_url="/login/")
@require_http_methods(["GET", "POST"])
def recipe_planner_view(request):
    warnings = []
    profile = get_profile_for_user(request.user)
    plan = get_plan_for_profile(profile, "manual")

    if request.method == "POST":
        if request.POST.get("clear_ingredients") == "1":
            clear_plan_items(plan)
            warnings.append("Cleared the current ingredient list.")
        else:
            warnings.extend(update_plan_from_payload(plan=plan, payload=request.POST))

    return render(request, "catalog/recipe_planner.html", _planner_context(profile=profile, plan=plan, warnings=warnings, template_name="manual"))


@login_required(login_url="/login/")
@require_http_methods(["POST"])
def recipe_planner_stream_view(request):
    profile = get_profile_for_user(request.user)
    plan = get_plan_for_profile(profile, "manual")
    warnings = update_plan_from_payload(plan=plan, payload=request.POST)

    def event_stream():
        if warnings:
            yield f"event: warning\ndata: {json.dumps({'messages': warnings})}\n\n"
        yield f"event: status\ndata: {json.dumps({'message': 'Saving inputs and starting recipe generation...'})}\n\n"
        try:
            for event_type, payload in stream_recipe_suggestions(plan, profile):
                if event_type == "chunk":
                    yield f"event: chunk\ndata: {json.dumps({'text': payload})}\n\n"
                elif event_type == "complete":
                    yield f"event: complete\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except LLMServiceError as exc:
            yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@login_required(login_url="/login/")
@require_http_methods(["POST"])
def recipe_planner_image_view(request):
    profile = get_profile_for_user(request.user)
    plan = get_plan_for_profile(profile, "manual")
    warnings = update_plan_from_payload(plan=plan, payload=request.POST)
    uploaded = request.FILES.get("ingredient_image")
    if uploaded is None:
        warnings.append("Please choose an image before running ingredient detection.")
        return render(request, "catalog/recipe_planner.html", _planner_context(profile=profile, plan=plan, warnings=warnings, template_name="manual"))

    try:
        analysis, unmatched = analyze_ingredient_image(
            plan=plan,
            profile=profile,
            image_name=uploaded.name,
            image_content_type=getattr(uploaded, "content_type", "") or "image/jpeg",
            image_bytes=uploaded.read(),
        )
        if unmatched:
            warnings.append(
                "Some detected items could not be matched to the local AH catalog: " + ", ".join(unmatched[:8])
            )
        if analysis.extracted_items:
            warnings.append("Detected ingredients were merged into the current ingredient list where a local product match was found.")
    except LLMServiceError as exc:
        warnings.append(str(exc))

    return render(request, "catalog/recipe_planner.html", _planner_context(profile=profile, plan=plan, warnings=warnings, template_name="manual"))


@login_required(login_url="/login/")
@require_http_methods(["GET", "POST"])
def image_recipe_planner_view(request):
    warnings = []
    profile = get_profile_for_user(request.user)
    plan = get_plan_for_profile(profile, "image")
    if request.method == "POST":
        if request.POST.get("clear_ingredients") == "1":
            clear_plan_items(plan)
            warnings.append("Cleared the current ingredient list.")
        else:
            warnings.extend(update_plan_from_payload(plan=plan, payload=request.POST))
    return render(
        request,
        "catalog/image_recipe_planner.html",
        _planner_context(profile=profile, plan=plan, warnings=warnings, template_name="image"),
    )


@login_required(login_url="/login/")
@require_http_methods(["POST"])
def image_recipe_planner_image_view(request):
    profile = get_profile_for_user(request.user)
    plan = get_plan_for_profile(profile, "image")
    warnings = update_plan_from_payload(plan=plan, payload=request.POST)
    uploaded = request.FILES.get("ingredient_image")
    if uploaded is None:
        warnings.append("Please choose an image before running ingredient detection.")
        return render(
            request,
            "catalog/image_recipe_planner.html",
            _planner_context(profile=profile, plan=plan, warnings=warnings, template_name="image"),
        )

    try:
        analysis, unmatched = analyze_ingredient_image(
            plan=plan,
            profile=profile,
            image_name=uploaded.name,
            image_content_type=getattr(uploaded, "content_type", "") or "image/jpeg",
            image_bytes=uploaded.read(),
        )
        if unmatched:
            warnings.append(
                "Some detected items could not be matched to the local AH catalog: " + ", ".join(unmatched[:8])
            )
        if analysis.extracted_items:
            warnings.append("Detected ingredients were merged into the current ingredient list where a local product match was found.")
    except LLMServiceError as exc:
        warnings.append(str(exc))

    return render(
        request,
        "catalog/image_recipe_planner.html",
        _planner_context(profile=profile, plan=plan, warnings=warnings, template_name="image"),
    )


@login_required(login_url="/login/")
@require_http_methods(["POST"])
def image_recipe_planner_stream_view(request):
    profile = get_profile_for_user(request.user)
    plan = get_plan_for_profile(profile, "image")
    warnings = update_plan_from_payload(plan=plan, payload=request.POST)
    uploaded = request.FILES.get("ingredient_image")

    def event_stream():
        if warnings:
            yield f"event: warning\ndata: {json.dumps({'messages': warnings})}\n\n"
        if uploaded is None:
            yield f"event: error\ndata: {json.dumps({'message': 'Please upload an image before generating recipes.'})}\n\n"
            return
        try:
            yield f"event: status\ndata: {json.dumps({'message': 'Analyzing image and extracting ingredients...'})}\n\n"
            analysis, unmatched = analyze_ingredient_image(
                plan=plan,
                profile=profile,
                image_name=uploaded.name,
                image_content_type=getattr(uploaded, 'content_type', '') or 'image/jpeg',
                image_bytes=uploaded.read(),
            )
            if unmatched:
                yield f"event: warning\ndata: {json.dumps({'messages': ['Some detected items could not be matched to the local AH catalog: ' + ', '.join(unmatched[:8])]})}\n\n"
            if analysis.extracted_items:
                yield f"event: status\ndata: {json.dumps({'message': 'Image analyzed. Generating recipe suggestions from detected ingredients...'})}\n\n"
            for event_type, payload in stream_recipe_suggestions(plan, profile):
                if event_type == "chunk":
                    yield f"event: chunk\ndata: {json.dumps({'text': payload})}\n\n"
                elif event_type == "complete":
                    yield f"event: complete\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except LLMServiceError as exc:
            yield f"event: error\ndata: {json.dumps({'message': str(exc)})}\n\n"

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@login_required(login_url="/login/")
@require_http_methods(["GET", "POST"])
def profile_view(request):
    profile = get_profile_for_user(request.user)
    if request.method == "POST":
        form = PlannerProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            return redirect("/profile/")
    else:
        form = PlannerProfileForm(instance=profile)

    return render(
        request,
        "catalog/profile.html",
        {
            "form": form,
            "profile_obj": profile,
            "metrics": calculate_profile_metrics(profile),
            "page_key": "profile",
        },
    )
