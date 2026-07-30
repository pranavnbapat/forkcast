# ForkCast

Django project that builds a local supermarket product inventory, starting with Albert Heijn, and uses it for promotion-aware, goal-conditioned meal planning.

The project currently focuses on:
- storing AH products in MySQL
- keeping the workflow admin-first via `/admin`
- importing AH inventory through verified mobile/API endpoints instead of brittle website scraping
- preserving source-language product and nutrition data where the API exposes it

## Current Scope

Implemented today:
- lean Django project with one app: `catalog`
- MySQL + phpMyAdmin via Docker Compose
- Django admin for supermarkets, crawl sources, products, nutrition, and snapshots
- LLM-based product quality enrichment for shelf life, spoilage, and sensor-target signals
- local nutrition search UI at `/` backed by the imported catalog
- shopping-list price UI at `/shopping-list/`
- recipe planner UI at `/recipe-planner/`
- streaming recipe generation on `/recipe-planner/stream/`
- direct BMI/BMR/TDEE calculation plus LLM-based goal explanation
- automatic startup sync that dedupes AH products, refreshes inventory, then backfills nutrition
- AH seed data
- AH API importer using:
  - anonymous token auth
  - product search
  - product detail
  - GraphQL nutrition fetch
  - bonus metadata/sections
- query-partition import command for expanding inventory coverage

Not fully solved yet:
- exact full-assortment completeness for AH
- exact ingredients / RI% / declaration note for every product from API data
- support for other supermarkets

## Why API First

The original browser/page-scraping route hit Akamai challenge pages on AH product URLs.

The verified AH mobile API path works much better:
- `POST /mobile-auth/v1/auth/token/anonymous`
- `GET /mobile-services/product/search/v2`
- `GET /mobile-services/product/detail/v4/fir/{id}`
- `POST /graphql` for nutrition rows
- `GET /mobile-services/bonuspage/v3/metadata`

This project therefore uses the API as the primary import path.

## Stack

- Python 3.12
- Django 6
- MySQL 8.4
- phpMyAdmin
- PyMySQL
- requests
- Playwright
- BeautifulSoup

## Project Layout

- [manage.py](manage.py): Django entrypoint
- [config/settings.py](config/settings.py): lean project settings
- [catalog/models.py](catalog/models.py): core schema
- [catalog/admin.py](catalog/admin.py): admin registrations and actions
- [catalog/services/ah_api.py](catalog/services/ah_api.py): current AH API importer
- [catalog/views.py](catalog/views.py): product search API + nutrition calculator view
- [catalog/services/nutrition.py](catalog/services/nutrition.py): unit conversion and nutrition aggregation
- [catalog/services/pricing.py](catalog/services/pricing.py): price, bonus, and basket-cost calculations
- [catalog/services/recipe_planner.py](catalog/services/recipe_planner.py): saved-plan context building and recipe-generation flow
- [catalog/services/health.py](catalog/services/health.py): BMI, BMR, and TDEE estimation
- [catalog/services/llm.py](catalog/services/llm.py): vLLM client
- [catalog/services/ah.py](catalog/services/ah.py): older browser/page-fetch path kept as reference/fallback
- [docker-compose.yml](docker-compose.yml): MySQL + phpMyAdmin

## Data Model

Main tables:
- `catalog_supermarket`: supermarket registry
- `catalog_crawlsource`: AH bonus/catalog source registry
- `catalog_importrun`: bookkeeping for inventory import runs
- `catalog_product`: all products across supermarkets
- `catalog_nutritionfacts`: one nutrition summary row per product
- `catalog_nutritionentry`: row-level nutrition values in source language
- `catalog_productsnapshot`: raw product snapshot + price over time
- `catalog_productqualityprofile`: source-tracked shelf-life, spoilage, degradation, and sensor-relevant quality profile
- `catalog_goal`: saved planning goals like lose weight or save money
- `catalog_cultureoption`: DB-backed culture dropdown options
- `catalog_cuisineoption`: DB-backed cuisine dropdown options
- `catalog_plannerprofile`: saved recipe-planning profile and constraints
- `catalog_ingredientplan`: saved ingredient list and planning horizon
- `catalog_ingredientplanitem`: saved products inside an ingredient plan
- `catalog_recipesuggestionrun`: persisted LLM-generated recipe suggestions

