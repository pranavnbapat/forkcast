"""Resolve one canonical NutritionFacts row per product from several sources.

NutritionFacts stays the single row the rest of the app reads, so nothing
downstream has to know that multiple sources exist. What changes is that the
row now records where its values came from.

Resolution is field-level and trust-ordered:

- a higher-trust source never has its value overwritten by a lower-trust one
- a field the higher-trust source lacks is filled from the next source that
  has it
- `resolved_from_source` names the primary contributor, and `resolution_note`
  lists every source that contributed

That last property is what makes gap-filling worthwhile: Albert Heijn commonly
publishes energy and macros but omits fibre, and OpenFoodFacts often has it.
"""

from __future__ import annotations

from django.utils import timezone

from catalog.models import DataSource, NutritionFacts, Product
from catalog.services.diet_metrics import apply_diet_metrics
from catalog.services.matching import off_record_for_product
from catalog.services.openfoodfacts import OFF_SOURCE_SLUG


# Fields resolved across sources. Deliberately excludes the derived diet
# metrics, which are recomputed from these afterwards.
RESOLVABLE_FIELDS = (
    "energy_kj",
    "energy_kcal",
    "fat_g",
    "saturates_g",
    "carbohydrates_g",
    "sugars_g",
    "fiber_g",
    "protein_g",
    "salt_g",
)

AH_SOURCE_SLUG = "albert-heijn"


class NutritionResolutionError(Exception):
    pass


def _source_map() -> dict[str, DataSource]:
    return {source.slug: source for source in DataSource.objects.all()}


def _ah_values(facts: NutritionFacts | None) -> dict:
    if facts is None:
        return {}
    return {
        field: getattr(facts, field)
        for field in RESOLVABLE_FIELDS
        if getattr(facts, field) is not None
    }


def _off_values(off_record) -> dict:
    if off_record is None:
        return {}
    return {
        field: getattr(off_record, field, None)
        for field in RESOLVABLE_FIELDS
        if getattr(off_record, field, None) is not None
    }


def resolve_product_nutrition(
    product: Product,
    *,
    sources: dict[str, DataSource] | None = None,
    trusted_matches_only: bool = False,
    dry_run: bool = False,
) -> dict:
    """Resolve one product. Returns a report of what changed."""
    sources = sources or _source_map()
    facts = getattr(product, "nutrition_facts", None)
    off_record = off_record_for_product(product, trusted_only=trusted_matches_only)

    ah_values = _ah_values(facts)
    off_values = _off_values(off_record)

    if not ah_values and not off_values:
        return {"product_id": product.id, "action": "no_data", "filled": [], "contributors": []}

    # Trust order: lower trust_rank wins. Build the winning value per field.
    candidates = []
    ah_source = sources.get(AH_SOURCE_SLUG)
    off_source = sources.get(OFF_SOURCE_SLUG)
    if ah_values and ah_source is not None:
        candidates.append((ah_source, ah_values))
    if off_values and off_source is not None:
        candidates.append((off_source, off_values))
    candidates.sort(key=lambda pair: pair[0].trust_rank)

    resolved: dict[str, object] = {}
    provider: dict[str, str] = {}
    for source, values in candidates:
        for field, value in values.items():
            if field not in resolved:
                resolved[field] = value
                provider[field] = source.slug

    filled_from_lower_trust = [
        field for field, slug in provider.items() if slug != candidates[0][0].slug
    ]
    contributors = sorted(set(provider.values()))

    if dry_run:
        return {
            "product_id": product.id,
            "action": "would_update" if facts else "would_create",
            "filled": filled_from_lower_trust,
            "contributors": contributors,
        }

    created = False
    if facts is None:
        facts = NutritionFacts(product=product)
        created = True

    changed_fields = []
    for field, value in resolved.items():
        if getattr(facts, field, None) != value:
            setattr(facts, field, value)
            changed_fields.append(field)

    primary_slug = candidates[0][0].slug
    facts.resolved_from_source = sources.get(primary_slug)
    facts.resolved_at = timezone.now()
    facts.resolution_note = (
        f"primary={primary_slug}; contributors={','.join(contributors)}"
        + (f"; filled={','.join(sorted(filled_from_lower_trust))}" if filled_from_lower_trust else "")
    )[:255]

    apply_diet_metrics(facts)
    facts.save()

    return {
        "product_id": product.id,
        "action": "created" if created else "updated",
        "filled": filled_from_lower_trust,
        "contributors": contributors,
        "changed_fields": changed_fields,
    }


def resolve_queryset(
    queryset,
    *,
    trusted_matches_only: bool = False,
    dry_run: bool = False,
    progress_every: int = 0,
    stdout=None,
) -> dict:
    sources = _source_map()
    if AH_SOURCE_SLUG not in sources or OFF_SOURCE_SLUG not in sources:
        raise NutritionResolutionError(
            "Expected data sources are missing. Run migrations first."
        )

    stats = {
        "considered": 0,
        "created": 0,
        "updated": 0,
        "no_data": 0,
        "gap_filled_products": 0,
        "gap_filled_fields": 0,
    }

    for product in queryset.iterator(chunk_size=500):
        stats["considered"] += 1
        report = resolve_product_nutrition(
            product,
            sources=sources,
            trusted_matches_only=trusted_matches_only,
            dry_run=dry_run,
        )
        action = report["action"]
        if action in ("created", "would_create"):
            stats["created"] += 1
        elif action in ("updated", "would_update"):
            stats["updated"] += 1
        else:
            stats["no_data"] += 1

        if report["filled"]:
            stats["gap_filled_products"] += 1
            stats["gap_filled_fields"] += len(report["filled"])

        if progress_every and stdout and stats["considered"] % progress_every == 0:
            stdout.write(f"  resolved {stats['considered']} products...")

    return stats
