"""Link staged OpenFoodFacts records to local products.

Two paths, deliberately kept separate so their reliability stays visible:

- `match_by_barcode`: deterministic. Requires a GTIN identifier on the local
  product. Recorded with match_method=barcode.
- `match_by_name`: heuristic. Compares brand/name/quantity text. Recorded with
  match_method=fuzzy_name plus a confidence label, so downstream code can
  choose to trust only deterministic matches.

The AH mobile API has not been observed to expose a GTIN, so in practice the
fuzzy path carries the load today. That is a data limitation, not a design
preference, and the barcode path is ready for the moment GTINs appear.
"""

from __future__ import annotations

import re
from collections import defaultdict
from difflib import SequenceMatcher

from catalog.models import DataSource, OpenFoodFactsProduct, Product, ProductIdentifier
from catalog.services.openfoodfacts import OFF_SOURCE_SLUG, get_off_source


# Words that carry no distinguishing signal in Dutch grocery names.
STOPWORDS = {
    "ah", "bio", "biologisch", "biologische", "de", "het", "een", "van", "met",
    "en", "voor", "per", "stuk", "stuks", "pack", "gram", "kg", "ml", "liter",
    "l", "g", "eetrijp", "vers", "verse", "naturel",
}

# A token shared by more than this fraction of OFF records is too common to
# block on; blocking on it would degenerate to a full scan.
MAX_TOKEN_SHARE = 0.05

DEFAULT_THRESHOLD = 0.82
CONFIDENCE_BANDS = ((0.94, "high"), (0.87, "medium"))


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def tokens(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", normalize(text))
        if token not in STOPWORDS and not token.isdigit()
    ]


def signature(*parts: str) -> str:
    collected: list[str] = []
    for part in parts:
        collected.extend(tokens(part))
    # Order-insensitive so "bananen ah" and "ah bananen" compare equal.
    return " ".join(sorted(set(collected)))


def confidence_for(score: float) -> str:
    for floor, label in CONFIDENCE_BANDS:
        if score >= floor:
            return label
    return "low"


def _record_identifier(
    *,
    product: Product,
    source: DataSource,
    barcode: str,
    match_method: str,
    confidence_label: str = "",
    notes: str = "",
) -> bool:
    """Returns True when a new identifier row was written."""
    _, created = ProductIdentifier.objects.get_or_create(
        product=product,
        source=source,
        id_type=ProductIdentifier.IdType.OFF_BARCODE,
        value=barcode,
        defaults={
            "match_method": match_method,
            "confidence_label": confidence_label,
            "notes": notes,
        },
    )
    return created


def match_by_barcode(*, supermarket_slug: str = "albert-heijn", dry_run: bool = False) -> dict:
    """Join on GTIN identifiers already recorded against local products."""
    off_source = get_off_source()

    gtin_rows = ProductIdentifier.objects.filter(
        id_type=ProductIdentifier.IdType.GTIN,
        product__supermarket__slug=supermarket_slug,
    ).select_related("product")

    gtins = {row.value: row.product for row in gtin_rows}
    if not gtins:
        return {"candidates": 0, "matched": 0, "created": 0, "note": "no GTIN identifiers recorded"}

    off_barcodes = set(
        OpenFoodFactsProduct.objects.filter(barcode__in=list(gtins)).values_list("barcode", flat=True)
    )

    created = 0
    for barcode in off_barcodes:
        if dry_run:
            created += 1
            continue
        created += int(
            _record_identifier(
                product=gtins[barcode],
                source=off_source,
                barcode=barcode,
                match_method=ProductIdentifier.MatchMethod.BARCODE,
                confidence_label="high",
                notes="GTIN join",
            )
        )

    return {
        "candidates": len(gtins),
        "matched": len(off_barcodes),
        "created": created,
        "note": "",
    }