Key design decision:
- AH products are not stored in a separate physical `ah_products` table
- all products live in `catalog_product`
- supermarket-specific grouping is done through `catalog_supermarket`
- AH product uniqueness is enforced by both:
  - `catalog_product.unique_product_url_per_supermarket`
  - `catalog_product.unique_product_external_id_per_supermarket`

## Captured AH Fields

Currently captured from the verified API path when available:
- title
- brand
- package size / sales unit size
- price
- image URL
- description
- Nutri-Score
- row-level nutrition entries

Example stored correctly for banana:
- `AH Bananen tros`
- price `1.45`
- Nutri-Score `A`
- nutrition rows including:
  - `Energie`
  - `Vet`
  - `waarvan verzadigd`
  - `waarvan onverzadigd`
  - `Koolhydraten`
  - `waarvan suikers`
  - `Voedingsvezel`
  - `Eiwitten`
  - `Zout`
  - `Vitamine B6 / Pyridoxine`
  - `Kalium/Potassium`

Additional LLM-derived quality fields can now be stored per product:
- estimated ambient / refrigerated / frozen shelf-life ranges
- storage assumptions and notes
- nutrient degradation summary and structured degradation signals
- spoilage summary
- smell / odor changes
- color and texture changes
- visible spoilage signs
- likely airborne molecules or VOCs
- likely sensor targets and sensor types
- safety/discard guidance

Important caveat:
- shelf life is not a universal constant
- it varies by whole vs cut, raw vs cooked, opened vs unopened, ripe vs unripe, packaging, humidity, and storage temperature
- the LLM-backed quality profile should therefore be treated as an estimated source, not as lab-grade truth

## Local Setup

### 1. Python environment

Use the existing virtualenv or create one.

Install dependencies:

```bash
.venv/bin/python -m pip install -r requirements.txt
```

Create the runtime directories and local env file (both are gitignored):

```bash
mkdir -p logs media
cp .env.example .env
```

### 2. Start MySQL and phpMyAdmin

```bash
docker compose up -d
```

Services:
- MySQL: `127.0.0.1:3315`
- phpMyAdmin: `http://127.0.0.1:8091/`
- OpenSearch: `http://127.0.0.1:9201/`

### 3. Apply migrations

```bash
set -a && source .env && set +a
.venv/bin/python manage.py migrate
```

### 4. Bootstrap the admin user

Set `DJANGO_SUPERUSER_PASSWORD` in `.env` first. The command has no default password and fails loudly if it is blank, because it grants superuser access. Re-running it resets the password for an existing user.

```bash
set -a && source .env && set +a
.venv/bin/python manage.py bootstrap_superuser
```

### 5. Run Django

```bash
set -a && source .env && set +a
.venv/bin/python manage.py runserver
```

Admin:
- `http://127.0.0.1:8000/admin/`
- Sync status:
  - `http://127.0.0.1:8000/admin/sync-status/`
- Nutrition search:
  - `http://127.0.0.1:8000/`
- Shopping list cost:
  - `http://127.0.0.1:8000/shopping-list/`
- Recipe planner:
  - `http://127.0.0.1:8000/recipe-planner/`

## Environment

Important local env vars in `.env` (copy [.env.example](.env.example) to get started):
- `MYSQL_HOST`
- `MYSQL_PORT`
- `MYSQL_DATABASE`
- `MYSQL_USER`
- `MYSQL_PASSWORD`
- `DJANGO_SUPERUSER_USERNAME`
- `DJANGO_SUPERUSER_EMAIL`
- `DJANGO_SUPERUSER_PASSWORD`
- `OPENSEARCH_ENABLED`
- `OPENSEARCH_URL`
- `OPENSEARCH_INDEX_NAME`
- `OPENSEARCH_USERNAME`
- `OPENSEARCH_PASSWORD`
- `OPENSEARCH_VERIFY_SSL`
- `OPENSEARCH_AUTO_INDEX_ON_SAVE`
- `FORKCAST_AUTO_OPENSEARCH_REINDEX`
- `FORKCAST_AUTO_OPENSEARCH_BATCH_SIZE`

AH-related vars currently present:
- `AH_API_USER_AGENT`
- `AH_API_CLIENT_NAME`
- `AH_API_CLIENT_VERSION`
- `AH_COOKIE`
- `AH_BROWSER_*`

