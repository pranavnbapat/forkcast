from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, SimpleTestCase, TestCase
from django.test.utils import override_settings

from django.utils import timezone

from catalog.models import (
    CategoryScope,
    CuisineOption,
    CultureOption,
    Goal,
    IngredientImageAnalysis,
    IngredientPlan,
    IngredientPlanItem,
    NutritionEntry,
    NutritionFacts,
    PlannerProfile,
    Product,
    ProductQualityProfile,
    ProductSnapshot,
    RecipeSuggestionRun,
    Supermarket,
)
from catalog.services.ah import AHScraper
from catalog.services.ah_api import AHAPIImporter
from catalog.services.diet_metrics import derive_diet_metrics
from catalog.services.nutrition import aggregate_profiles, aggregate_macro_summaries, build_product_profile, convert_to_grams
from catalog.services.product_filters import food_candidate_queryset
from catalog.services.product_quality import ProductQualityEnricher


class AHScraperTests(SimpleTestCase):
    def setUp(self):
        self.scraper = AHScraper.__new__(AHScraper)

    def test_extract_links_finds_product_and_listing_urls(self):
        html = """
        <html><body>
            <a href="/producten/product/wi202196/hak-witte-bonen-in-tomatensaus">Product</a>
            <a href="/producten/groente-aardappel-fruit/groente">Category</a>
            <a href="/bonus">Bonus</a>
            <a href="https://example.com/ignored">External</a>
        </body></html>
        """

        product_urls, listing_urls = self.scraper.extract_links(html, "https://www.ah.nl/producten")

        self.assertEqual(
            product_urls,
            {"https://www.ah.nl/producten/product/wi202196/hak-witte-bonen-in-tomatensaus"},
        )
        self.assertEqual(
            listing_urls,
            {
                "https://www.ah.nl/producten/groente-aardappel-fruit/groente",
                "https://www.ah.nl/bonus",
            },
        )

    def test_parse_product_html_extracts_core_fields(self):
        html = """
        <html>
            <head>
                <meta property="og:title" content="AH Bananen tros bestellen | Albert Heijn">
                <meta property="og:image" content="https://static.example.com/banana.jpg">
            </head>
            <body>
                <h1>AH Bananen tros</h1>
                <div>1.45</div>
                <div>A nutri-score</div>
                <h2>Omschrijving</h2>
                <p>Lekker zoete bananen.</p>
                <h2>Ingrediënten</h2>
                <p>Ingrediënten: Waarvan toegevoegde suikers 0g per 100 gram en waarvan toegevoegd zout 0g per 100 gram</p>
                <h2>Inhoud en gewicht</h2>
                <p>1 kg</p>
                <h2>Voedingswaarden</h2>
                <p>Deze waarden gelden voor het onbereide product.</p>
                <table>
                    <tr><th>Soort</th><th>Per 100 Gram</th><th>RI*</th></tr>
                    <tr><td>Energie</td><td>385 kJ (91 kcal)</td><td></td></tr>
                    <tr><td>Vet</td><td>0,3 g</td></tr>
                    <tr><td>waarvan verzadigd</td><td>0,1 g</td></tr>
                    <tr><td>waarvan onverzadigd</td><td>0,2 g</td></tr>
                    <tr><td>Koolhydraten</td><td>20 g</td></tr>
                    <tr><td>waarvan suikers</td><td>16 g</td></tr>
                    <tr><td>Voedingsvezel</td><td>1,9 g</td></tr>
                    <tr><td>Eiwitten</td><td>1,1 g</td></tr>
                    <tr><td>Zout</td><td>0 g</td></tr>
                    <tr><td>Vitamine B6 / Pyridoxine</td><td>0,29 mg</td><td>20%</td></tr>
                    <tr><td>Kalium/Potassium</td><td>374 mg</td><td>18%</td></tr>
                </table>
                <p>* Referentie-inname van een gemiddelde volwassene is 8400 kJ / 2000 kcal</p>
            </body>
        </html>
        """

        parsed = self.scraper.parse_product_html(
            html,
            "https://www.ah.nl/producten/product/wi197393/ah-bananen-tros",
        )

        self.assertEqual(parsed["name"], "AH Bananen tros")
        self.assertEqual(parsed["brand"], "AH")
        self.assertEqual(parsed["external_id"], "wi197393")
        self.assertEqual(parsed["package_size"], "1 kg")
        self.assertEqual(parsed["description"], "Lekker zoete bananen.")
        self.assertEqual(
            parsed["ingredients"],
            "Ingrediënten: Waarvan toegevoegde suikers 0g per 100 gram en waarvan toegevoegd zout 0g per 100 gram",
        )
        self.assertEqual(parsed["price_amount"], Decimal("1.45"))
        self.assertEqual(parsed["nutri_score_grade"], "A")
        self.assertEqual(parsed["nutri_score_label"], "A nutri-score")
        self.assertEqual(parsed["nutrition"]["declaration_basis"], "Deze waarden gelden voor het onbereide product.")
        self.assertEqual(parsed["nutrition"]["amount_column_label"], "Per 100 Gram")
        self.assertEqual(parsed["nutrition"]["reference_intake_column_label"], "RI*")
        self.assertEqual(
            parsed["nutrition"]["reference_intake_note"],
            "* Referentie-inname van een gemiddelde volwassene is 8400 kJ / 2000 kcal",
        )
        self.assertEqual(parsed["nutrition"]["energy_kj"], Decimal("385"))
        self.assertEqual(parsed["nutrition"]["energy_kcal"], Decimal("91"))
        self.assertEqual(parsed["nutrition"]["salt_g"], Decimal("0"))
        self.assertEqual(parsed["nutrition_entries"][0]["label"], "Energie")
        self.assertEqual(parsed["nutrition_entries"][0]["value_text"], "385 kJ (91 kcal)")
        self.assertEqual(parsed["nutrition_entries"][-1]["label"], "Kalium/Potassium")
        self.assertEqual(parsed["nutrition_entries"][-1]["reference_intake_text"], "18%")


