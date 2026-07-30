from django.db import migrations


def seed_ah_crawl_sources(apps, schema_editor):
    CrawlSource = apps.get_model("catalog", "CrawlSource")
    Supermarket = apps.get_model("catalog", "Supermarket")

    supermarket = Supermarket.objects.get(slug="albert-heijn")
    CrawlSource.objects.update_or_create(
        supermarket=supermarket,
        url="https://www.ah.nl/producten",
        defaults={"name": "AH catalog", "source_type": "catalog", "is_active": True},
    )
    CrawlSource.objects.update_or_create(
        supermarket=supermarket,
        url="https://www.ah.nl/bonus",
        defaults={"name": "AH bonus", "source_type": "bonus", "is_active": True},
    )


def unseed_ah_crawl_sources(apps, schema_editor):
    CrawlSource = apps.get_model("catalog", "CrawlSource")
    CrawlSource.objects.filter(url__in=["https://www.ah.nl/producten", "https://www.ah.nl/bonus"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0003_crawlsource"),
    ]

    operations = [
        migrations.RunPython(seed_ah_crawl_sources, unseed_ah_crawl_sources),
    ]