Startup-sync vars, all optional. These are read by [catalog/startup.py](catalog/startup.py) and fall back to the defaults shown below if absent from `.env`:
- `FORKCAST_AUTO_SYNC_ON_START` (default `1`)
- `FORKCAST_AUTO_BROAD_PAGES` (default `20`)
- `FORKCAST_AUTO_PARTITION_PAGES` (default `25`)
- `FORKCAST_AUTO_NUTRITION_BATCH_SIZE` (default `2000`)
- `FORKCAST_AUTO_PROGRESS_EVERY` (default `25`)
- `FORKCAST_AUTO_SYNC_LOCK_PATH` (default `/tmp/forkcast_ah_autosync.lock`)
- `FORKCAST_AUTO_SYNC_STATUS_PATH` (default `/tmp/forkcast_ah_autosync_status.json`)
- `FORKCAST_AUTO_OPENSEARCH_REINDEX` (default `0`)
- `FORKCAST_AUTO_OPENSEARCH_BATCH_SIZE` (default `1000`)

Recipe-planning LLM vars:
- `RUNPOD_VLLM_HOST`
- `VLLM_MODEL`
- `VLLM_API_KEY`
- `VLLM_TIMEOUT`
- `PIPELINE_MAX_CHARS`

The same vLLM configuration is used for product-quality enrichment unless you change the code to add a separate quality model endpoint.

Notes:
- the current primary importer is API-based and does not depend on browser scraping
- browser/cookie settings remain because the earlier path was explored and may still be useful as fallback/debugging
- product autocomplete/search can optionally use OpenSearch; if it is disabled or unavailable, the app falls back to DB-backed search automatically

## Manual Sync Commands

If the startup worker is not enough, run the imports manually.

### 1. Clean reset and rebuild

If you want a true clean start:

1. reset the MySQL database
2. rerun migrations
3. recreate the superuser
4. rerun the food-only AH imports

Commands used for the clean rebuild:

```bash
docker exec forkcast_mysql mysql -uroot -prootpass -e "DROP DATABASE IF EXISTS \`supermarkt\`; CREATE DATABASE \`supermarkt\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
.venv/bin/python manage.py migrate
.venv/bin/python manage.py bootstrap_superuser
```

### 2. Broad food-only inventory refresh

```bash
.venv/bin/python manage.py discover_ah_catalog --query '' --start-page 0 --max-pages 20 --sort-on RELEVANCE
.venv/bin/python manage.py discover_ah_catalog --query '' --start-page 0 --max-pages 20 --sort-on PRICEHIGHLOW
.venv/bin/python manage.py discover_ah_catalog --query '' --start-page 0 --max-pages 20 --sort-on PRICELOWHIGH
.venv/bin/python manage.py discover_ah_catalog --query '' --start-page 0 --max-pages 20 --sort-on NUTRISCORE
```

These imports now respect the DB-backed food-category scope and skip obvious non-food categories at import time.

### 2b. Exact category-by-category food import

The AH API supports an exact category filter using `taxonomyId`.
This project stores those taxonomy IDs in `CategoryScope` and can import category by category.
Because AH category IDs and labels are not perfectly stable, the importer also uses category-specific fallback queries and only keeps products whose returned `mainCategory` matches the target food category.

Run all active food categories:

```bash
.venv/bin/python manage.py import_ah_category_scope --max-pages-per-category 50 --sort-on RELEVANCE
```

Run a specific category only:

```bash
.venv/bin/python manage.py import_ah_category_scope --category-slug zuivel-eieren --max-pages-per-category 50
.venv/bin/python manage.py import_ah_category_scope --category-slug groente-aardappelen --max-pages-per-category 50
```

Disable the fallback query pass if you only want raw taxonomy-based import:

```bash
.venv/bin/python manage.py import_ah_category_scope --max-pages-per-category 50 --disable-fallback-queries
```

This is the preferred path when you want stronger completeness over the allowed food categories. Broad search imports are still useful, but category-scope import is the more systematic coverage path.

### 3. Food category scope

The importer now stores and uses a persistent category allowlist in the DB through `CategoryScope`.

Allowed food-category examples:
- `Pasen`
- `Groente, aardappelen`
- `Fruit, verse sappen`
- `Bakkerij`
- `Zuivel, eieren`
- `Vlees`
- `Vis`
- `Vegetarisch, vegan en plantaardig`
- `Maaltijden, salades`
- `Kaas`
- `Vleeswaren`
- `Diepvries`
- `Borrel, chips, snacks`
- `Koek, snoep, chocolade`
- `Koffie, thee`
- `Frisdrank, sappen, water`
- `Bier, wijn, aperitieven`
- `Ontbijtgranen, beleg`
- `Pasta, rijst, wereldkeuken`
- `Soepen, sauzen, kruiden, olie`
- `Tussendoortjes`
- `Glutenvrij`

