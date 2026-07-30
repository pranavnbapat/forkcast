from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR

from catalog.models import Product, ProductSnapshot
from catalog.services.nutrition import to_decimal


@dataclass
class SelectedBuyProductInput:
    product: Product
    quantity: Decimal


def latest_snapshot_for_product(product: Product) -> ProductSnapshot | None:
    return product.snapshots.order_by("-scraped_at", "-id").first()


def build_price_profile(product: Product, quantity: Decimal) -> dict:
    snapshot = latest_snapshot_for_product(product)
    current_price = snapshot.price_amount if snapshot else None
    product_card = (snapshot.payload or {}).get("product_card", {}) if snapshot else {}
    regular_price = to_decimal(product_card.get("priceBeforeBonus")) or current_price
    is_bonus = bool(product_card.get("isBonus"))
    bonus_label = product_card.get("bonusMechanism") or ""
    current_total, pricing_note = calculate_current_total(
        quantity=quantity,
        current_price=current_price,
        regular_price=regular_price,
        product_card=product_card,
    )
    regular_total = regular_price * quantity if regular_price is not None else None
    savings_total = (
        regular_total - current_total
        if current_total is not None and regular_total is not None and regular_total >= current_total
        else Decimal("0")
    )

    return {
        "product": product,
        "quantity": quantity,
        "snapshot": snapshot,
        "current_price": current_price,
        "regular_price": regular_price,
        "is_bonus": is_bonus,
        "bonus_label": bonus_label,
        "pricing_note": pricing_note,
        "current_total": current_total,
        "regular_total": regular_total,
        "savings_total": savings_total,
    }


def aggregate_price_profiles(profiles: list[dict]) -> dict:
    current_total = Decimal("0")
    regular_total = Decimal("0")
    savings_total = Decimal("0")

    for profile in profiles:
        if profile["current_total"] is not None:
            current_total += profile["current_total"]
        if profile["regular_total"] is not None:
            regular_total += profile["regular_total"]
        if profile["savings_total"] is not None:
            savings_total += profile["savings_total"]

    return {
        "current_total": current_total,
        "regular_total": regular_total,
        "savings_total": savings_total,
    }


def calculate_current_total(
    *,
    quantity: Decimal,
    current_price: Decimal | None,
    regular_price: Decimal | None,
    product_card: dict,
) -> tuple[Decimal | None, str]:
    if current_price is None:
        return None, ""

    discount_labels = product_card.get("discountLabels") or []
    quantity_int = decimal_to_int_if_whole(quantity)
    if quantity_int is None:
        return current_price * quantity, ""

    for label in discount_labels:
        code = label.get("code")
        if code == "DISCOUNT_X_PLUS_Y_FREE" and regular_price is not None:
            buy_count = int(label.get("count") or 0)
            free_count = int(label.get("freeCount") or 0)
            group_size = buy_count + free_count
            if buy_count > 0 and free_count > 0 and group_size > 0:
                full_groups = quantity_int // group_size
                remainder = quantity_int % group_size
                payable_units = (full_groups * buy_count) + min(remainder, buy_count)
                total = regular_price * Decimal(payable_units)
                note = f"{buy_count}+{free_count} gratis toegepast"
                return total, note

        if code == "DISCOUNT_BUNDLE_BULK" and regular_price is not None:
            percentage = to_decimal(label.get("precisePercentage") or label.get("percentage"))
            if percentage is not None:
                discount_factor = (Decimal("100") - percentage) / Decimal("100")
                total = (regular_price * quantity * discount_factor).quantize(Decimal("0.01"))
                return total, f"{percentage}% volumevoordeel toegepast"

    return current_price * quantity, ""


def decimal_to_int_if_whole(value: Decimal) -> int | None:
    if value != value.to_integral_value(rounding=ROUND_FLOOR):
        return None
    return int(value)