@override_settings(OPENSEARCH_ENABLED=False)
class NutritionSearchTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = get_user_model().objects.create_user(username="nutrition_user", password="nutritionpass123")
        self.supermarket, _ = Supermarket.objects.get_or_create(
            slug="albert-heijn",
            defaults={"name": "Albert Heijn"},
        )
        self.product = Product.objects.create(
            supermarket=self.supermarket,
            name="AH Bananen tros",
            brand="AH",
            external_id="wi197393",
            source_url="https://www.ah.nl/producten/product/wi197393/ah-bananen-tros",
            nutri_score_grade="A",
            nutri_score_label="A nutri-score",
        )
        facts = NutritionFacts.objects.create(
            product=self.product,
            serving_size="Tros",
            energy_kj=Decimal("385"),
            energy_kcal=Decimal("91"),
            fat_g=Decimal("0.3"),
            sugars_g=Decimal("16"),
            salt_g=Decimal("0"),
        )
        NutritionEntry.objects.create(nutrition_facts=facts, position=1, label="Energie", value_text="385.0 kJ (91.0 kcal)")
        NutritionEntry.objects.create(nutrition_facts=facts, position=2, label="Vet", value_text="0.3 g")
        NutritionEntry.objects.create(nutrition_facts=facts, position=3, label="Zout", value_text="0.0 g")
        ProductSnapshot.objects.create(
            product=self.product,
            price_amount=Decimal("1.45"),
            price_text="1.45",
            payload={
                "product_card": {
                    "currentPrice": 1.45,
                    "priceBeforeBonus": 1.95,
                    "isBonus": True,
                    "bonusMechanism": "Bonusprijs",
                }
            },
            scraped_at=timezone.now(),
        )

    def test_convert_to_grams_for_tsp_oil_uses_density(self):
        oil = Product(
            name="Monini olijfolie",
            brand="Monini",
            external_id="wi999901",
            source_url="https://example.com/olive-oil",
            supermarket=self.supermarket,
        )
        grams, note = convert_to_grams(oil, Decimal("1"), "tsp")
        self.assertEqual(grams, Decimal("4.55"))
        self.assertIn("density 0.91", note)

    def test_build_profile_and_aggregate(self):
        profile = build_product_profile(self.product, Decimal("50"))
        totals = aggregate_profiles([profile])
        self.assertEqual(profile["rows"][0]["label"], "Energie")
        self.assertEqual(totals[0]["label"], "Energie")
        self.assertEqual(totals[0]["value"], Decimal("192.50"))
        self.assertEqual(totals[0]["display_value"], "192.50 kJ (45.50 kcal)")

    def test_macro_summary_uses_formula_based_energy(self):
        self.product.nutrition_facts.carbohydrates_g = Decimal("20")
        self.product.nutrition_facts.protein_g = Decimal("1.1")
        self.product.nutrition_facts.fat_g = Decimal("0.3")
        self.product.nutrition_facts.saturates_g = Decimal("0.1")
        self.product.nutrition_facts.sugars_g = Decimal("16")
        self.product.nutrition_facts.fiber_g = Decimal("1.9")
        metrics = derive_diet_metrics(
            fat_g=self.product.nutrition_facts.fat_g,
            saturates_g=self.product.nutrition_facts.saturates_g,
            carbohydrates_g=self.product.nutrition_facts.carbohydrates_g,
            sugars_g=self.product.nutrition_facts.sugars_g,
            fiber_g=self.product.nutrition_facts.fiber_g,
            protein_g=self.product.nutrition_facts.protein_g,
        )
        self.assertEqual(metrics["estimated_energy_kcal"], Decimal("87.1"))
        self.assertEqual(metrics["unsaturated_g"], Decimal("0.2"))
        self.assertEqual(metrics["starch_g"], Decimal("2.1"))

    def test_aggregate_macro_summaries_returns_declared_and_estimated_energy(self):
        self.product.nutrition_facts.carbohydrates_g = Decimal("20")
        self.product.nutrition_facts.protein_g = Decimal("1.1")
        self.product.nutrition_facts.fat_g = Decimal("0.3")
        self.product.nutrition_facts.saturates_g = Decimal("0.1")
        self.product.nutrition_facts.sugars_g = Decimal("16")
        self.product.nutrition_facts.fiber_g = Decimal("1.9")
        self.product.nutrition_facts.unsaturated_g = Decimal("0.2")
        self.product.nutrition_facts.starch_g = Decimal("2.1")
        self.product.nutrition_facts.estimated_energy_kcal = Decimal("87.1")
        self.product.nutrition_facts.estimated_energy_kj = Decimal("361.08")
        self.product.nutrition_facts.save()
        profile = build_product_profile(self.product, Decimal("100"))
        summary = aggregate_macro_summaries([profile])
        self.assertEqual(summary["declared_energy_kcal"], Decimal("91"))
        self.assertEqual(summary["estimated_energy_kcal"], Decimal("87.1"))

    def test_search_page_renders(self):
        self.client.force_login(self.user)
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nutrition Search")

    def test_search_page_requires_login(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_product_search_api_returns_matches(self):
        self.client.force_login(self.user)
        response = self.client.get("/api/products/search/?q=banana")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["results"][0]["name"], "AH Bananen tros")
        response = self.client.get("/api/products/search/?q=ban")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["results"][0]["name"], "AH Bananen tros")
        self.assertTrue(payload["results"][0]["has_nutrition"])
        self.assertEqual(payload["results"][0]["price_amount"], 1.45)
        self.assertTrue(payload["results"][0]["is_bonus"])

    def test_search_api_matches_brand_and_marks_inventory_only_products(self):
        self.client.force_login(self.user)
        Product.objects.create(
            supermarket=self.supermarket,
            name="Terra Tofu naturel",
            brand="Terra",
            package_size="375 g",
            external_id="wi999999",
            source_url="https://www.ah.nl/producten/product/wi999999/terra-tofu-naturel",
        )

        response = self.client.get("/api/products/search/?q=terra")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["results"][0]["name"], "Terra Tofu naturel")
        self.assertFalse(payload["results"][0]["has_nutrition"])

    def test_post_warns_when_product_has_no_nutrition_rows(self):
        self.client.force_login(self.user)
        tofu = Product.objects.create(
            supermarket=self.supermarket,
            name="AH Terra tofu",
            brand="AH Terra",
            external_id="wi999998",
            source_url="https://www.ah.nl/producten/product/wi999998/ah-terra-tofu",
        )

        response = self.client.post(
            "/",
            data={
                "product_id": [str(tofu.id)],
                "quantity": ["50"],
                "unit": ["g"],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No nutrition rows stored yet for AH Terra tofu.")

    def test_search_prefers_nutrition_ready_exact_match(self):
        self.client.force_login(self.user)
        Product.objects.create(
            supermarket=self.supermarket,
            name="AH Bananen tros 2-pack",
            brand="AH",
            external_id="wi999997",
            source_url="https://www.ah.nl/producten/product/wi999997/ah-bananen-tros-2-pack",
        )

        response = self.client.get("/api/products/search/?q=AH Bananen tros")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["results"][0]["name"], "AH Bananen tros")
        self.assertTrue(payload["results"][0]["has_nutrition"])

    def test_search_matches_words_out_of_order(self):
        self.client.force_login(self.user)
        response = self.client.get("/api/products/search/?q=tros bananen")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["results"][0]["name"], "AH Bananen tros")

    def test_search_handles_small_typo(self):
        self.client.force_login(self.user)
        egg_product = Product.objects.create(
            supermarket=self.supermarket,
            name="AH Biologisch Eieren S M L",
            brand="AH Biologisch",
            external_id="wi999994",
            source_url="https://www.ah.nl/producten/product/wi999994/ah-biologisch-eieren-s-m-l",
        )

        response = self.client.get("/api/products/search/?q=eeiren")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["results"][0]["id"], egg_product.id)
        self.assertEqual(payload["results"][0]["name"], "AH Biologisch Eieren S M L")

    def test_planner_search_collapses_same_family_same_nutrition_variants(self):
        self.client.force_login(self.user)
        avocado = Product.objects.create(
            supermarket=self.supermarket,
            name="AH Avocado",
            brand="AH",
            external_id="wi999910",
            source_url="https://www.ah.nl/producten/product/wi999910/ah-avocado",
        )
        avocado_ripe = Product.objects.create(
            supermarket=self.supermarket,
            name="AH Avocado eetrijp",
            brand="AH",
            external_id="wi999911",
            source_url="https://www.ah.nl/producten/product/wi999911/ah-avocado-eetrijp",
        )
        avocado_oil = Product.objects.create(
            supermarket=self.supermarket,
            name="AH Avocado olie",
            brand="AH",
            external_id="wi999912",
            source_url="https://www.ah.nl/producten/product/wi999912/ah-avocado-olie",
        )
        NutritionFacts.objects.create(
            product=avocado,
            energy_kcal=Decimal("183"),
            fat_g=Decimal("18"),
            saturates_g=Decimal("2.1"),
            unsaturated_g=Decimal("15"),
            carbohydrates_g=Decimal("1.8"),
            sugars_g=Decimal("0.5"),
            fiber_g=Decimal("3.1"),
            protein_g=Decimal("2.0"),
            salt_g=Decimal("0.03"),
        )
        NutritionFacts.objects.create(
            product=avocado_ripe,
            energy_kcal=Decimal("183"),
            fat_g=Decimal("18"),
            saturates_g=Decimal("2.1"),
            unsaturated_g=Decimal("15"),
            carbohydrates_g=Decimal("1.8"),
            sugars_g=Decimal("0.5"),
            fiber_g=Decimal("3.1"),
            protein_g=Decimal("2.0"),
            salt_g=Decimal("0.03"),
        )
        NutritionFacts.objects.create(
            product=avocado_oil,
            energy_kcal=Decimal("824"),
            fat_g=Decimal("91"),
            saturates_g=Decimal("12"),
            unsaturated_g=Decimal("79"),
            carbohydrates_g=Decimal("0"),
            sugars_g=Decimal("0"),
            fiber_g=Decimal("0"),
            protein_g=Decimal("0"),
            salt_g=Decimal("0"),
        )

        response = self.client.get("/api/products/search/?q=avocado&mode=planner")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        names = [item["name"] for item in payload["results"]]
        self.assertIn("AH Avocado olie", names)
        self.assertTrue("AH Avocado" in names or "AH Avocado eetrijp" in names)
        self.assertFalse("AH Avocado" in names and "AH Avocado eetrijp" in names)

    @patch("catalog.views._opensearch_results")
    def test_product_search_api_prefers_opensearch_when_available(self, mock_opensearch_results):
        self.client.force_login(self.user)
        mock_opensearch_results.return_value = [
            {
                "id": 999,
                "name": "AH Avocado eetrijp",
                "brand": "AH",
                "package_size": "2 stuks",
                "image_url": "https://example.com/avocado.jpg",
                "nutri_score_grade": "A",
                "has_nutrition": True,
                "price_amount": 2.49,
                "is_bonus": False,
            }
        ]

        response = self.client.get("/api/products/search/?q=avocado")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["backend"], "opensearch")
        self.assertEqual(payload["results"][0]["name"], "AH Avocado eetrijp")

    def test_post_suggests_alternative_same_name_with_nutrition(self):
        self.client.force_login(self.user)
        empty_variant = Product.objects.create(
            supermarket=self.supermarket,
            name="AH Avocado",
            brand="AH",
            package_size="per stuk",
            external_id="wi999996",
            source_url="https://www.ah.nl/producten/product/wi999996/ah-avocado",
        )
        ready_variant = Product.objects.create(
            supermarket=self.supermarket,
            name="AH Avocado",
            brand="AH",
            package_size="650 g",
            external_id="wi999995",
            source_url="https://www.ah.nl/producten/product/wi999995/ah-avocado-650g",
        )
        ready_facts = NutritionFacts.objects.create(product=ready_variant)
        NutritionEntry.objects.create(
            nutrition_facts=ready_facts,
            position=1,
            label="Energie",
            value_text="160 kcal",
        )

        response = self.client.post(
            "/",
            data={
                "product_id": [str(empty_variant.id)],
                "quantity": ["100"],
                "unit": ["g"],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Try AH Avocado 650 g.")


class ProductSyncIntegrityTests(TestCase):
    def setUp(self):
        self.supermarket, _ = Supermarket.objects.get_or_create(
            slug="albert-heijn",
            defaults={"name": "Albert Heijn"},
        )

    def test_sync_product_card_reuses_existing_placeholder_by_external_id(self):
        Product.objects.create(
            supermarket=self.supermarket,
            name="wi123456",
            brand="",
            package_size="",
            external_id="wi123456",
            source_url="https://www.ah.nl/producten/product/wi123456",
        )
        importer = AHAPIImporter(supermarket=self.supermarket)
        card = {
            "webshopId": 123456,
            "title": "AH Rijke variant",
            "brand": "AH",
            "salesUnitSize": "500 g",
            "descriptionFull": "Better data",
            "descriptionHighlights": "",
            "properties": {},
            "images": [{"url": "https://example.com/rich.jpg", "width": 400}],
            "currentPrice": "2.49",
            "nutriscore": "A",
        }
        nutrition_rows = [{"name": "Energie", "value": "100 kJ (24 kcal)"}]

        importer.sync_product_card(card, load_detail=False, load_nutrition=False, nutrition_rows=nutrition_rows)
        importer.close()

        self.assertEqual(Product.objects.filter(external_id="wi123456").count(), 1)
        survivor = Product.objects.get(external_id="wi123456")
        self.assertEqual(survivor.name, "AH Rijke variant")
        self.assertEqual(survivor.package_size, "500 g")
        self.assertEqual(survivor.snapshots.count(), 1)
        self.assertEqual(survivor.nutrition_facts.entries.count(), 1)

    def test_sync_product_card_avoids_duplicate_snapshot_for_unchanged_payload(self):
        importer = AHAPIImporter(supermarket=self.supermarket)
        card = {
            "webshopId": 999001,
            "title": "AH Test product",
            "brand": "AH",
            "salesUnitSize": "100 g",
            "descriptionFull": "Beschrijving",
            "descriptionHighlights": "",
            "properties": {},
            "images": [{"url": "https://example.com/test.jpg", "width": 400}],
            "currentPrice": "1.99",
            "nutriscore": "A",
        }
        nutrition_rows = [{"name": "Energie", "value": "100 kJ (24 kcal)"}]

        importer.sync_product_card(card, load_detail=False, load_nutrition=False, nutrition_rows=nutrition_rows)
        importer.sync_product_card(card, load_detail=False, load_nutrition=False, nutrition_rows=nutrition_rows)
        importer.close()

        product = Product.objects.get(external_id="wi999001")
        self.assertEqual(Product.objects.filter(external_id="wi999001").count(), 1)
        self.assertEqual(product.snapshots.count(), 1)
        self.assertEqual(product.nutrition_facts.entries.count(), 1)

    def test_sync_product_card_skips_non_food_category_outside_scope(self):
        CategoryScope.objects.get_or_create(
            slug="bakkerij",
            defaults={"name": "Bakkerij", "is_food": True, "is_active": True},
        )
        CategoryScope.objects.get_or_create(
            slug="baby-en-kind",
            defaults={"name": "Baby en kind", "is_food": False, "is_active": True},
        )
        importer = AHAPIImporter(supermarket=self.supermarket)
        card = {
            "webshopId": 999002,
            "title": "AH Baby product",
            "brand": "AH",
            "salesUnitSize": "1 stuk",
            "descriptionFull": "Beschrijving",
            "descriptionHighlights": "",
            "properties": {},
            "images": [{"url": "https://example.com/test.jpg", "width": 400}],
            "currentPrice": "3.99",
            "mainCategory": "Baby en kind",
        }

        product = importer.sync_product_card(card, load_detail=False, load_nutrition=False, nutrition_rows=[])
        importer.close()

        self.assertIsNone(product)
        self.assertFalse(Product.objects.filter(external_id="wi999002").exists())


class AdminStatusTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.supermarket, _ = Supermarket.objects.get_or_create(
            slug="albert-heijn",
            defaults={"name": "Albert Heijn"},
        )
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="admin_status",
            email="admin@example.com",
            password="admin1234!",
        )

    def test_admin_sync_status_page_renders(self):
        self.client.force_login(self.user)

        response = self.client.get("/admin/sync-status/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AH Sync Status")
        self.assertContains(response, "Catalog Coverage")


class ShoppingListTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = get_user_model().objects.create_user(username="shopping_user", password="shoppingpass123")
        self.supermarket, _ = Supermarket.objects.get_or_create(
            slug="albert-heijn",
            defaults={"name": "Albert Heijn"},
        )
        self.product = Product.objects.create(
            supermarket=self.supermarket,
            name="AH Avocado",
            brand="AH",
            external_id="wi169797",
            package_size="per stuk",
            source_url="https://www.ah.nl/producten/product/wi169797/ah-avocado",
        )
        ProductSnapshot.objects.create(
            product=self.product,
            price_amount=Decimal("1.45"),
            price_text="1.45",
            payload={
                "product_card": {
                    "currentPrice": 1.45,
                    "priceBeforeBonus": 1.95,
                    "isBonus": True,
                    "bonusMechanism": "25% korting",
                }
            },
            scraped_at=timezone.now(),
        )

    def test_shopping_list_page_renders(self):
        self.client.force_login(self.user)
        response = self.client.get("/shopping-list/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Shopping List Cost")

    def test_shopping_list_requires_login(self):
        response = self.client.get("/shopping-list/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_shopping_list_calculates_totals_and_savings(self):
        self.client.force_login(self.user)
        response = self.client.post(
            "/shopping-list/",
            data={
                "product_id": [str(self.product.id)],
                "quantity": ["2"],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AH Avocado")
        self.assertContains(response, "€ 2.90")
        self.assertContains(response, "€ 3.90")
        self.assertContains(response, "€ 1.00")

    def test_shopping_list_applies_buy_one_get_one_free(self):
        self.client.force_login(self.user)
        product = Product.objects.create(
            supermarket=self.supermarket,
            name="Hak Witte bonen in tomatensaus",
            brand="Hak",
            external_id="wi202196",
            package_size="360 g",
            source_url="https://www.ah.nl/producten/product/wi202196/hak-witte-bonen-in-tomatensaus",
        )
        ProductSnapshot.objects.create(
            product=product,
            price_amount=Decimal("2.59"),
            price_text="2.59",
            payload={
                "product_card": {
                    "currentPrice": 2.59,
                    "priceBeforeBonus": 2.59,
                    "isBonus": True,
                    "bonusMechanism": "1 + 1 GRATIS",
                    "discountLabels": [
                        {
                            "code": "DISCOUNT_X_PLUS_Y_FREE",
                            "count": 1,
                            "freeCount": 1,
                            "defaultDescription": "1+1 gratis",
                        }
                    ],
                }
            },
            scraped_at=timezone.now(),
        )

        response = self.client.post(
            "/shopping-list/",
            data={
                "product_id": [str(product.id)],
                "quantity": ["2"],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "€ 2.59")
        self.assertContains(response, "1+1 gratis toegepast")

    def test_shopping_list_applies_volume_discount(self):
        self.client.force_login(self.user)
        product = Product.objects.create(
            supermarket=self.supermarket,
            name="Alpro Barista soja 4-pack",
            brand="Alpro",
            external_id="wi568163",
            package_size="4 stuks",
            source_url="https://www.ah.nl/producten/product/wi568163/alpro-barista-soja-4-pack",
        )
        ProductSnapshot.objects.create(
            product=product,
            price_amount=Decimal("10.22"),
            price_text="10.22",
            payload={
                "product_card": {
                    "currentPrice": 10.22,
                    "priceBeforeBonus": 10.76,
                    "isBonus": True,
                    "bonusMechanism": "5% volume voordeel",
                    "discountLabels": [
                        {
                            "code": "DISCOUNT_BUNDLE_BULK",
                            "percentage": 5,
                            "precisePercentage": 5,
                        }
                    ],
                }
            },
            scraped_at=timezone.now(),
        )

        response = self.client.post(
            "/shopping-list/",
            data={
                "product_id": [str(product.id)],
                "quantity": ["1"],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "€ 10.22")
        self.assertContains(response, "5% volumevoordeel toegepast")

    def test_logout_route_logs_user_out(self):
        self.client.force_login(self.user)
        response = self.client.get("/logout/")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/login/")


class RecipePlannerTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = get_user_model().objects.create_user(username="planner", password="plannerpass123")
        self.supermarket, _ = Supermarket.objects.get_or_create(
            slug="albert-heijn",
            defaults={"name": "Albert Heijn"},
        )
        self.primary_goal, _ = Goal.objects.get_or_create(
            slug="lose-weight",
            defaults={"name": "Lose Weight"},
        )
        self.secondary_goal, _ = Goal.objects.get_or_create(
            slug="save-money",
            defaults={"name": "Save Money"},
        )
        self.protein_goal, _ = Goal.objects.get_or_create(
            slug="eat-more-protein",
            defaults={"name": "Eat More Protein"},
        )
        self.culture_option, _ = CultureOption.objects.get_or_create(
            slug="indian",
            defaults={"name": "Indian", "region": "South Asia"},
        )
        self.cuisine_option, _ = CuisineOption.objects.get_or_create(
            slug="curries",
            defaults={"name": "Curries", "region": "South Asia"},
        )
        self.product = Product.objects.create(
            supermarket=self.supermarket,
            name="AH Avocado",
            brand="AH",
            external_id="wi169797",
            package_size="per stuk",
            source_url="https://www.ah.nl/producten/product/wi169797/ah-avocado",
        )
        ProductSnapshot.objects.create(
            product=self.product,
            price_amount=Decimal("1.45"),
            price_text="1.45",
            payload={"product_card": {"currentPrice": 1.45, "isBonus": False}},
            scraped_at=timezone.now(),
        )

    def test_recipe_planner_page_renders(self):
        self.client.force_login(self.user)
        response = self.client.get("/recipe-planner/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Recipe Planner")
        self.assertContains(response, "Profile Snapshot")
        self.assertContains(response, "Edit profile")

    def test_image_recipe_planner_page_renders(self):
        self.client.force_login(self.user)
        response = self.client.get("/image-recipe-planner/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Image Recipe Planner")
        self.assertContains(response, "Upload image")

    def test_recipe_planner_requires_login(self):
        response = self.client.get("/recipe-planner/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_profile_page_saves_user_profile(self):
        self.client.force_login(self.user)
        response = self.client.post(
            "/profile/",
            data={
                "primary_goal": str(self.primary_goal.id),
                "secondary_goal": str(self.secondary_goal.id),
                "gender": "male",
                "age": "37",
                "height_cm": "180",
                "weight_kg": "82",
                "culture_option": str(self.culture_option.id),
                "cuisine_option": str(self.cuisine_option.id),
                "culture": "Gujarati",
                "lifestyle": "active",
                "fasting_pattern": "none",
                "diet_style": "vegetarian",
                "allergies": "peanuts",
                "notes": "High protein lunches",
            },
        )
        self.assertEqual(response.status_code, 302)
        profile = PlannerProfile.objects.get(user=self.user)
        self.assertEqual(profile.primary_goal_id, self.primary_goal.id)
        self.assertEqual(profile.height_cm, Decimal("180"))
        self.assertEqual(profile.culture, "Gujarati")

    @patch("catalog.views.stream_recipe_suggestions")
    def test_recipe_planner_saves_profile_plan_and_run(self, mock_generate):
        self.client.force_login(self.user)
        profile = PlannerProfile.objects.create(
            name=f"{self.user.username} Profile",
            user=self.user,
            primary_goal=self.primary_goal,
            secondary_goal=self.secondary_goal,
            gender="male",
            age=30,
            height_cm=Decimal("178"),
            weight_kg=Decimal("82"),
            culture_option=self.culture_option,
            cuisine_option=self.cuisine_option,
            culture="Indian",
            lifestyle="active",
            fasting_pattern="none",
            diet_style="vegetarian",
            allergies="peanuts",
            notes="Need easy lunch recipes",
        )
        plan = IngredientPlan.objects.create(
            name=f"{self.user.username} Profile Manual Ingredient Plan",
            profile=profile,
        )
        run = RecipeSuggestionRun.objects.create(
            plan=plan,
            profile=profile,
            model_name="qwen-test",
            status="completed",
            response_json={
                "overview": {"primary_goal_fit": "Good", "secondary_goal_fit": "Cheap enough"},
                "metabolic_context": {
                    "bmi": "25.9",
                    "bmr_kcal": "1760",
                    "tdee_kcal": "2728",
                    "planning_note": "Use TDEE as the maintenance reference and adjust by goal."
                },
                "goal_explanation": {
                    "summary": "Given the selected goal, the planner should keep intake near maintenance while increasing protein quality.",
                    "reasoning": [
                        "BMR is the baseline energy your body uses at rest.",
                        "TDEE reflects your daily expenditure after activity is added.",
                        "For maintenance, calories should stay close to TDEE with enough protein and fibre."
                    ]
                },
                "daily_targets": {
                    "energy_kcal": "2200-2400 kcal",
                    "protein_g": "120-150 g/day",
                    "fat_g": "60-80 g/day",
                    "carb_g": "220-280 g/day",
                    "fiber_g": "30-40 g/day",
                    "sugar_guidance": "Keep added sugar modest and prioritize whole-food carbohydrate sources.",
                    "salt_guidance": "Keep salt moderate and avoid oversalting soups and packaged foods.",
                    "meal_distribution": "Split into 3 meals and 1 snack"
                },
                "recipes": [],
                "shopping_gaps": [],
                "money_saving_notes": [],
                "nutrition_notes": [],
                "questions_to_clarify": [],
            },
        )
        mock_generate.return_value = iter([("complete", {"run_id": run.id, "response_json": run.response_json})])

        response = self.client.post(
            "/recipe-planner/stream/",
            data={
                "horizon": "tomorrow",
                "plan_notes": "For tomorrow",
                "product_id": [str(self.product.id)],
                "quantity": ["2"],
                "unit": ["unit"],
                "item_note": [""],
            },
        )

        self.assertEqual(response.status_code, 200)
        streamed = b"".join(response.streaming_content).decode("utf-8")
        self.assertIn("event: complete", streamed)
        profile.refresh_from_db()
        plan.refresh_from_db()
        self.assertEqual(profile.secondary_goal_id, self.secondary_goal.id)
        self.assertEqual(profile.height_cm, Decimal("178"))
        self.assertEqual(profile.weight_kg, Decimal("82"))
        self.assertEqual(profile.culture_option_id, self.culture_option.id)
        self.assertEqual(profile.cuisine_option_id, self.cuisine_option.id)
        self.assertEqual(profile.culture, "Indian")
        self.assertEqual(plan.horizon, "tomorrow")
        self.assertEqual(plan.items.count(), 1)
        item = IngredientPlanItem.objects.get(plan=plan)
        self.assertEqual(item.product_id, self.product.id)
        self.assertEqual(item.quantity, Decimal("2"))
        mock_generate.assert_called_once()

    def test_protein_goal_exists(self):
        self.assertTrue(Goal.objects.filter(slug="eat-more-protein").exists())

    def test_environment_goals_exist(self):
        self.assertTrue(Goal.objects.filter(slug="sustainable-eating").exists())
        self.assertTrue(Goal.objects.filter(slug="lower-environmental-impact").exists())

    @patch("catalog.views.analyze_ingredient_image")
    @patch("catalog.views.stream_recipe_suggestions")
    def test_image_recipe_planner_stream_runs_recipe_flow(self, mock_generate, mock_analyze):
        self.client.force_login(self.user)
        profile = PlannerProfile.objects.create(
            name=f"{self.user.username} Profile",
            user=self.user,
            primary_goal=self.primary_goal,
        )
        plan = IngredientPlan.objects.create(
            name=f"{self.user.username} Profile Image Ingredient Plan",
            profile=profile,
        )
        analysis = IngredientImageAnalysis.objects.create(
            plan=plan,
            profile=profile,
            model_name="vision-test",
            status="completed",
            response_json={"summary": "Detected avocado."},
            extracted_items=[],
            image=SimpleUploadedFile("analysis.jpg", b"fake", content_type="image/jpeg"),
        )
        mock_analyze.return_value = (analysis, [])
        mock_generate.return_value = iter([("complete", {"run_id": 1, "response_json": {"recipes": []}})])

        response = self.client.post(
            "/image-recipe-planner/stream/",
            data={
                "horizon": "tomorrow",
                "ingredient_image": SimpleUploadedFile("ingredients.jpg", b"fake-image", content_type="image/jpeg"),
            },
        )

        self.assertEqual(response.status_code, 200)
        streamed = b"".join(response.streaming_content).decode("utf-8")
        self.assertIn("event: complete", streamed)
        mock_analyze.assert_called_once()
        mock_generate.assert_called_once()

    @patch("catalog.views.analyze_ingredient_image")
    def test_recipe_planner_image_upload_saves_analysis_and_shows_detection(self, mock_analyze):
        self.client.force_login(self.user)
        profile = PlannerProfile.objects.create(
            name=f"{self.user.username} Profile",
            user=self.user,
            primary_goal=self.primary_goal,
        )
        plan = IngredientPlan.objects.create(
            name=f"{self.user.username} Profile Manual Ingredient Plan",
            profile=profile,
        )
        analysis = IngredientImageAnalysis.objects.create(
            plan=plan,
            profile=profile,
            model_name="vision-test",
            status="completed",
            response_json={"summary": "Detected avocado and tofu."},
            extracted_items=[
                {
                    "name": "AH Avocado",
                    "estimated_quantity": "2",
                    "unit": "unit",
                    "confidence": "high",
                    "notes": "",
                    "matched_product_id": self.product.id,
                    "matched_product_name": self.product.name,
                }
            ],
            image=SimpleUploadedFile("analysis.jpg", b"fake", content_type="image/jpeg"),
        )
        mock_analyze.return_value = (analysis, [])

        response = self.client.post(
            "/recipe-planner/image/",
            data={
                "horizon": "tomorrow",
                "product_id": [str(self.product.id)],
                "quantity": ["1"],
                "unit": ["unit"],
                "item_note": [""],
                "ingredient_image": SimpleUploadedFile("ingredients.jpg", b"fake-image", content_type="image/jpeg"),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Latest image analysis")
        self.assertContains(response, "Detected avocado and tofu.")
        mock_analyze.assert_called_once()

    def test_recipe_planner_can_clear_current_ingredient_list(self):
        self.client.force_login(self.user)
        profile = PlannerProfile.objects.create(
            name=f"{self.user.username} Profile",
            user=self.user,
            primary_goal=self.primary_goal,
        )
        plan = IngredientPlan.objects.create(
            name=f"{self.user.username} Profile Manual Ingredient Plan",
            profile=profile,
        )
        IngredientPlanItem.objects.create(
            plan=plan,
            product=self.product,
            quantity=Decimal("1"),
            unit="unit",
        )

        response = self.client.post("/recipe-planner/", data={"clear_ingredients": "1", "horizon": "week"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cleared the current ingredient list.")
        self.assertEqual(plan.items.count(), 0)


class ProductFilterTests(TestCase):
    def test_food_candidate_queryset_excludes_obvious_non_food_rows(self):
        supermarket, _ = Supermarket.objects.get_or_create(name="Albert Heijn", slug="albert-heijn")
        food = Product.objects.create(
            supermarket=supermarket,
            name="AH Bananen tros",
            external_id="wi100001",
            source_url="https://www.ah.nl/producten/product/wi100001/ah-bananen-tros",
        )
        Product.objects.create(
            supermarket=supermarket,
            name="Bol.com e-gift 10 euro",
            external_id="wi100002",
            source_url="https://www.ah.nl/producten/product/wi100002/bolcom-e-gift-10-euro",
        )
        filtered = list(food_candidate_queryset(Product.objects.filter(supermarket=supermarket)))
        self.assertEqual([item.id for item in filtered], [food.id])


class ProductQualityTests(TestCase):
    def setUp(self):
        self.supermarket, _ = Supermarket.objects.get_or_create(name="Albert Heijn", slug="albert-heijn")
        self.product = Product.objects.create(
            supermarket=self.supermarket,
            name="AH Avocado eetrijp",
            brand="AH",
            category_name="Fruit, verse sappen",
            subcategory_name="Avocado",
            package_size="2 stuks",
            description="Rijpe avocado's voor direct gebruik.",
            ingredients="Avocado",
            external_id="wi555001",
            source_url="https://www.ah.nl/producten/product/wi555001/ah-avocado-eetrijp",
        )

    def test_enricher_upserts_quality_profile(self):
        mock_client = type(
            "MockClient",
            (),
            {
                "chat_json": staticmethod(
                    lambda **kwargs: (
                        '{"confidence_label":"medium"}',
                        {
                            "confidence_label": "medium",
                            "assumptions_text": "Whole, uncut avocado at normal home temperatures.",
                            "storage_notes": "Ambient until ripe, then refrigerate.",
                            "ambient_days_min": 2,
                            "ambient_days_max": 4,
                            "refrigerated_days_min": 3,
                            "refrigerated_days_max": 6,
                            "frozen_days_min": 30,
                            "frozen_days_max": 90,
                            "nutrient_degradation_summary": "Vitamin C declines first.",
                            "nutrient_degradation_json": {"sensitive_nutrients": ["vitamin c"]},
                            "spoilage_summary": "Softening, browning, sour odor.",
                            "odor_notes": "Sour or fermented smell.",
                            "color_change_notes": "Brown or black flesh develops.",
                            "texture_change_notes": "Mushy texture.",
                            "visible_signs_json": ["dark flesh"],
                            "spoilage_processes_json": ["enzymatic browning", "microbial spoilage"],
                            "airborne_molecules_json": [{"name": "ethanol"}],
                            "sensor_targets_json": [{"target": "ethanol", "sensor_type": "MOS"}],
                            "safety_risk_notes": "Discard if mold or off odor is present.",
                            "discard_guidance": "Discard once sour, moldy, or leaking.",
                        },
                    )
                )
            },
        )()
        enricher = ProductQualityEnricher(source_name="test_llm", client=mock_client)

        profile = enricher.enrich_product(self.product)

        self.assertEqual(profile.product, self.product)
        self.assertEqual(profile.source_type, ProductQualityProfile.SourceType.LLM)
        self.assertEqual(profile.source_name, "test_llm")
        self.assertEqual(profile.ambient_days_max, 4)
        self.assertEqual(profile.refrigerated_days_max, 6)
        self.assertEqual(profile.airborne_molecules_json, [{"name": "ethanol"}])
        self.assertEqual(profile.sensor_targets_json, [{"target": "ethanol", "sensor_type": "MOS"}])

    @patch("catalog.management.commands.enrich_product_quality.ProductQualityEnricher.enrich_product")
    def test_quality_command_creates_profiles_for_matching_products(self, mock_enrich):
        mock_enrich.side_effect = lambda product: ProductQualityProfile.objects.create(
            product=product,
            source_type=ProductQualityProfile.SourceType.LLM,
            source_name="vllm_default",
            raw_response_json={"ok": True},
        )

        from django.core.management import call_command

        call_command(
            "enrich_product_quality",
            "--limit",
            "1",
            "--name-contains",
            "Avocado",
            "--source-name",
            "vllm_default",
            "--missing-only",
        )

        self.assertTrue(
            ProductQualityProfile.objects.filter(
                product=self.product,
                source_type=ProductQualityProfile.SourceType.LLM,
                source_name="vllm_default",
            ).exists()
        )