Blocked non-food examples:
- `Koken, tafelen, vrije tijd`
- `Baby en kind`
- `Drogisterij`
- `Huishouden`
- `Huisdier`
- `Gezondheid en sport`
- `AH Voordeelshop`

You can review and edit these in `/admin` under `Category scopes`.

### 4. Query-targeted inventory refresh

Useful when a category looks under-captured in the local DB.

Examples:

```bash
.venv/bin/python manage.py discover_ah_catalog --query 'eieren' --start-page 0 --max-pages 10 --sort-on RELEVANCE
.venv/bin/python manage.py discover_ah_catalog --query 'ei' --start-page 0 --max-pages 10 --sort-on RELEVANCE
```

Important:
- the current importer is strongest on API search/query coverage
- it does not yet do full taxonomy/category traversal from every AH category page
- so category URLs such as `/producten/2335/eieren` may need a targeted query import to improve coverage

### 5. Partitioned inventory refresh

```bash
.venv/bin/python manage.py import_ah_partitions --single-chars --sort-on RELEVANCE --max-pages-per-partition 25
```

### 6. Nutrition/detail refresh

Nutrition backfill now skips obvious non-food rows and retries transient AH API failures with backoff.
If the AH API returns no nutrition rows for a product after a detail fetch, the product is marked as `nutrition unavailable` and removed from repeated nutrition retries.

For a small manual pass:

```bash
.venv/bin/python manage.py scrape_ah_products --missing-nutrition --limit 200
```

For continuous batch backfill:

```bash
.venv/bin/python manage.py backfill_ah_nutrition --batch-size 2000 --pause-seconds 1 --progress-every 25
```

## How To Check Sync Progress

### Admin UI

Open:

- `http://127.0.0.1:8000/admin/sync-status/`

This shows:
- whether the startup worker is running
- current phase
- food-candidate product counts
- remaining products without nutrition
- latest inventory run
- latest nutrition run
- latest failure

### Shell status

Read the startup status JSON:

```bash
.venv/bin/python manage.py shell -c "from catalog.startup import read_startup_status; import json; print(json.dumps(read_startup_status(), indent=2))"
```

### Live manual backfill progress

The manual nutrition backfill command now prints mid-batch progress every `--progress-every` products, for example:

```text
Batch 3: 50/2000 processed, current=1234 AH Biologisch Eieren S M L
```

### Background log tail

If you run the backfill detached:

```bash
nohup .venv/bin/python manage.py backfill_ah_nutrition --batch-size 2000 --pause-seconds 1 --progress-every 25 > logs/ah_nutrition_backfill.log 2>&1 &
tail -f logs/ah_nutrition_backfill.log
```

## Useful Commands

All commands below assume:

```bash
set -a && source .env && set +a
```

### Health checks

```bash
.venv/bin/python manage.py check
.venv/bin/python manage.py test
```

### Create/update the superuser

```bash
.venv/bin/python manage.py bootstrap_superuser
```

### Import one product by URL

```bash
.venv/bin/python manage.py scrape_ah_products --source-url https://www.ah.nl/producten/product/wi197393/ah-bananen-tros --limit 1
```

### Continuous nutrition backfill

Run until the local AH catalog is exhausted:

```bash
.venv/bin/python manage.py backfill_ah_nutrition --batch-size 2000 --pause-seconds 1
```

Run a bounded number of batches:

```bash
.venv/bin/python manage.py backfill_ah_nutrition --batch-size 2000 --pause-seconds 1 --max-batches 3
```

### Deduplicate existing AH products

```bash
.venv/bin/python manage.py dedupe_ah_products
```

## Automatic Sync On App Start

When Django starts through `runserver`, the `catalog` app now starts a guarded background worker that:

1. deduplicates AH products by `external_id`
2. refreshes inventory through broad API slices and single-character partitions
3. deduplicates again
4. backfills nutrition for products that still have no stored nutrition rows

The worker is guarded by a process lock file so it only starts once per server start/reload cycle.

Write behavior:
- products are reused by `external_id` first, then `source_url`
- unchanged product snapshots are not duplicated
- nutrition entries are only rewritten when the incoming nutrition payload changes

## Recipe Planner

