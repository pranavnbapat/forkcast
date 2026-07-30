from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render

from catalog.models import ImportRun, NutritionFacts, Product, Supermarket
from catalog.services.opensearch import ProductOpenSearchIndex
from catalog.services.product_filters import food_candidate_queryset
from catalog.startup import read_startup_status


@staff_member_required
def sync_status_view(request):
    supermarket = Supermarket.objects.get(slug="albert-heijn")
    status = read_startup_status()
    latest_inventory_run = (
        ImportRun.objects.filter(
            supermarket=supermarket,
            mode__in=["startup_inventory_sync", "search_import", "partition_import"],
            status="completed",
        )
        .order_by("-created_at")
        .first()
    )
    latest_nutrition_run = (
        ImportRun.objects.filter(
            supermarket=supermarket,
            mode__in=["startup_nutrition_backfill", "detail_backfill"],
            status="completed",
        )
        .order_by("-created_at")
        .first()
    )
    latest_failure = (
        ImportRun.objects.filter(
            supermarket=supermarket,
            status="failed",
        )
        .order_by("-created_at")
        .first()
    )
    total_products = Product.objects.filter(supermarket=supermarket).count()
    food_candidate_products = food_candidate_queryset(Product.objects.filter(supermarket=supermarket)).count()
    products_marked_nutrition_unavailable = Product.objects.filter(
        supermarket=supermarket,
        nutrition_unavailable=True,
    ).count()
    products_with_nutrition = Product.objects.filter(
        supermarket=supermarket,
        nutrition_facts__entries__isnull=False,
    ).distinct().count()
    products_missing_nutrition = food_candidate_queryset(
        Product.objects.filter(supermarket=supermarket, nutrition_unavailable=False)
    ).exclude(
        id__in=NutritionFacts.objects.filter(
            product__supermarket=supermarket,
            entries__isnull=False,
        ).values_list("product_id", flat=True)
    ).count()

    completion_state = "in_progress"
    completion_label = "In Progress"
    completion_reason = "Inventory import or nutrition backfill is still running."
    if status and status.get("phase") == "failed":
        completion_state = "needs_attention"
        completion_label = "Needs Attention"
        completion_reason = "The latest startup sync failed and needs a restart or inspection."
    elif not status or not status.get("running"):
        if total_products > 0 and products_missing_nutrition == 0:
            completion_state = "complete"
            completion_label = "Complete"
            completion_reason = "No remaining nutrition backlog for food-candidate products."
        else:
            completion_state = "paused"
            completion_label = "Paused / Incomplete"
            completion_reason = "The worker is not running and there is still remaining work."
    elif status.get("phase") == "completed" and products_missing_nutrition == 0:
        completion_state = "complete"
        completion_label = "Complete"
        completion_reason = "The startup sync completed and no nutrition backlog remains."

    opensearch_status = ProductOpenSearchIndex().status()

    context = {
        "title": "AH Sync Status",
        "opts": None,
        "status": status,
        "supermarket": supermarket,
        "total_products": total_products,
        "food_candidate_products": food_candidate_products,
        "products_marked_nutrition_unavailable": products_marked_nutrition_unavailable,
        "products_with_nutrition": products_with_nutrition,
        "products_missing_nutrition": products_missing_nutrition,
        "completion_state": completion_state,
        "completion_label": completion_label,
        "completion_reason": completion_reason,
        "opensearch_status": opensearch_status,
        "latest_inventory_run": latest_inventory_run,
        "latest_nutrition_run": latest_nutrition_run,
        "latest_failure": latest_failure,
    }
    return render(request, "admin/catalog/sync_status.html", context)
