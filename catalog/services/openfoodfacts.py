"""OpenFoodFacts ingestion.

OFF is a reference database, not a retailer: it describes products but carries
no price or promotion data. Records are staged by barcode in
OpenFoodFactsProduct and matched to local products separately.

Bulk loading reads the published JSONL dumps rather than the API. OFF asks
that bulk consumers use the dumps, and the API is only appropriate for
targeted single-barcode lookups.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path

from django.utils import timezone

from catalog.models import DataSource, OpenFoodFactsProduct


OFF_SOURCE_SLUG = "openfoodfacts"

# OFF nutriment key -> our field name. OFF reports per-100g values with a
# _100g suffix; energy-kcal_100g is not always present even when energy_100g is.
NUTRIMENT_FIELD_MAP = {
    "energy-kj_100g": "energy_kj",
    "energy-kcal_100g": "energy_kcal",
    "fat_100g": "fat_g",
    "saturated-fat_100g": "saturates_g",
    "carbohydrates_100g": "carbohydrates_g",
    "sugars_100g": "sugars_g",
    "fiber_100g": "fiber_g",
    "proteins_100g": "protein_g",
    "salt_100g": "salt_g",
    "sodium_100g": "sodium_g",
}

# Fields that make a record meaningfully different; used for change detection.
HASHED_FIELDS = (
    "product_name",
    "brands",
    "quantity",
    "serving_size",
    "ingredients_text",
    "allergens_text",
    "nutriscore_grade",
    "nova_group",
    *NUTRIMENT_FIELD_MAP.values(),
)


class OpenFoodFactsError(Exception):
    pass


def get_off_source() -> DataSource:
    try:
        return DataSource.objects.get(slug=OFF_SOURCE_SLUG)
    except DataSource.DoesNotExist as exc:
        raise OpenFoodFactsError(
            f"DataSource '{OFF_SOURCE_SLUG}' is missing. Run migrations first."
        ) from exc


def _to_decimal(value) -> Decimal | None:
    if value in (None, "", "unknown"):
        return None
    try:
        parsed = Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        return None
    # OFF contains occasional absurd outliers; treat them as unusable rather
    # than letting them poison downstream aggregates.
    if parsed < 0 or parsed > Decimal("100000"):
        return None
    return parsed.quantize(Decimal("0.01"))


def _to_int(value) -> int | None:
    if value in (None, "", "unknown"):
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _tag_list(value) -> list:
    if isinstance(value, list):
        return [str(item) for item in value if item not in (None, "")]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _text(value, limit: int | None = None) -> str:
    if value in (None, ""):
        return ""
    text = str(value).strip()
    return text[:limit] if limit else text


def _last_modified(payload: dict):
    raw = payload.get("last_modified_t") or payload.get("last_modified_datetime")
    if raw in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(raw), tz=dt_timezone.utc)
    except (ValueError, TypeError, OSError):
        return None


def normalize_off_payload(payload: dict) -> dict | None:
    """Turn a raw OFF product record into OpenFoodFactsProduct field values.

    Returns None when the record has no usable barcode, which happens in the
    dumps more often than you would expect.
    """
    barcode = _text(payload.get("code") or payload.get("_id"))
    if not barcode or not barcode.strip("0"):
        return None

    nutriments = payload.get("nutriments") or {}
    fields = {
        "barcode": barcode[:64],
        "product_name": _text(payload.get("product_name"), 255),
        "brands": _text(payload.get("brands"), 255),
        "quantity": _text(payload.get("quantity"), 120),
        "serving_size": _text(payload.get("serving_size"), 120),
        "categories_tags": _tag_list(payload.get("categories_tags")),
        "countries_tags": _tag_list(payload.get("countries_tags")),
        "labels_tags": _tag_list(payload.get("labels_tags")),
        "additives_tags": _tag_list(payload.get("additives_tags")),
        "ingredients_text": _text(payload.get("ingredients_text")),
        "allergens_text": _text(payload.get("allergens") or payload.get("allergens_tags")),
        "nutriscore_grade": _text(payload.get("nutriscore_grade") or payload.get("nutrition_grades"), 5).upper(),
        "nova_group": _to_int(payload.get("nova_group")),
        "completeness": _to_decimal(payload.get("completeness")),
        "off_last_modified_at": _last_modified(payload),
    }

    for off_key, field_name in NUTRIMENT_FIELD_MAP.items():
        fields[field_name] = _to_decimal(nutriments.get(off_key))

    # OFF often has energy_100g in kJ without an explicit energy-kj_100g.
    if fields["energy_kj"] is None:
        unit = _text(nutriments.get("energy_unit")).lower()
        if unit in ("", "kj"):
            fields["energy_kj"] = _to_decimal(nutriments.get("energy_100g"))

    # Derive one from the other when only a single energy figure is present.
    if fields["energy_kcal"] is None and fields["energy_kj"] is not None:
        fields["energy_kcal"] = (fields["energy_kj"] / Decimal("4.184")).quantize(Decimal("0.01"))
    elif fields["energy_kj"] is None and fields["energy_kcal"] is not None:
        fields["energy_kj"] = (fields["energy_kcal"] * Decimal("4.184")).quantize(Decimal("0.01"))

    # Salt and sodium are related by a fixed factor; fill a missing one.
    if fields["salt_g"] is None and fields["sodium_g"] is not None:
        fields["salt_g"] = (fields["sodium_g"] * Decimal("2.5")).quantize(Decimal("0.01"))
    elif fields["sodium_g"] is None and fields["salt_g"] is not None:
        fields["sodium_g"] = (fields["salt_g"] / Decimal("2.5")).quantize(Decimal("0.01"))

    fields["content_hash"] = compute_content_hash(fields)
    return fields


def compute_content_hash(fields: dict) -> str:
    parts = []
    for name in HASHED_FIELDS:
        value = fields.get(name)
        parts.append("" if value is None else str(value))
    return sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def iter_jsonl(path: str | Path) -> Iterator[dict]:
    """Yield records from an OFF JSONL dump, transparently handling gzip."""
    file_path = Path(path)
    if not file_path.exists():
        raise OpenFoodFactsError(f"Dump file not found: {file_path}")

    opener = gzip.open if file_path.suffix == ".gz" else open
    with opener(file_path, "rt", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                # A single corrupt line should not abort a multi-million-row load.
                continue


def matches_country(payload_fields: dict, country_tag: str) -> bool:
    if not country_tag:
        return True
    return country_tag in payload_fields.get("countries_tags", [])


class OpenFoodFactsIngestor:
    """Upserts staged OFF records, skipping rows whose content has not changed."""

    def __init__(self, *, source: DataSource | None = None, store_raw: bool = False):
        self.source = source or get_off_source()
        self.store_raw = store_raw
        self.seen = 0
        self.created = 0
        self.updated = 0
        self.unchanged = 0
        self.skipped = 0

    def ingest(self, payloads: Iterable[dict], *, country_tag: str = "", batch_size: int = 500) -> dict:
        batch: list[dict] = []
        for payload in payloads:
            self.seen += 1
            fields = normalize_off_payload(payload)
            if fields is None or not matches_country(fields, country_tag):
                self.skipped += 1
                continue
            if self.store_raw:
                fields["raw_payload"] = payload
            batch.append(fields)
            if len(batch) >= batch_size:
                self._flush(batch)
                batch = []
        self._flush(batch)
        return self.stats()

    def _flush(self, batch: list[dict]) -> None:
        if not batch:
            return
        barcodes = [item["barcode"] for item in batch]
        existing = {
            row.barcode: row
            for row in OpenFoodFactsProduct.objects.filter(barcode__in=barcodes)
        }
        for fields in batch:
            current = existing.get(fields["barcode"])
            if current is None:
                OpenFoodFactsProduct.objects.create(source=self.source, **fields)
                self.created += 1
                continue
            if current.content_hash and current.content_hash == fields["content_hash"]:
                self.unchanged += 1
                continue
            for name, value in fields.items():
                setattr(current, name, value)
            current.source = self.source
            current.save()
            self.updated += 1

    def stats(self) -> dict:
        return {
            "seen": self.seen,
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "skipped": self.skipped,
            "finished_at": timezone.now().isoformat(),
        }
