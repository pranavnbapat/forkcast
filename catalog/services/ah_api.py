import json
import os
import string
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from urllib.parse import parse_qs, quote, urlparse

import requests
from bs4 import BeautifulSoup
from django.utils import timezone
from django.utils.text import slugify

from catalog.models import CategoryScope, CrawlSource, NutritionEntry, NutritionFacts, Product, ProductSnapshot, Supermarket
from catalog.services.diet_metrics import derive_diet_metrics


API_BASE_URL = "https://api.ah.nl"


@dataclass
class DiscoveryResult:
    discovered_products: int
    discovered_sources: int
    visited_pages: int
    failed_pages: int


class AHAPIError(Exception):
    pass


class AHAPIImporter:
    def __init__(self, supermarket: Supermarket | None = None):
        self.supermarket = supermarket or Supermarket.objects.get(slug="albert-heijn")
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": os.getenv(
                    "AH_API_USER_AGENT",
                    "Appie/9.28 (iPhone17,3; iPhone; CPU OS 26_1 like Mac OS X)",
                ),
                "x-client-name": os.getenv("AH_API_CLIENT_NAME", "appie-ios"),
                "x-client-version": os.getenv("AH_API_CLIENT_VERSION", "9.28"),
                "x-application": "AHWEBSHOP",
                "Accept": "application/json",
                "Content-Type": "application/json",
            }
        )
        self.timeout_seconds = int(os.getenv("AH_TIMEOUT", "20"))
        self.max_retries = int(os.getenv("AH_API_MAX_RETRIES", "3"))
        self.retry_backoff_seconds = float(os.getenv("AH_API_RETRY_BACKOFF_SECONDS", "2"))
        self._access_token = ""
        self._refresh_token = ""
        self._allowed_main_categories = None

    def close(self):
        self.session.close()

    def authenticate_anonymous(self):
        if self._access_token:
            return
        payload = {"clientId": self.session.headers["x-client-name"]}
        response = self._request_with_retry(
            "POST",
            f"{API_BASE_URL}/mobile-auth/v1/auth/token/anonymous",
            json=payload,
            context="anonymous token",
        )
        data = response.json()
        self._access_token = data["access_token"]
        self._refresh_token = data.get("refresh_token", "")
        self.session.headers["Authorization"] = f"Bearer {self._access_token}"

    def request_json(self, method: str, path: str, *, body: dict | None = None) -> dict | list:
        self.authenticate_anonymous()
        url = path if path.startswith("http") else f"{API_BASE_URL}{path}"
        response = self._request_with_retry(method, url, json=body, context=path)
        return response.json()

    def _request_with_retry(self, method: str, url: str, *, json: dict | None = None, context: str) -> requests.Response:
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.session.request(method, url, json=json, timeout=self.timeout_seconds)
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise AHAPIError(f"AH API {context} failed after {attempt} attempts: {exc}") from exc
                time.sleep(self.retry_backoff_seconds * attempt)
                continue

            if response.status_code < 400:
                return response

            retryable = response.status_code in {429, 500, 502, 503, 504, 552}
            if retryable and attempt < self.max_retries:
                last_error = AHAPIError(f"HTTP {response.status_code} {response.text[:300]}")
                time.sleep(self.retry_backoff_seconds * attempt)
                continue
            self.raise_for_status(response, context)

        if last_error:
            raise AHAPIError(f"AH API {context} failed: {last_error}")
        raise AHAPIError(f"AH API {context} failed without a response.")

    def raise_for_status(self, response: requests.Response, context: str):
        if response.status_code >= 400:
            raise AHAPIError(f"AH API {context} failed: HTTP {response.status_code} {response.text[:300]}")

    def search_products(
        self,
        query: str,
        page: int,
        size: int,
        sort_on: str = "RELEVANCE",
        taxonomy_id: int | None = None,
    ) -> dict:
        path = "/mobile-services/product/search/v2"
        query_string = f"?query={quote(query)}&page={page}&size={size}&sortOn={quote(sort_on)}"
        if taxonomy_id is not None:
            query_string += f"&taxonomyId={taxonomy_id}"
        path += query_string
        return self.request_json("GET", path)

    def get_product_detail(self, product_id: int) -> dict:
        data = self.request_json("GET", f"/mobile-services/product/detail/v4/fir/{product_id}")
        return data["productCard"]

    def get_product_nutrition(self, product_id: int) -> list[dict]:
        query = """
        query FetchProduct($productId: Int!) {
          product(id: $productId) {
            id
            tradeItem {
              nutritions {
                nutrients {
                  type
                  name
                  value
                }
              }
            }
          }
        }
        """
        data = self.request_json(
            "POST",
            "/graphql",
            body={"query": query, "variables": {"productId": product_id}},
        )
        trade_item = (((data.get("data") or {}).get("product") or {}).get("tradeItem") or {})
        nutritions = trade_item.get("nutritions") or []
        if not nutritions:
            return []
        return nutritions[0].get("nutrients") or []

    def get_bonus_metadata(self) -> dict:
        return self.request_json("GET", "/mobile-services/bonuspage/v3/metadata")

    def get_bonus_section(self, api_path: str) -> dict:
        parsed = urlparse(api_path)
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        if not path.startswith("/mobile-services/"):
            path = f"/mobile-services/{path.lstrip('/')}"
        return self.request_json("GET", path)

    def discover_from_sources(self, sources, max_pages: int = 10, create_sources: bool = True) -> DiscoveryResult:
        discovered_products = 0
        discovered_sources = 0
        visited_pages = 0
        failed_pages = 0

        for source in sources:
            try:
                if source.source_type == CrawlSource.SourceType.CATALOG or source.url.endswith("/producten"):
                    result = self.import_search_pages(query="", start_page=0, max_pages=max_pages, page_size=100)
                    discovered_products += result["imported_products"]
                    visited_pages += result["visited_pages"]
                else:
                    metadata = self.get_bonus_metadata()
                    visited_pages += 1
                    if create_sources:
                        for period in metadata.get("periods", []):
                            for tab in period.get("tabs", []):
                                for item in tab.get("urlMetadataList", []):
                                    api_url = self.to_api_url(item["url"])
                                    _, created = CrawlSource.objects.get_or_create(
                                        supermarket=self.supermarket,
                                        url=api_url,
                                        defaults={
                                            "name": item.get("description") or tab.get("description") or api_url,
                                            "source_type": CrawlSource.SourceType.BONUS,
                                        },
                                    )
                                    discovered_sources += int(created)

                    api_url = self.to_api_url(source.url)
                    if "bonuspage/v3/metadata" in api_url or source.url.endswith("/bonus"):
                        metadata = self.get_bonus_metadata()
                        for period in metadata.get("periods", []):
                            for tab in period.get("tabs", []):
                                for item in tab.get("urlMetadataList", [])[:max_pages]:
                                    section = self.get_bonus_section(self.to_api_url(item["url"]))
                                    visited_pages += 1
                                    discovered_products += self.sync_bonus_section(section)
                    else:
                        section = self.get_bonus_section(api_url)
                        visited_pages += 1
                        discovered_products += self.sync_bonus_section(section)

                source.last_crawled_at = timezone.now()
                source.last_error = ""
                source.save(update_fields=["last_crawled_at", "last_error", "updated_at"])
            except AHAPIError as exc:
                failed_pages += 1
                source.last_crawled_at = timezone.now()
                source.last_error = str(exc)
                source.save(update_fields=["last_crawled_at", "last_error", "updated_at"])

        return DiscoveryResult(
            discovered_products=discovered_products,
            discovered_sources=discovered_sources,
            visited_pages=visited_pages,
            failed_pages=failed_pages,
        )

    def import_search_pages(
        self,
        query: str,
        start_page: int = 0,
        max_pages: int = 10,
        page_size: int = 100,
        sort_on: str = "RELEVANCE",
        taxonomy_id: int | None = None,
        target_category_name: str | None = None,
    ) -> dict:
        imported_products = 0
        visited_pages = 0
        for page in range(start_page, start_page + max_pages):
            data = self.search_products(
                query=query,
                page=page,
                size=page_size,
                sort_on=sort_on,
                taxonomy_id=taxonomy_id,
            )
            visited_pages += 1
            for card in data.get("products", []):
                product = self.sync_product_card(
                    card,
                    load_detail=False,
                    load_nutrition=False,
                    target_category_name=target_category_name,
                )
                imported_products += int(product is not None)
            page_info = data.get("page") or {}
            total_pages = page_info.get("totalPages") or 0
            if total_pages and page + 1 >= total_pages:
                break
        return {"imported_products": imported_products, "visited_pages": visited_pages}

    def sync_bonus_section(self, section: dict) -> int:
        count = 0
        for item in section.get("bonusGroupOrProducts", []):
            product = item.get("product")
            if product:
                count += int(self.sync_product_card(product, load_detail=False, load_nutrition=False) is not None)
                continue
            bonus_group = item.get("bonusGroup") or {}
            for grouped_product in bonus_group.get("products", []):
                count += int(self.sync_product_card(grouped_product, load_detail=False, load_nutrition=False) is not None)
        return count

    def sync_product_from_url(self, source_url: str) -> Product:
        product_id = self.parse_product_id_from_url(source_url)
        if product_id is None:
            raise AHAPIError(f"Could not extract AH product id from URL: {source_url}")
        return self.sync_product_by_id(product_id)

    def sync_product_by_id(self, product_id: int) -> Product:
        detail = self.get_product_detail(product_id)
        nutrition = self.get_product_nutrition(product_id)
        return self.sync_product_card(detail, load_detail=False, load_nutrition=False, nutrition_rows=nutrition)

    def sync_product_card(
        self,
        card: dict,
        *,
        load_detail: bool = True,
        load_nutrition: bool = True,
        nutrition_rows: list[dict] | None = None,
        target_category_name: str | None = None,
    ) -> Product:
        product_id = int(card["webshopId"])
        detail = card
        if load_detail:
            detail = self.get_product_detail(product_id)
        main_category = (detail.get("mainCategory") or card.get("mainCategory") or "").strip()
        if main_category and not self.is_allowed_main_category(main_category):
            return None
        if target_category_name and main_category and not self.matches_category_name(main_category, target_category_name):
            return None
        attempted_nutrition = load_nutrition or nutrition_rows is not None
        if nutrition_rows is None and load_nutrition:
            nutrition_rows = self.get_product_nutrition(product_id)
        nutrition_rows = nutrition_rows or []

        source_url = self.build_product_url(product_id, detail.get("title", ""))
        product = Product.objects.filter(
            supermarket=self.supermarket,
            external_id=f"wi{product_id}",
        ).first()
        if product is None:
            product = Product.objects.filter(
                supermarket=self.supermarket,
                source_url=source_url,
            ).first()
        if product is None:
            product = Product(
                supermarket=self.supermarket,
                source_url=source_url,
                external_id=f"wi{product_id}",
                name=detail.get("title", f"wi{product_id}"),
            )
        product.name = detail.get("title") or product.name
        product.brand = detail.get("brand", "") or ""
        product.external_id = f"wi{product_id}"
        product.source_url = source_url
        product.category_name = main_category
        product.subcategory_name = (detail.get("subCategory") or card.get("subCategory") or "").strip()
        product.package_size = detail.get("salesUnitSize", "") or ""
        product.description = self.html_to_text(detail.get("descriptionFull") or detail.get("descriptionHighlights") or "")
        product.ingredients = ""
        product.allergen_info = self.build_allergen_info(detail.get("properties") or {})
        product.image_url = self.pick_image_url(detail.get("images") or [])
        nutri_score_grade = (detail.get("nutriscore") or self.get_property_value(detail.get("properties") or {}, "nutriscore"))
        product.nutri_score_grade = nutri_score_grade or ""
        product.nutri_score_label = f"{nutri_score_grade} nutri-score" if nutri_score_grade else ""
        product.last_scraped_at = timezone.now()
        if attempted_nutrition:
            product.nutrition_last_attempted_at = timezone.now()
            product.nutrition_unavailable = not bool(nutrition_rows)
        product.save()

        if nutrition_rows:
            nutrition_raw_text = json.dumps(nutrition_rows, ensure_ascii=False, sort_keys=True)
            nutrition_defaults = {
                "serving_size": detail.get("salesUnitSize", "") or "",
                "declaration_basis": "",
                "amount_column_label": "",
                "reference_intake_column_label": "",
                "reference_intake_note": "",
                "energy_kj": self.extract_energy(nutrition_rows, "kJ"),
                "energy_kcal": self.extract_energy(nutrition_rows, "kcal"),
                "fat_g": self.extract_decimal_by_label(nutrition_rows, "Vet"),
                "saturates_g": self.extract_decimal_by_label(nutrition_rows, "waarvan verzadigd"),
                "carbohydrates_g": self.extract_decimal_by_label(nutrition_rows, "Koolhydraten"),
                "sugars_g": self.extract_decimal_by_label(nutrition_rows, "waarvan suikers"),
                "fiber_g": self.extract_decimal_by_label(nutrition_rows, "Voedingsvezel"),
                "protein_g": self.extract_decimal_by_label(nutrition_rows, "Eiwitten"),
                "salt_g": self.extract_decimal_by_label(nutrition_rows, "Zout"),
                "raw_text": nutrition_raw_text,
            }
            nutrition_defaults.update(
                derive_diet_metrics(
                    fat_g=nutrition_defaults["fat_g"],
                    saturates_g=nutrition_defaults["saturates_g"],
                    carbohydrates_g=nutrition_defaults["carbohydrates_g"],
                    sugars_g=nutrition_defaults["sugars_g"],
                    fiber_g=nutrition_defaults["fiber_g"],
                    protein_g=nutrition_defaults["protein_g"],
                )
            )
            nutrition_facts, created = NutritionFacts.objects.get_or_create(
                product=product,
                defaults=nutrition_defaults,
            )
            had_same_raw_text = nutrition_facts.raw_text == nutrition_raw_text
            if not created:
                for field_name, field_value in nutrition_defaults.items():
                    setattr(nutrition_facts, field_name, field_value)
                nutrition_facts.save()
            if created or not had_same_raw_text or nutrition_facts.entries.count() != len(nutrition_rows):
                nutrition_facts.entries.all().delete()
                NutritionEntry.objects.bulk_create(
                    [
                        NutritionEntry(
                            nutrition_facts=nutrition_facts,
                            position=index,
                            label=row.get("name", ""),
                            value_text=row.get("value", ""),
                            reference_intake_text="",
                        )
                        for index, row in enumerate(nutrition_rows, start=1)
                    ]
                )
            if product.nutrition_unavailable:
                product.nutrition_unavailable = False
                product.save(update_fields=["nutrition_unavailable", "updated_at"])

        price_now = detail.get("currentPrice")
        if price_now is None:
            price_now = detail.get("priceBeforeBonus")
        snapshot_payload = {
            "product_card": detail,
            "nutrition_rows": nutrition_rows,
        }
        payload_signature = self.snapshot_signature(snapshot_payload)
        price_amount = self.to_decimal(price_now)
        price_text = str(price_now) if price_now is not None else ""
        latest_snapshot = product.snapshots.order_by("-scraped_at", "-id").first()
        latest_signature = self.snapshot_signature(latest_snapshot.payload) if latest_snapshot else ""
        if (
            latest_snapshot is None
            or latest_snapshot.price_amount != price_amount
            or latest_snapshot.price_text != price_text
            or latest_signature != payload_signature
        ):
            ProductSnapshot.objects.create(
                product=product,
                price_amount=price_amount,
                price_text=price_text,
                payload=snapshot_payload,
                scraped_at=timezone.now(),
            )
        return product

    def is_allowed_main_category(self, main_category: str) -> bool:
        if self._allowed_main_categories is None:
            self._allowed_main_categories = set(
                CategoryScope.objects.filter(is_food=True, is_active=True).values_list("name", flat=True)
            )
        if not self._allowed_main_categories:
            return True
        return any(self.matches_category_name(main_category, allowed) for allowed in self._allowed_main_categories)

    def matches_category_name(self, left: str, right: str) -> bool:
        return self.normalize_category_name(left) == self.normalize_category_name(right)

    def normalize_category_name(self, value: str) -> str:
        normalized = (value or "").strip().lower()
        replacements = {
            "&": "en",
            "/": " ",
            "-": " ",
        }
        for source, replacement in replacements.items():
            normalized = normalized.replace(source, replacement)
        return " ".join(normalized.split())

    def snapshot_signature(self, payload: dict | list | None) -> str:
        normalized = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
        return sha256(normalized.encode("utf-8")).hexdigest()

    def build_product_url(self, product_id: int, title: str) -> str:
        slug = slugify(title or "") or ""
        if slug:
            return f"https://www.ah.nl/producten/product/wi{product_id}/{slug}"
        return f"https://www.ah.nl/producten/product/wi{product_id}"

    def parse_product_id_from_url(self, source_url: str) -> int | None:
        path = urlparse(source_url).path
        for part in path.split("/"):
            if part.startswith("wi") and part[2:].isdigit():
                return int(part[2:])
        return None

    def to_api_url(self, source_url: str) -> str:
        if source_url.startswith(API_BASE_URL):
            return source_url
        parsed = urlparse(source_url)
        if parsed.netloc == "www.ah.nl":
            if parsed.path == "/bonus":
                return f"{API_BASE_URL}/mobile-services/bonuspage/v3/metadata"
            return f"{API_BASE_URL}{parsed.path}"
        if source_url.startswith("bonuspage/"):
            return f"{API_BASE_URL}/mobile-services/{source_url}"
        return source_url

    def build_allergen_info(self, properties: dict) -> str:
        values = []
        for key, entries in sorted(properties.items()):
            if not key.startswith("sp_include_intolerance_"):
                continue
            values.extend(entries)
        return "\n".join(values)

    def get_property_value(self, properties: dict, key: str) -> str:
        values = properties.get(key) or []
        return values[0] if values else ""

    def pick_image_url(self, images: list[dict]) -> str:
        if not images:
            return ""
        best = sorted(images, key=lambda item: item.get("width", 0), reverse=True)[0]
        return best.get("url", "")

    def html_to_text(self, html: str) -> str:
        if not html:
            return ""
        return BeautifulSoup(html, "html.parser").get_text("\n", strip=True)

    def extract_decimal_by_label(self, rows: list[dict], label: str) -> Decimal | None:
        for row in rows:
            if row.get("name", "").strip().lower() != label.lower():
                continue
            return self.to_decimal(row.get("value"))
        return None

    def extract_energy(self, rows: list[dict], unit: str) -> Decimal | None:
        for row in rows:
            if row.get("name") != "Energie":
                continue
            value = row.get("value", "")
            if unit == "kJ" and "kJ" in value:
                return self.to_decimal(value.split("kJ")[0])
            if unit == "kcal":
                start = value.find("(")
                end = value.find("kcal")
                if start != -1 and end != -1:
                    return self.to_decimal(value[start + 1 : end])
        return None

    def to_decimal(self, value) -> Decimal | None:
        if value is None:
            return None
        text = str(value).strip()
        number = []
        seen_digit = False
        for char in text:
            if char.isdigit():
                number.append(char)
                seen_digit = True
                continue
            if char in ",." and seen_digit:
                number.append("." if char == "," else char)
                continue
            if seen_digit:
                break
        if not number:
            return None
        normalized = "".join(number)
        try:
            return Decimal(normalized)
        except InvalidOperation:
            return None

    def import_query_partitions(
        self,
        *,
        partitions: list[str],
        max_pages_per_partition: int = 25,
        page_size: int = 100,
        sort_on: str = "RELEVANCE",
    ) -> dict:
        total_imported = 0
        total_pages = 0
        results = []
        for partition in partitions:
            result = self.import_search_pages(
                query=partition,
                start_page=0,
                max_pages=max_pages_per_partition,
                page_size=page_size,
                sort_on=sort_on,
            )
            result["query"] = partition
            results.append(result)
            total_imported += result["imported_products"]
            total_pages += result["visited_pages"]
        return {
            "partitions": results,
            "total_imported": total_imported,
            "total_pages": total_pages,
        }

    @staticmethod
    def default_single_char_partitions() -> list[str]:
        return list(string.ascii_lowercase) + list(string.digits)
