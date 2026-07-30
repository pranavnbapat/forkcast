from __future__ import annotations

from django.db.models import Q, QuerySet


NON_FOOD_NAME_TERMS = [
    "e-gift",
    "egift",
    "giftcard",
    "gift card",
    "cadeau",
    "cadeaubon",
    "euro",
    "stedentrip",
    "treinreis",
    "dagentree",
    "sauna",
    "ticket",
    "beschermmat",
    "matras",
    "pocket",
    "body worlds",
]


def non_food_q() -> Q:
    query = Q()
    for term in NON_FOOD_NAME_TERMS:
        query |= Q(name__icontains=term)
    return query


def food_candidate_queryset(queryset: QuerySet) -> QuerySet:
    return queryset.exclude(non_food_q())


def is_food_candidate(product) -> bool:
    name = (getattr(product, "name", "") or "").lower()
    return not any(term in name for term in NON_FOOD_NAME_TERMS)
