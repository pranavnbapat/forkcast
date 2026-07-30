from __future__ import annotations

import json
from typing import Any

import requests
from django.conf import settings
from django.db.models import QuerySet

from catalog.models import Product
from catalog.services.pricing import latest_snapshot_for_product


class OpenSearchError(Exception):
    pass


class ProductOpenSearchIndex:
    def __init__(self):
        self.enabled = settings.OPENSEARCH_ENABLED
        self.base_url = settings.OPENSEARCH_URL.rstrip("/")
        self.index_name = settings.OPENSEARCH_INDEX_NAME
        self.username = settings.OPENSEARCH_USERNAME
        self.password = settings.OPENSEARCH_PASSWORD
        self.verify = settings.OPENSEARCH_VERIFY_SSL
        self.timeout = settings.OPENSEARCH_TIMEOUT
        self.auto_index_on_save = settings.OPENSEARCH_AUTO_INDEX_ON_SAVE

    def is_configured(self) -> bool:
        return self.enabled and bool(self.base_url and self.index_name)

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json"}

    def _auth(self):
        if self.username:
            return (self.username, self.password)
        return None

    def _request(self, method: str, path: str, *, json_payload: dict[str, Any] | None = None):
        if not self.is_configured():
            raise OpenSearchError("OpenSearch is not configured.")
        response = requests.request(
            method=method,
            url=f"{self.base_url}/{path.lstrip('/')}",
            headers=self._headers(),
            json=json_payload,
            auth=self._auth(),
            timeout=self.timeout,
            verify=self.verify,
        )
        if response.status_code >= 400:
            raise OpenSearchError(f"OpenSearch request failed: HTTP {response.status_code} {response.text[:500]}")
        if not response.text:
            return {}
        return response.json()

    def ensure_index(self):
        if not self.is_configured():
            return
        mappings = {
            "settings": {
                "analysis": {
                    "filter": {
                        "product_edge_ngram": {"type": "edge_ngram", "min_gram": 2, "max_gram": 20},
                    },
                    "analyzer": {
                        "autocomplete_index": {
                            "type": "custom",
                            "tokenizer": "standard",
                            "filter": ["lowercase", "asciifolding", "product_edge_ngram"],
                        },
                        "autocomplete_search": {
                            "type": "custom",
                            "tokenizer": "standard",
                            "filter": ["lowercase", "asciifolding"],
                        },
                    },
                }
            },
            "mappings": {
                "properties": {
                    "id": {"type": "integer"},
                    "name": {
                        "type": "text",
                        "analyzer": "autocomplete_index",
                        "search_analyzer": "autocomplete_search",
                        "fields": {"raw": {"type": "keyword"}},
                    },
                    "brand": {
                        "type": "text",
                        "analyzer": "autocomplete_index",
                        "search_analyzer": "autocomplete_search",
                    },
                    "package_size": {"type": "text"},
                    "description": {"type": "text"},
                    "category_name": {"type": "keyword"},
                    "subcategory_name": {"type": "keyword"},
                    "image_url": {"type": "keyword"},
                    "nutri_score_grade": {"type": "keyword"},
                    "has_nutrition": {"type": "boolean"},
                    "price_amount": {"type": "float"},
                    "is_bonus": {"type": "boolean"},
                    "search_blob": {
                        "type": "text",
                        "analyzer": "autocomplete_index",
                        "search_analyzer": "autocomplete_search",
                    },
                }
            },
        }
        exists = requests.head(
            f"{self.base_url}/{self.index_name}",
            auth=self._auth(),
            timeout=self.timeout,
            verify=self.verify,
        )
        if exists.status_code == 200:
            return
        if exists.status_code not in (404,):
            raise OpenSearchError(f"OpenSearch HEAD failed: HTTP {exists.status_code} {exists.text[:500]}")
        self._request("PUT", self.index_name, json_payload=mappings)

    def recreate_index(self):
        if not self.is_configured():
            return
        response = requests.delete(
            f"{self.base_url}/{self.index_name}",
            auth=self._auth(),
            timeout=self.timeout,
            verify=self.verify,
        )
        if response.status_code not in (200, 404):
            raise OpenSearchError(f"OpenSearch delete index failed: HTTP {response.status_code} {response.text[:500]}")
        self.ensure_index()

    def document_for_product(self, product: Product) -> dict[str, Any]:
        snapshot = latest_snapshot_for_product(product)
        product_card = (snapshot.payload or {}).get("product_card", {}) if snapshot else {}
        facts = getattr(product, "nutrition_facts", None)
        quality = product.quality_profiles.filter(source_type="llm").order_by("-last_generated_at", "-id").first()
        return {
            "id": product.id,
            "name": product.name,
            "brand": product.brand,
            "source_url": product.source_url,
            "external_id": product.external_id,
            "package_size": product.package_size,
            "description": product.description,
            "ingredients": product.ingredients,
            "allergen_info": product.allergen_info,
            "category_name": product.category_name,
            "subcategory_name": product.subcategory_name,
            "image_url": product.image_url,
            "nutri_score_grade": product.nutri_score_grade,
            "has_nutrition": bool(getattr(product, "has_nutrition_entries", False) or getattr(product, "nutrition_facts_id", None)),
            "price_amount": float(snapshot.price_amount) if snapshot and snapshot.price_amount is not None else None,
            "price_text": snapshot.price_text if snapshot else "",
            "price_before_bonus": product_card.get("priceBeforeBonus"),
            "is_bonus": bool(product_card.get("isBonus")),
            "bonus_mechanism": product_card.get("bonusMechanism", ""),
            "nutrition_summary": {
                "energy_kj": float(facts.energy_kj) if facts and facts.energy_kj is not None else None,
                "energy_kcal": float(facts.energy_kcal) if facts and facts.energy_kcal is not None else None,
                "estimated_energy_kcal": float(facts.estimated_energy_kcal)
                if facts and facts.estimated_energy_kcal is not None
                else None,
                "protein_g": float(facts.protein_g) if facts and facts.protein_g is not None else None,
                "fat_g": float(facts.fat_g) if facts and facts.fat_g is not None else None,
                "saturates_g": float(facts.saturates_g) if facts and facts.saturates_g is not None else None,
                "unsaturated_g": float(facts.unsaturated_g) if facts and facts.unsaturated_g is not None else None,
                "carbohydrates_g": float(facts.carbohydrates_g) if facts and facts.carbohydrates_g is not None else None,
                "sugars_g": float(facts.sugars_g) if facts and facts.sugars_g is not None else None,
                "fiber_g": float(facts.fiber_g) if facts and facts.fiber_g is not None else None,
                "starch_g": float(facts.starch_g) if facts and facts.starch_g is not None else None,
                "salt_g": float(facts.salt_g) if facts and facts.salt_g is not None else None,
                "balanced_score": float(facts.balanced_score) if facts and facts.balanced_score is not None else None,
            },
            "quality_profile": {
                "source_name": quality.source_name if quality else "",
                "confidence_label": quality.confidence_label if quality else "",
                "ambient_days_min": quality.ambient_days_min if quality else None,
                "ambient_days_max": quality.ambient_days_max if quality else None,
                "refrigerated_days_min": quality.refrigerated_days_min if quality else None,
                "refrigerated_days_max": quality.refrigerated_days_max if quality else None,
                "frozen_days_min": quality.frozen_days_min if quality else None,
                "frozen_days_max": quality.frozen_days_max if quality else None,
                "spoilage_summary": quality.spoilage_summary if quality else "",
                "odor_notes": quality.odor_notes if quality else "",
                "color_change_notes": quality.color_change_notes if quality else "",
                "texture_change_notes": quality.texture_change_notes if quality else "",
                "storage_notes": quality.storage_notes if quality else "",
            },
            "search_blob": " ".join(
                part
                for part in [
                    product.name,
                    product.brand,
                    product.package_size,
                    product.description,
                    product.ingredients,
                    product.category_name,
                    product.subcategory_name,
                    quality.spoilage_summary if quality else "",
                    quality.odor_notes if quality else "",
                ]
                if part
            ),
        }

    def index_products(self, products: list[Product]):
        if not products:
            return 0
        self.ensure_index()
        lines: list[str] = []
        for product in products:
            lines.append(json.dumps({"index": {"_index": self.index_name, "_id": product.id}}))
            lines.append(json.dumps(self.document_for_product(product), ensure_ascii=True))
        payload = "\n".join(lines) + "\n"
        response = requests.post(
            f"{self.base_url}/_bulk",
            data=payload,
            headers={"Content-Type": "application/x-ndjson"},
            auth=self._auth(),
            timeout=self.timeout,
            verify=self.verify,
        )
        if response.status_code >= 400:
            raise OpenSearchError(f"OpenSearch bulk index failed: HTTP {response.status_code} {response.text[:500]}")
        data = response.json()
        if data.get("errors"):
            raise OpenSearchError(f"OpenSearch bulk index reported errors: {response.text[:500]}")
        return len(products)

    def bulk_index_queryset(self, queryset: QuerySet, *, batch_size: int = 1000) -> int:
        total = 0
        start = 0
        while True:
            batch = list(queryset[start : start + batch_size])
            if not batch:
                break
            total += self.index_products(batch)
            start += batch_size
        return total

    def delete_product(self, product_id: int):
        if not self.is_configured():
            return
        response = requests.delete(
            f"{self.base_url}/{self.index_name}/_doc/{product_id}",
            auth=self._auth(),
            timeout=self.timeout,
            verify=self.verify,
        )
        if response.status_code not in (200, 404):
            raise OpenSearchError(f"OpenSearch delete failed: HTTP {response.status_code} {response.text[:500]}")

    def search(self, query: str, *, size: int = 12) -> list[dict[str, Any]]:
        self.ensure_index()
        payload = {
            "size": size,
            "query": {
                "bool": {
                    "should": [
                        {
                            "multi_match": {
                                "query": query,
                                "fields": ["name^6", "brand^3", "package_size^2", "description", "search_blob^4"],
                                "type": "best_fields",
                                "fuzziness": "AUTO",
                            }
                        },
                        {
                            "multi_match": {
                                "query": query,
                                "fields": ["name^8", "brand^3", "search_blob^5"],
                                "type": "bool_prefix",
                            }
                        },
                    ],
                    "minimum_should_match": 1,
                }
            },
            "sort": [
                {"has_nutrition": {"order": "desc"}},
                {"_score": {"order": "desc"}},
                {"name.raw": {"order": "asc", "missing": "_last"}},
            ],
        }
        data = self._request("POST", f"{self.index_name}/_search", json_payload=payload)
        hits = data.get("hits", {}).get("hits", [])
        return [hit.get("_source", {}) for hit in hits]

    def status(self) -> dict[str, Any]:
        info = {
            "enabled": self.enabled,
            "configured": self.is_configured(),
            "url": self.base_url,
            "index_name": self.index_name,
            "reachable": False,
            "index_exists": False,
            "document_count": None,
            "error": "",
        }
        if not self.is_configured():
            return info
        try:
            cluster = requests.get(
                f"{self.base_url}/",
                auth=self._auth(),
                timeout=self.timeout,
                verify=self.verify,
            )
            if cluster.status_code >= 400:
                info["error"] = f"HTTP {cluster.status_code} {cluster.text[:200]}"
                return info
            info["reachable"] = True

            exists = requests.head(
                f"{self.base_url}/{self.index_name}",
                auth=self._auth(),
                timeout=self.timeout,
                verify=self.verify,
            )
            if exists.status_code == 200:
                info["index_exists"] = True
                count_response = requests.get(
                    f"{self.base_url}/{self.index_name}/_count",
                    auth=self._auth(),
                    timeout=self.timeout,
                    verify=self.verify,
                )
                if count_response.status_code < 400:
                    count_data = count_response.json()
                    info["document_count"] = count_data.get("count")
                else:
                    info["error"] = f"Count failed: HTTP {count_response.status_code} {count_response.text[:200]}"
            elif exists.status_code != 404:
                info["error"] = f"Index HEAD failed: HTTP {exists.status_code} {exists.text[:200]}"
            return info
        except requests.RequestException as exc:
            info["error"] = str(exc)
            return info
