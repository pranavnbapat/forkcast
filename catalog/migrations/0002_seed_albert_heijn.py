from django.db import migrations


def seed_albert_heijn(apps, schema_editor):
    Supermarket = apps.get_model("catalog", "Supermarket")
    Supermarket.objects.update_or_create(
        slug="albert-heijn",
        defaults={
            "name": "Albert Heijn",
            "homepage": "https://www.ah.nl/",
            "is_active": True,
        },
    )


def unseed_albert_heijn(apps, schema_editor):
    Supermarket = apps.get_model("catalog", "Supermarket")
    Supermarket.objects.filter(slug="albert-heijn").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_albert_heijn, unseed_albert_heijn),
    ]
