from __future__ import annotations

import atexit
import fcntl
import json
import os
import sys
import threading
from contextlib import suppress

from django.utils import timezone

from catalog.models import ImportRun, NutritionFacts, Product, Supermarket
from catalog.services.ah_api import AHAPIError, AHAPIImporter
from catalog.services.dedupe import dedupe_products_by_external_id
from catalog.services.opensearch import OpenSearchError, ProductOpenSearchIndex
from catalog.services.product_filters import food_candidate_queryset


_startup_thread_started = False
_startup_lock_handle = None


def should_start_background_sync() -> bool:
    if os.getenv("FORKCAST_AUTO_SYNC_ON_START", "1") != "1":
        return False
    blocked_commands = {
        "makemigrations",
        "migrate",
        "collectstatic",
        "shell",
        "dbshell",
        "createsuperuser",
        "bootstrap_superuser",
        "test",
    }
    if any(command in sys.argv for command in blocked_commands):
        return False
    if "runserver" in sys.argv:
        return os.getenv("RUN_MAIN") == "true"
    return False


def start_background_sync() -> None:
    global _startup_thread_started
    if _startup_thread_started or not should_start_background_sync():
        return
    if not acquire_process_lock():
        return

    thread = threading.Thread(target=run_startup_sync, name="catalog-startup-sync", daemon=True)
    thread.start()
    _startup_thread_started = True