The recipe planner at `/recipe-planner/` stores:
- your primary and secondary goals
- DB-backed culture and cuisine selections
- profile details that affect recipe selection
- a saved ingredient list from the AH catalog
- generated recipe suggestions from the configured vLLM endpoint

The form explicitly asks for:
- primary and secondary goal
- gender and age
- height and weight
- culture dropdown
- cuisine dropdown
- culture / cuisine preference
- lifestyle
- fasting pattern
- diet preference
- allergies / exclusions
- extra planning context

Why those details are used:
- goals determine whether the planner should bias toward fat loss, muscle gain, cost savings, plant-forward meals, and similar outcomes
- height and weight support BMI, BMR, and TDEE estimation
- culture and cuisine bias recipe style and flavor direction
- lifestyle and fasting affect portioning and meal timing assumptions
- diet preference and allergies are treated as compatibility constraints

Spices and condiments:
- pantry staples, spices, and condiments can be marked directly on the saved ingredient list
- the planner assumes those are already available and excludes them from cost-sensitive missing-item logic by default
- the page makes that assumption explicit so recipe suggestions do not waste budget on cumin, salt, oil, and similar basics unless needed

Recipe output:
- BMI, BMR, and TDEE are computed directly in code
- the LLM receives those metrics as context
- the LLM is asked to explain what those values mean for the chosen goal
- the recipe response can include:
  - metabolic context
  - goal explanation
  - calorie target
  - protein, fat, carb, and fibre guidance
  - sugar and salt guidance
  - meal distribution guidance

Streaming behavior:
- the planner no longer shows raw streamed JSON
- `Suggest Recipes` uses a streaming endpoint and updates the page progressively
- structured recipe output is rendered below the form after completion

## How To Check Background Sync

The easiest way is the admin status page:

- `http://127.0.0.1:8000/admin/sync-status/`

That page shows:
- whether the startup worker is currently running
- the current phase, such as `dedupe`, `inventory_sync`, `nutrition_backfill`, `completed`, or `failed`
- remaining products missing nutrition
- the latest inventory sync run
- the latest nutrition sync run
- the latest failure, if any

You can also inspect the worker status from the Django shell:

```bash
source .venv/bin/activate
.venv/bin/python manage.py shell -c "from catalog.startup import read_startup_status; import json; print(json.dumps(read_startup_status(), indent=2))"
```

Example fields:
- `running`
- `phase`
- `message`
- `batch_number`
- `remaining_missing`
- `updated_at`

And you can inspect historical runs in the database through:
- `/admin` -> `Import runs`

Current local example:
- if `phase` is `nutrition_backfill` and `running` is `true`, then nutritional enrichment is actively happening
- if `phase` is `inventory_sync`, then product discovery/import is happening
- if `phase` is `completed`, the current startup sync cycle has finished

### Import broad catalog slices by sort order

Relevance:

```bash
.venv/bin/python manage.py discover_ah_catalog --query '' --start-page 0 --max-pages 20 --sort-on RELEVANCE
```

Price high to low:

```bash
.venv/bin/python manage.py discover_ah_catalog --query '' --start-page 0 --max-pages 20 --sort-on PRICEHIGHLOW
```

Price low to high:

```bash
.venv/bin/python manage.py discover_ah_catalog --query '' --start-page 0 --max-pages 20 --sort-on PRICELOWHIGH
```

Nutri-Score:

```bash
.venv/bin/python manage.py discover_ah_catalog --query '' --start-page 0 --max-pages 20 --sort-on NUTRISCORE
```

### Import query partitions

Single custom partitions:

```bash
.venv/bin/python manage.py import_ah_partitions --query aa --query sb --sort-on RELEVANCE --max-pages-per-partition 2
```

All single-character partitions:

```bash
.venv/bin/python manage.py import_ah_partitions --single-chars --sort-on RELEVANCE --max-pages-per-partition 25
```

### Source-driven discovery

If you explicitly want to use stored crawl sources:

```bash
.venv/bin/python manage.py discover_ah_catalog --source-id 1 --max-pages 10
```

### Backfill richer detail

For already imported products:

```bash
.venv/bin/python manage.py scrape_ah_products --limit 100 --stale-only
```

Backfill products still missing nutrition rows:

```bash
.venv/bin/python manage.py scrape_ah_products --missing-nutrition --limit 100
```

Backfill products still missing descriptions:

```bash
.venv/bin/python manage.py scrape_ah_products --missing-description --limit 100
```

