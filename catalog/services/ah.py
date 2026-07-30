import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from django.utils import timezone
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from catalog.models import (
    CrawlSource,
    NutritionEntry,
    NutritionFacts,
    Product,
    ProductSnapshot,
    Supermarket,
)


BASE_URL = "https://www.ah.nl"
PRODUCT_PATH_RE = re.compile(r"^/producten/product/(wi[^/?#]+)(?:/[^?#]+)?/?$")
PRICE_RE = re.compile(r"€\s*\d+(?:[.,]\d{1,2})?")
NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


@dataclass
class DiscoveryResult:
    discovered_products: int
    discovered_sources: int
    visited_pages: int
    failed_pages: int


class AHScraperError(Exception):
    pass


class AHScraper:
    def __init__(self, supermarket: Supermarket | None = None):
        os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
        self.supermarket = supermarket or Supermarket.objects.get(slug="albert-heijn")
        self.user_agent = os.getenv(
            "AH_USER_AGENT",
            (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36"
            ),
        )
        self.cookie_header = os.getenv("AH_COOKIE", "").strip()
        self.delay_seconds = float(os.getenv("AH_REQUEST_DELAY", "0.3"))
        self.timeout_seconds = int(os.getenv("AH_TIMEOUT", "20"))
        self.headless = os.getenv("AH_BROWSER_HEADLESS", "1") == "1"
        self.browser_path = os.getenv("AH_BROWSER_EXECUTABLE_PATH", "/usr/bin/google-chrome")
        self.browser_user_data_dir = os.getenv("AH_BROWSER_USER_DATA_DIR", "").strip()
        self.browser_profile_dir = os.getenv("AH_BROWSER_PROFILE_DIR", "Default").strip()
        self.browser_cloned_user_data_dir = os.getenv(
            "AH_BROWSER_CLONED_USER_DATA_DIR",
            "/tmp/ah-playwright-profile",
        ).strip()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def fetch(self, url: str) -> str:
        try:
            page = self.get_page()
            response = page.goto(url, wait_until="load", timeout=self.timeout_seconds * 1000)
            page.wait_for_timeout(int(self.delay_seconds * 1000) + 500)
        except PlaywrightError as exc:
            raise AHScraperError(
                f"Albert Heijn browser request failed for {url}: {exc}. "
                "You can set AH_COOKIE if your normal browser session is required."
            ) from exc
        if response is None:
            raise AHScraperError(f"Albert Heijn did not return a response for {url}.")
        if response.status >= 400:
            raise AHScraperError(
                f"Albert Heijn request failed for {url}: HTTP {response.status}. "
                "You can set AH_COOKIE if your normal browser session is required."
            )

        text = page.content()
        if "Access Denied" in text and "Reference #" in text:
            raise AHScraperError(
                "Albert Heijn blocked the automated browser session. "
                "Set AH_COOKIE from your own browser session if needed."
            )
        time.sleep(self.delay_seconds)
        return text

    def get_page(self):
        if self._page is not None:
            return self._page

        self._playwright = sync_playwright().start()
        browser_args = [
            "--disable-blink-features=AutomationControlled",
            "--lang=nl-NL",
            "--window-size=1440,1400",
            "--no-sandbox",
        ]
        if self.browser_user_data_dir:
            launch_user_data_dir = self.prepare_cloned_user_data_dir()
            self._context = self._playwright.chromium.launch_persistent_context(
                user_data_dir=launch_user_data_dir,
                executable_path=self.browser_path,
                headless=self.headless,
                args=browser_args + [f"--profile-directory={self.browser_profile_dir}"],
                user_agent=self.user_agent,
                locale="nl-NL",
                viewport={"width": 1440, "height": 1400},
            )
            pages = self._context.pages
            self._page = pages[0] if pages else self._context.new_page()
        else:
            self._browser = self._playwright.chromium.launch(
                executable_path=self.browser_path,
                headless=self.headless,
                args=browser_args,
            )
            self._context = self._browser.new_context(
                user_agent=self.user_agent,
                locale="nl-NL",
                viewport={"width": 1440, "height": 1400},
            )
            if self.cookie_header:
                self._context.add_cookies(self.parse_cookie_header(self.cookie_header))
            self._page = self._context.new_page()
        return self._page

    def close(self):
        if self._page is not None:
            self._page.close()
            self._page = None
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None

    def prepare_cloned_user_data_dir(self) -> str:
        source_root = Path(self.browser_user_data_dir)
        target_root = Path(self.browser_cloned_user_data_dir)
        source_profile = source_root / self.browser_profile_dir
        target_profile = target_root / self.browser_profile_dir

        if target_root.exists():
            shutil.rmtree(target_root)
        target_root.mkdir(parents=True, exist_ok=True)

        local_state = source_root / "Local State"
        if local_state.exists():
            shutil.copy2(local_state, target_root / "Local State")

        shutil.copytree(
            source_profile,
            target_profile,
            ignore=shutil.ignore_patterns(
                "Singleton*",
                "lockfile",
                "Lock",
                "LOCK",
                "Cookies-journal",
                "Network Action Predictor-journal",
            ),
        )
        return str(target_root)

    def discover_from_sources(
        self,
        sources: Iterable[CrawlSource],
        max_pages: int = 100,
        create_sources: bool = True,
    ) -> DiscoveryResult:
        pending = [source.url for source in sources if source.is_active]
        seen_pages = set()
        product_urls = set()
        listing_urls = set()
        failed_pages = 0
        source_by_url = {source.url: source for source in sources}

        while pending and len(seen_pages) < max_pages:
            current_url = pending.pop(0)
            if current_url in seen_pages:
                continue
            seen_pages.add(current_url)
            try:
                html = self.fetch(current_url)
            except AHScraperError as exc:
                failed_pages += 1
                crawl_source = source_by_url.get(current_url)
                if crawl_source is not None:
                    crawl_source.last_crawled_at = timezone.now()
                    crawl_source.last_error = str(exc)
                    crawl_source.save(update_fields=["last_crawled_at", "last_error", "updated_at"])
                continue
            current_product_urls, next_listing_urls = self.extract_links(html, current_url)
            product_urls.update(current_product_urls)

            for next_url in next_listing_urls:
                if next_url not in seen_pages and next_url not in pending:
                    pending.append(next_url)
                    listing_urls.add(next_url)

        discovered_sources = 0
        if create_sources:
            for url in sorted(listing_urls):
                _, created = CrawlSource.objects.get_or_create(
                    supermarket=self.supermarket,
                    url=url,
                    defaults={
                        "name": self.build_source_name(url),
                        "source_type": CrawlSource.SourceType.LISTING,
                    },
                )
                discovered_sources += int(created)

        for source in sources:
            source.last_crawled_at = timezone.now()
            source.last_error = ""
            source.save(update_fields=["last_crawled_at", "last_error", "updated_at"])

        for url in sorted(product_urls):
            external_id = self.extract_external_id(url)
            Product.objects.get_or_create(
                supermarket=self.supermarket,
                source_url=url,
                defaults={
                    "name": external_id or url.rsplit("/", 1)[-1],
                    "external_id": external_id or "",
                },
            )

        return DiscoveryResult(
            discovered_products=len(product_urls),
            discovered_sources=discovered_sources,
            visited_pages=len(seen_pages),
            failed_pages=failed_pages,
        )

    def scrape_product(self, product: Product) -> Product:
        html = self.fetch(product.source_url)
        parsed = self.parse_product_html(html, product.source_url)

        product.name = parsed["name"] or product.name
        product.brand = parsed["brand"]
        product.external_id = parsed["external_id"] or product.external_id
        product.package_size = parsed["package_size"]
        product.description = parsed["description"]
        product.ingredients = parsed["ingredients"]
        product.allergen_info = parsed["allergen_info"]
        product.image_url = parsed["image_url"]
        product.nutri_score_grade = parsed["nutri_score_grade"]
        product.nutri_score_label = parsed["nutri_score_label"]
        product.last_scraped_at = timezone.now()
        product.save()

        nutrition_facts, _ = NutritionFacts.objects.update_or_create(
            product=product,
            defaults=parsed["nutrition"],
        )
        nutrition_facts.entries.all().delete()
        NutritionEntry.objects.bulk_create(
            [
                NutritionEntry(
                    nutrition_facts=nutrition_facts,
                    position=index,
                    label=row["label"],
                    value_text=row["value_text"],
                    reference_intake_text=row["reference_intake_text"],
                )
                for index, row in enumerate(parsed["nutrition_entries"], start=1)
            ]
        )

        ProductSnapshot.objects.create(
            product=product,
            price_amount=parsed["price_amount"],
            price_text=parsed["price_text"],
            payload=parsed["payload"],
            scraped_at=timezone.now(),
        )
        return product

    def scrape_products(self, products: Iterable[Product], limit: int | None = None) -> int:
        count = 0
        for product in products:
            if limit is not None and count >= limit:
                break
            self.scrape_product(product)
            count += 1
        return count

    def extract_links(self, html: str, current_url: str) -> tuple[set[str], set[str]]:
        soup = BeautifulSoup(html, "html.parser")
        product_urls = set()
        listing_urls = set()

        for tag in soup.select("a[href]"):
            href = tag.get("href", "").strip()
            absolute_url = urljoin(current_url, href)
            parsed = urlparse(absolute_url)

            if parsed.netloc and parsed.netloc != "www.ah.nl":
                continue

            normalized_url = f"{BASE_URL}{parsed.path}"
            if PRODUCT_PATH_RE.match(parsed.path):
                product_urls.add(normalized_url.rstrip("/"))
                continue

            if parsed.path.startswith("/producten") or parsed.path.startswith("/bonus"):
                listing_urls.add(normalized_url.rstrip("/"))

        return product_urls, listing_urls

    def parse_product_html(self, html: str, url: str) -> dict:
        soup = BeautifulSoup(html, "html.parser")
        title = self.first_text(soup, ["h1"]) or self.extract_meta(soup, "og:title")
        title = re.sub(r"\s+bestellen\s*\|\s*Albert Heijn$", "", title or "", flags=re.I).strip()

        sections = self.extract_sections(soup)
        nutrition_meta = self.extract_nutrition_rows(soup)
        nutrition_rows = nutrition_meta["rows"]

        price_text = self.extract_price_text(soup)
        price_amount = self.parse_decimal(price_text) if price_text else None
        payload = {
            "sections": sections,
            "nutrition_meta": nutrition_meta,
            "nutrition_rows": nutrition_rows,
            "source_url": url,
        }

        return {
            "name": title,
            "brand": self.extract_brand(title),
            "external_id": self.extract_external_id(url),
            "package_size": self.extract_package_size(soup, sections),
            "description": sections.get("Omschrijving", ""),
            "ingredients": sections.get("Ingrediënten", ""),
            "allergen_info": sections.get("Allergie-informatie", ""),
            "image_url": self.extract_meta(soup, "og:image"),
            "nutri_score_grade": self.extract_nutri_score_grade(soup),
            "nutri_score_label": self.extract_nutri_score_label(soup),
            "price_text": price_text or "",
            "price_amount": price_amount,
            "nutrition": {
                "serving_size": self.extract_serving_size(soup, sections),
                "declaration_basis": nutrition_meta["declaration_basis"],
                "amount_column_label": nutrition_meta["amount_column_label"],
                "reference_intake_column_label": nutrition_meta["reference_intake_column_label"],
                "reference_intake_note": nutrition_meta["reference_intake_note"],
                "energy_kj": self.extract_nutrient(nutrition_rows, "Energie", suffix="kJ"),
                "energy_kcal": self.extract_nutrient(nutrition_rows, "Energie", suffix="kcal"),
                "fat_g": self.extract_nutrient(nutrition_rows, "Vet"),
                "saturates_g": self.extract_nutrient(nutrition_rows, "waarvan verzadigd"),
                "carbohydrates_g": self.extract_nutrient(nutrition_rows, "Koolhydraten"),
                "sugars_g": self.extract_nutrient(nutrition_rows, "waarvan suikers"),
                "fiber_g": self.extract_nutrient(nutrition_rows, "Voedingsvezel"),
                "protein_g": self.extract_nutrient(nutrition_rows, "Eiwitten"),
                "salt_g": self.extract_nutrient(nutrition_rows, "Zout"),
                "raw_text": json.dumps(nutrition_rows, ensure_ascii=True),
            },
            "nutrition_entries": nutrition_rows,
            "payload": payload,
        }

    def extract_sections(self, soup: BeautifulSoup) -> dict[str, str]:
        headings = [
            "Omschrijving",
            "Ingrediënten",
            "Voedingswaarden",
            "Gebruik",
            "Bewaren",
            "Herkomst",
            "Contactgegevens",
            "Kenmerken",
            "Extra informatie",
            "Inhoud en gewicht",
            "Allergie-informatie",
        ]
        sections = {}
        for heading in headings:
            node = soup.find(["h2", "h3", "h4"], string=re.compile(rf"^{re.escape(heading)}$", re.I))
            if not node:
                continue
            parts = []
            for sibling in node.find_next_siblings():
                sibling_name = getattr(sibling, "name", "")
                sibling_text = sibling.get_text(" ", strip=True)
                if sibling_name in {"h2", "h3", "h4"}:
                    break
                if sibling_text:
                    parts.append(sibling_text)
            sections[heading] = "\n".join(parts).strip()
        return sections

    def extract_nutrition_rows(self, soup: BeautifulSoup) -> dict:
        rows = []
        nutrition_heading = soup.find(["h2", "h3"], string=re.compile(r"^Voedingswaarden$", re.I))
        if not nutrition_heading:
            return {
                "declaration_basis": "",
                "amount_column_label": "",
                "reference_intake_column_label": "",
                "reference_intake_note": "",
                "rows": rows,
            }

        table = nutrition_heading.find_next("table")
        if not table:
            return {
                "declaration_basis": self.extract_nutrition_declaration_basis(nutrition_heading),
                "amount_column_label": "",
                "reference_intake_column_label": "",
                "reference_intake_note": self.extract_nutrition_reference_note(table),
                "rows": rows,
            }

        amount_column_label = ""
        reference_intake_column_label = ""
        for row in table.select("tr"):
            cols = [col.get_text(" ", strip=True) for col in row.select("th, td")]
            if not cols:
                continue
            if cols[0] == "Soort":
                amount_column_label = cols[1] if len(cols) > 1 else ""
                reference_intake_column_label = cols[2] if len(cols) > 2 else ""
                continue
            if len(cols) >= 2:
                rows.append(
                    {
                        "label": cols[0],
                        "value_text": cols[1],
                        "reference_intake_text": cols[2] if len(cols) > 2 else "",
                    }
                )
        return {
            "declaration_basis": self.extract_nutrition_declaration_basis(nutrition_heading),
            "amount_column_label": amount_column_label,
            "reference_intake_column_label": reference_intake_column_label,
            "reference_intake_note": self.extract_nutrition_reference_note(table),
            "rows": rows,
        }

    def extract_price_text(self, soup: BeautifulSoup) -> str:
        candidates = []
        for text in soup.stripped_strings:
            if "Prijs per" in text or "Normale prijs" in text or "€" in text:
                if PRICE_RE.search(text):
                    candidates.append(text)
                    continue
            if re.fullmatch(r"\d+(?:[.,]\d{2})", text):
                candidates.append(text)
        return candidates[0] if candidates else ""

    def extract_serving_size(self, soup: BeautifulSoup, sections: dict[str, str]) -> str:
        text = sections.get("Inhoud en gewicht", "")
        match = re.search(r"Portiegrootte:?\s*(.+)", text, flags=re.I)
        if match:
            return match.group(1).strip()
        return ""

    def extract_package_size(self, soup: BeautifulSoup, sections: dict[str, str]) -> str:
        text = sections.get("Inhoud en gewicht", "")
        match = re.search(r"(\d+(?:[.,]\d+)?\s*(?:Gram|g|KG|kg|ml|Liter|L|Stuks|stuk\(s\)))", text)
        if match:
            return match.group(1).strip()

        for candidate in soup.stripped_strings:
            if re.fullmatch(r"\d+(?:[.,]\d+)?\s*(?:g|kg|ml|l|Gram|Stuks)", candidate):
                return candidate.strip()
        return ""

    def extract_nutri_score_grade(self, soup: BeautifulSoup) -> str:
        text = soup.get_text("\n", strip=True)
        match = re.search(r"([A-E])\s+nutri-score", text, flags=re.I)
        if match:
            return match.group(1).upper()

        nutri_text = self.extract_nutri_score_label(soup)
        match = re.search(r"([A-E])", nutri_text, flags=re.I)
        return match.group(1).upper() if match else ""

    def extract_nutri_score_label(self, soup: BeautifulSoup) -> str:
        text = soup.get_text("\n", strip=True)
        for line in text.splitlines():
            if "nutri-score" in line.lower():
                return line.strip()
        return ""

    def extract_nutrition_declaration_basis(self, heading) -> str:
        if heading is None:
            return ""
        parts = []
        for sibling in heading.find_next_siblings():
            sibling_name = getattr(sibling, "name", "")
            if sibling_name == "table":
                break
            sibling_text = sibling.get_text(" ", strip=True)
            if sibling_text:
                parts.append(sibling_text)
        return " ".join(parts).strip()

    def extract_nutrition_reference_note(self, table) -> str:
        if table is None:
            return ""
        note_parts = []
        for sibling in table.find_next_siblings():
            sibling_name = getattr(sibling, "name", "")
            if sibling_name in {"h2", "h3", "h4", "table"}:
                break
            sibling_text = sibling.get_text(" ", strip=True)
            if sibling_text:
                note_parts.append(sibling_text)
        return " ".join(note_parts).strip()

    def extract_nutrient(
        self,
        rows: list[dict],
        label: str,
        suffix: str | None = None,
    ) -> Decimal | None:
        for row in rows:
            row_label = row["label"].strip().lower()
            if label.lower() not in row_label:
                continue
            value = row["value_text"]
            if suffix and suffix.lower() == "kcal":
                match = re.search(r"\(([-\d.,]+)\s*kcal\)", value, flags=re.I)
                return self.parse_decimal(match.group(1)) if match else None
            if suffix and suffix.lower() == "kj":
                match = re.search(r"([-\d.,]+)\s*kJ", value, flags=re.I)
                return self.parse_decimal(match.group(1)) if match else None
            match = NUMBER_RE.search(value)
            return self.parse_decimal(match.group(0)) if match else None
        return None

    def extract_brand(self, name: str) -> str:
        if not name:
            return ""
        words = name.split()
        if len(words) >= 2 and words[0].upper() == "AH":
            return "AH"
        return words[0]

    def extract_external_id(self, url: str) -> str | None:
        match = PRODUCT_PATH_RE.search(urlparse(url).path)
        return match.group(1) if match else None

    def extract_meta(self, soup: BeautifulSoup, property_name: str) -> str:
        tag = soup.find("meta", attrs={"property": property_name}) or soup.find(
            "meta",
            attrs={"name": property_name},
        )
        if tag:
            return tag.get("content", "").strip()
        return ""

    def build_source_name(self, url: str) -> str:
        path = urlparse(url).path.strip("/") or "home"
        return path.replace("/", " / ")

    def parse_decimal(self, value: str) -> Decimal | None:
        if not value:
            return None
        match = NUMBER_RE.search(value)
        if not match:
            return None
        raw = match.group(0)
        if "," in raw and "." in raw:
            normalized = raw.replace(".", "").replace(",", ".")
        elif "," in raw:
            normalized = raw.replace(",", ".")
        else:
            normalized = raw
        try:
            return Decimal(normalized)
        except InvalidOperation:
            return None

    def first_text(self, soup: BeautifulSoup, selectors: list[str]) -> str:
        for selector in selectors:
            node = soup.select_one(selector)
            if node:
                return node.get_text(" ", strip=True)
        return ""

    def parse_cookie_header(self, cookie_header: str) -> list[dict]:
        cookies = []
        for chunk in cookie_header.split(";"):
            chunk = chunk.strip()
            if not chunk or "=" not in chunk:
                continue
            name, value = chunk.split("=", 1)
            cookies.append(
                {
                    "name": name.strip(),
                    "value": value.strip(),
                    "domain": ".ah.nl",
                    "path": "/",
                }
            )
        return cookies