def acquire_process_lock() -> bool:
    global _startup_lock_handle
    lock_path = os.getenv("FORKCAST_AUTO_SYNC_LOCK_PATH", "/tmp/forkcast_ah_autosync.lock")
    handle = open(lock_path, "w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _startup_lock_handle = handle
    atexit.register(release_process_lock)
    return True


def release_process_lock() -> None:
    global _startup_lock_handle
    if _startup_lock_handle is None:
        return
    with suppress(OSError):
        fcntl.flock(_startup_lock_handle.fileno(), fcntl.LOCK_UN)
    _startup_lock_handle.close()
    _startup_lock_handle = None


def startup_status_path() -> str:
    return os.getenv("FORKCAST_AUTO_SYNC_STATUS_PATH", "/tmp/forkcast_ah_autosync_status.json")


def write_startup_status(**payload) -> None:
    data = {
        "updated_at": timezone.now().isoformat(),
        **payload,
    }
    with open(startup_status_path(), "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)


def read_startup_status() -> dict:
    path = startup_status_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}


def run_startup_sync() -> None:
    supermarket = Supermarket.objects.get(slug="albert-heijn")
    importer = AHAPIImporter(supermarket=supermarket)
    batch_size = int(os.getenv("FORKCAST_AUTO_NUTRITION_BATCH_SIZE", "2000"))
    max_partition_pages = int(os.getenv("FORKCAST_AUTO_PARTITION_PAGES", "25"))
    broad_pages = int(os.getenv("FORKCAST_AUTO_BROAD_PAGES", "20"))
    progress_every = max(1, int(os.getenv("FORKCAST_AUTO_PROGRESS_EVERY", "25")))
    opensearch_batch_size = int(os.getenv("FORKCAST_AUTO_OPENSEARCH_BATCH_SIZE", "1000"))
    auto_reindex_opensearch = os.getenv("FORKCAST_AUTO_OPENSEARCH_REINDEX", "0") == "1"

    try:
        write_startup_status(
            running=True,
            phase="startup",
            message="Starting AH startup sync.",
            pid=os.getpid(),
        )
        removed = dedupe_products_by_external_id(supermarket)
        write_startup_status(
            running=True,
            phase="dedupe",
            message="Initial product dedupe completed.",
            removed_duplicates=removed,
            pid=os.getpid(),
        )
        ImportRun.objects.create(
            supermarket=supermarket,
            importer="ah_api",
            mode="startup_dedupe",
            query="",
            sort_on="",
            start_page=0,
            pages_visited=0,
            rows_imported=0,
            unique_products_added=0,
            status="completed",
            notes=f"removed_duplicates={removed}",
        )

        before_products = Product.objects.filter(supermarket=supermarket).count()
        total_pages = 0
        total_rows = 0
        for sort_on in ["RELEVANCE", "PRICEHIGHLOW", "PRICELOWHIGH", "NUTRISCORE"]:
            write_startup_status(
                running=True,
                phase="inventory_sync",
                message=f"Refreshing broad AH inventory slice {sort_on}.",
                current_sort=sort_on,
                total_pages=total_pages,
                total_rows=total_rows,
                pid=os.getpid(),
            )
            result = importer.import_search_pages(
                query="",
                start_page=0,
                max_pages=broad_pages,
                page_size=100,
                sort_on=sort_on,
            )
            total_pages += result["visited_pages"]
            total_rows += result["imported_products"]
            ImportRun.objects.create(
                supermarket=supermarket,
                importer="ah_api",
                mode="startup_inventory_sync",
                query="",
                sort_on=sort_on,
                start_page=0,
                pages_visited=result["visited_pages"],
                rows_imported=result["imported_products"],
                unique_products_added=0,
                status="completed",
            )

        partition_result = importer.import_query_partitions(
            partitions=importer.default_single_char_partitions(),
            max_pages_per_partition=max_partition_pages,
            page_size=100,
            sort_on="RELEVANCE",
        )
        total_pages += partition_result["total_pages"]
        total_rows += partition_result["total_imported"]
        removed_after_import = dedupe_products_by_external_id(supermarket)
        after_products = Product.objects.filter(supermarket=supermarket).count()
        write_startup_status(
            running=True,
            phase="inventory_sync",
            message="Partition import completed.",
            total_pages=total_pages,
            total_rows=total_rows,
            removed_duplicates=removed + removed_after_import,
            current_products=after_products,
            pid=os.getpid(),
        )
        ImportRun.objects.create(
            supermarket=supermarket,
            importer="ah_api",
            mode="startup_inventory_sync",
            query="single_chars",
            sort_on="RELEVANCE",
            start_page=0,
            pages_visited=partition_result["total_pages"],
            rows_imported=partition_result["total_imported"],
            unique_products_added=max(0, after_products - before_products),
            status="completed",
            notes=f"total_pages={total_pages}; total_rows={total_rows}",
        )

        batch_number = 0
        while True:
            product_ids = list(
                _missing_nutrition_queryset(supermarket)
                .values_list("id", flat=True)
                .order_by("id")[:batch_size]
            )
            if not product_ids:
                break
            scraped = 0
            batch_number += 1
            write_startup_status(
                running=True,
                phase="nutrition_backfill",
                message=f"Backfilling nutrition batch {batch_number}.",
                batch_number=batch_number,
                batch_size=batch_size,
                remaining_missing=_missing_nutrition_queryset(supermarket).count(),
                pid=os.getpid(),
            )
            for product in Product.objects.filter(id__in=product_ids).order_by("id"):
                importer.sync_product_from_url(product.source_url)
                scraped += 1
                if scraped % progress_every == 0 or scraped == len(product_ids):
                    write_startup_status(
                        running=True,
                        phase="nutrition_backfill",
                        message=f"Processing nutrition batch {batch_number}.",
                        batch_number=batch_number,
                        batch_size=batch_size,
                        batch_scraped=scraped,
                        batch_target=len(product_ids),
                        current_product_id=product.id,
                        current_product_name=product.name,
                        pid=os.getpid(),
                    )
            remaining = _missing_nutrition_queryset(supermarket).count()
            write_startup_status(
                running=True,
                phase="nutrition_backfill",
                message=f"Completed nutrition batch {batch_number}.",
                batch_number=batch_number,
                batch_size=batch_size,
                last_batch_scraped=scraped,
                remaining_missing=remaining,
                pid=os.getpid(),
            )
            ImportRun.objects.create(
                supermarket=supermarket,
                importer="ah_api",
                mode="startup_nutrition_backfill",
                query="",
                sort_on="",
                start_page=0,
                pages_visited=0,
                rows_imported=scraped,
                unique_products_added=0,
                status="completed",
                notes=f"batch_number={batch_number}; remaining_missing={remaining}",
            )
        if auto_reindex_opensearch:
            write_startup_status(
                running=True,
                phase="opensearch_reindex",
                message="Reindexing OpenSearch from local catalog.",
                total_pages=total_pages,
                total_rows=total_rows,
                pid=os.getpid(),
            )
            try:
                reindexed = ProductOpenSearchIndex().bulk_index_queryset(
                    food_candidate_queryset(
                        Product.objects.filter(supermarket=supermarket, is_active=True).select_related("nutrition_facts")
                    ).order_by("id"),
                    batch_size=opensearch_batch_size,
                )
                ImportRun.objects.create(
                    supermarket=supermarket,
                    importer="opensearch",
                    mode="startup_reindex",
                    query="",
                    sort_on="",
                    start_page=0,
                    pages_visited=0,
                    rows_imported=reindexed,
                    unique_products_added=0,
                    status="completed",
                    notes=f"batch_size={opensearch_batch_size}",
                )
            except OpenSearchError as exc:
                ImportRun.objects.create(
                    supermarket=supermarket,
                    importer="opensearch",
                    mode="startup_reindex",
                    query="",
                    sort_on="",
                    start_page=0,
                    pages_visited=0,
                    rows_imported=0,
                    unique_products_added=0,
                    status="failed",
                    notes=str(exc),
                )
                write_startup_status(
                    running=False,
                    phase="failed",
                    message="AH startup sync failed during OpenSearch reindex.",
                    error=str(exc),
                    pid=os.getpid(),
                )
                return

        write_startup_status(
            running=False,
            phase="completed",
            message="AH startup sync completed.",
            total_pages=total_pages,
            total_rows=total_rows,
            remaining_missing=_missing_nutrition_queryset(supermarket).count(),
            pid=os.getpid(),
        )
    except Exception as exc:
        write_startup_status(
            running=False,
            phase="failed",
            message="AH startup sync failed.",
            error=str(exc),
            pid=os.getpid(),
        )
        ImportRun.objects.create(
            supermarket=supermarket,
            importer="ah_api",
            mode="startup_sync",
            query="",
            sort_on="",
            start_page=0,
            pages_visited=0,
            rows_imported=0,
            unique_products_added=0,
            status="failed",
            notes=str(exc),
        )
    finally:
        importer.close()


def _missing_nutrition_queryset(supermarket: Supermarket):
    return food_candidate_queryset(
        Product.objects.filter(
            supermarket=supermarket,
            is_active=True,
            nutrition_unavailable=False,
        )
    ).exclude(
        id__in=NutritionFacts.objects.filter(
            product__supermarket=supermarket,
            entries__isnull=False,
        ).values_list("product_id", flat=True)
    )