### OpenSearch product search

Minimal local setup:

```bash
docker compose up -d forkcast_opensearch
```

Enable it in `.env`:

```bash
OPENSEARCH_ENABLED=1
OPENSEARCH_URL=http://127.0.0.1:9201
OPENSEARCH_INDEX_NAME=supermarkt_products
OPENSEARCH_VERIFY_SSL=0
OPENSEARCH_AUTO_INDEX_ON_SAVE=1
FORKCAST_AUTO_OPENSEARCH_REINDEX=1
FORKCAST_AUTO_OPENSEARCH_BATCH_SIZE=1000
```

Index the current local product catalog:

```bash
set -a && source .env && set +a
.venv/bin/python manage.py index_products_opensearch --all --batch-size 1000 --recreate
```

If you prefer manual chunks:

```bash
.venv/bin/python manage.py index_products_opensearch --limit 5000 --offset 0
.venv/bin/python manage.py index_products_opensearch --limit 5000 --offset 5000
.venv/bin/python manage.py index_products_opensearch --limit 5000 --offset 10000
```

Once enabled and indexed, `/api/products/search/` will prefer OpenSearch automatically and fall back to the DB search path if OpenSearch is unavailable.

Current indexed document includes:
- product identity and URL
- brand, package size, description, ingredients, allergens
- category and subcategory
- image URL
- Nutri-Score
- latest price and bonus metadata
- nutrition summary fields
- latest LLM-derived quality/spoilage summary when available

Automation options:
- `OPENSEARCH_AUTO_INDEX_ON_SAVE=1`: update the index when products, nutrition, snapshots, or quality profiles change
- `FORKCAST_AUTO_OPENSEARCH_REINDEX=1`: after startup sync finishes, bulk reindex the local catalog into OpenSearch

### Enrich shelf life and spoilage profiles

Use the configured LLM to estimate storage life, nutrient degradation, spoilage behavior, likely odor/color/texture changes, and sensor-relevant emitted compounds.

Typical run:

```bash
set -a && source .env && set +a
.venv/bin/python manage.py enrich_product_quality --limit 100 --missing-only --source-name vllm_default
```

Useful variants:

```bash
# specific product
.venv/bin/python manage.py enrich_product_quality --product-id 123 --force

# focused subset
.venv/bin/python manage.py enrich_product_quality --name-contains avocado --limit 25 --missing-only

# rerun even if an LLM profile already exists
.venv/bin/python manage.py enrich_product_quality --limit 50 --force
```

Important assumptions:
- this stores an estimated source, not a lab-validated truth
- shelf life varies by whole vs cut, raw vs cooked, opened vs unopened, ripe vs unripe, packaging, and real storage temperature
- frozen-life ranges are stored too, because fridge-vs-ambient alone is not enough for many foods

## Admin Workflow

Available in `/admin`:
- `Supermarkets`
- `Crawl sources`
- `Import runs`
- `Products`
- `Product quality profiles`
- `Nutrition facts`
- `Product snapshots`

Useful admin actions:
- seed default AH crawl sources
- discover products from selected crawl sources
- scrape selected AH products
- generate LLM quality profiles for selected products

## Import Tracking

API import commands now create `ImportRun` rows.

This gives you:
- a history of which query/sort slices were imported
- rough row counts and pages visited
- a simple way to see repeated vs new import activity from `/admin`

Current bookkeeping is best-effort:
- `rows_imported` tracks rows processed from the API slice
- `unique_products_added` is currently tracked on broad search imports
- partition imports are recorded per partition, but not all per-partition uniqueness is computed yet

## Current Status

As of the latest local run in this workspace:
- AH inventory stored: about `12.8k` products
- API-based banana import verified
- broad import slices working across multiple sort orders
- query-partition import working and deduplicating correctly

Treat that count as a moving local state, not a hard-coded guarantee.

## Known Limitations

- AH API broad search is not a simple “all products in one request” interface
- some search strategies appear capped or behave non-intuitively
- two-character queries are not clean prefix filters in all cases
- exact ingredients and RI percentages are not consistently available from the verified API responses we use today
- completeness still needs iterative partitioning and validation

## Recommended Next Steps

- keep expanding API partitions and sort slices
- add persistence for import runs / coverage bookkeeping
- backfill detailed product data in batches
- identify a safer, verifiable strategy for the remaining uncovered AH catalog slice
- add the next supermarket once the AH pipeline is stable
