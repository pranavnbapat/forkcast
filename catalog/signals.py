from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from catalog.models import NutritionFacts, Product, ProductQualityProfile, ProductSnapshot
from catalog.services.opensearch import OpenSearchError, ProductOpenSearchIndex


def _should_auto_index() -> bool:
    return settings.OPENSEARCH_ENABLED and settings.OPENSEARCH_AUTO_INDEX_ON_SAVE


def _schedule_product_index(product_id: int):
    if not _should_auto_index():
        return

    def _index():
        try:
            product = Product.objects.select_related("nutrition_facts").get(id=product_id)
            ProductOpenSearchIndex().index_products([product])
        except (Product.DoesNotExist, OpenSearchError):
            return

    transaction.on_commit(_index)


@receiver(post_save, sender=Product)
def index_product_on_save(sender, instance, **kwargs):
    _schedule_product_index(instance.id)


@receiver(post_save, sender=NutritionFacts)
def index_product_on_nutrition_save(sender, instance, **kwargs):
    _schedule_product_index(instance.product_id)


@receiver(post_save, sender=ProductSnapshot)
def index_product_on_snapshot_save(sender, instance, **kwargs):
    _schedule_product_index(instance.product_id)


@receiver(post_save, sender=ProductQualityProfile)
def index_product_on_quality_save(sender, instance, **kwargs):
    _schedule_product_index(instance.product_id)


@receiver(post_delete, sender=Product)
def delete_product_from_index(sender, instance, **kwargs):
    if not _should_auto_index():
        return

    def _delete():
        try:
            ProductOpenSearchIndex().delete_product(instance.id)
        except OpenSearchError:
            return

    transaction.on_commit(_delete)