def _build_off_index(country_tag: str = ""):
    """Token -> set of OFF row indexes, for candidate blocking."""
    queryset = OpenFoodFactsProduct.objects.all()
    if country_tag:
        queryset = queryset.filter(countries_tags__contains=country_tag)

    rows = list(
        queryset.values_list("id", "barcode", "product_name", "brands", "quantity")
    )
    index: dict[str, set[int]] = defaultdict(set)
    signatures: dict[int, str] = {}

    for position, (_, _, product_name, brands, quantity) in enumerate(rows):
        sig = signature(product_name, brands, quantity)
        signatures[position] = sig
        for token in set(sig.split()):
            index[token].add(position)

    # Drop tokens so common they provide no selectivity.
    if rows:
        ceiling = max(1, int(len(rows) * MAX_TOKEN_SHARE))
        index = {token: positions for token, positions in index.items() if len(positions) <= ceiling}

    return rows, index, signatures


def match_by_name(
    *,
    supermarket_slug: str = "albert-heijn",
    threshold: float = DEFAULT_THRESHOLD,
    country_tag: str = "",
    limit: int = 0,
    skip_already_matched: bool = True,
    dry_run: bool = False,
) -> dict:
    """Heuristically link local products to staged OFF records by text."""
    off_source = get_off_source()
    rows, index, signatures = _build_off_index(country_tag)

    if not rows:
        return {
            "considered": 0, "matched": 0, "created": 0, "no_candidate": 0,
            "below_threshold": 0, "off_rows": 0,
            "note": "no OpenFoodFacts records staged",
        }

    products = Product.objects.filter(supermarket__slug=supermarket_slug, is_active=True)
    if skip_already_matched:
        products = products.exclude(
            id__in=ProductIdentifier.objects.filter(
                source=off_source,
                id_type=ProductIdentifier.IdType.OFF_BARCODE,
            ).values_list("product_id", flat=True)
        )
    products = products.order_by("id")
    if limit:
        products = products[:limit]

    considered = matched = created = no_candidate = below_threshold = 0

    for product in products.iterator(chunk_size=500):
        considered += 1
        product_sig = signature(product.name, product.brand, product.package_size)
        if not product_sig:
            no_candidate += 1
            continue

        candidate_positions: set[int] = set()
        for token in set(product_sig.split()):
            candidate_positions |= index.get(token, set())

        if not candidate_positions:
            no_candidate += 1
            continue

        best_score = 0.0
        best_position = None
        for position in candidate_positions:
            score = SequenceMatcher(None, product_sig, signatures[position]).ratio()
            if score > best_score:
                best_score = score
                best_position = position

        if best_position is None or best_score < threshold:
            below_threshold += 1
            continue

        matched += 1
        barcode = rows[best_position][1]
        if dry_run:
            created += 1
            continue
        created += int(
            _record_identifier(
                product=product,
                source=off_source,
                barcode=barcode,
                match_method=ProductIdentifier.MatchMethod.FUZZY_NAME,
                confidence_label=confidence_for(best_score),
                notes=f"score={best_score:.3f}",
            )
        )

    return {
        "considered": considered,
        "matched": matched,
        "created": created,
        "no_candidate": no_candidate,
        "below_threshold": below_threshold,
        "off_rows": len(rows),
        "note": "",
    }


def off_record_for_product(product: Product, *, trusted_only: bool = False) -> OpenFoodFactsProduct | None:
    """The OFF record linked to a product, if any.

    With trusted_only, fuzzy matches are ignored so callers can require a
    deterministic link.
    """
    identifiers = product.identifiers.filter(
        source__slug=OFF_SOURCE_SLUG,
        id_type=ProductIdentifier.IdType.OFF_BARCODE,
    )
    if trusted_only:
        identifiers = identifiers.exclude(
            match_method=ProductIdentifier.MatchMethod.FUZZY_NAME
        )
    identifier = identifiers.order_by("-is_primary", "id").first()
    if identifier is None:
        return None
    return OpenFoodFactsProduct.objects.filter(barcode=identifier.value).first()
