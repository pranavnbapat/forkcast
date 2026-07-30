from django.db import migrations, models


def seed_category_scope(apps, schema_editor):
    CategoryScope = apps.get_model("catalog", "CategoryScope")
    allowed = [
        ("Pasen", "pasen", "https://www.ah.nl/producten/21024/pasen"),
        ("Groente, aardappelen", "groente-aardappelen", "https://www.ah.nl/producten/6401/groente-aardappelen"),
        ("Fruit, verse sappen", "fruit-verse-sappen", "https://www.ah.nl/producten/20885/fruit-verse-sappen"),
        ("Bakkerij", "bakkerij", "https://www.ah.nl/producten/1355/bakkerij"),
        ("Zuivel, eieren", "zuivel-eieren", "https://www.ah.nl/producten/1730/zuivel-eieren"),
        ("Vlees", "vlees", ""),
        ("Vis", "vis", ""),
        ("Vegetarisch, vegan en plantaardig", "vegetarisch-vegan-plantaardig", ""),
        ("Maaltijden, salades", "maaltijden-salades", ""),
        ("Kaas", "kaas", ""),
        ("Vleeswaren", "vleeswaren", ""),
        ("Diepvries", "diepvries", ""),
        ("Borrel, chips, snacks", "borrel-chips-snacks", ""),
        ("Koek, snoep, chocolade", "koek-snoep-chocolade", ""),
        ("Koffie, thee", "koffie-thee", ""),
        ("Frisdrank, sappen, water", "frisdrank-sappen-water", ""),
        ("Bier, wijn, aperitieven", "bier-wijn-aperitieven", ""),
        ("Ontbijtgranen, beleg", "ontbijtgranen-beleg", ""),
        ("Pasta, rijst, wereldkeuken", "pasta-rijst-wereldkeuken", ""),
        ("Soepen, sauzen, kruiden, olie", "soepen-sauzen-kruiden-olie", ""),
        ("Tussendoortjes", "tussendoortjes", ""),
        ("Glutenvrij", "glutenvrij", ""),
    ]
    blocked = [
        ("Koken, tafelen, vrije tijd", "koken-tafelen-vrije-tijd", "https://www.ah.nl/producten/1057/koken-tafelen-vrije-tijd"),
        ("Baby en kind", "baby-en-kind", "https://www.ah.nl/producten/18521/baby-en-kind"),
        ("Drogisterij", "drogisterij", ""),
        ("Huishouden", "huishouden", ""),
        ("Huisdier", "huisdier", ""),
        ("Gezondheid en sport", "gezondheid-sport", ""),
        ("AH Voordeelshop", "ah-voordeelshop", ""),
    ]
    for name, slug, url in allowed:
        CategoryScope.objects.get_or_create(
            slug=slug,
            defaults={"name": name, "source_url": url, "is_food": True, "is_active": True},
        )
    for name, slug, url in blocked:
        CategoryScope.objects.get_or_create(
            slug=slug,
            defaults={"name": name, "source_url": url, "is_food": False, "is_active": True},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0012_seed_environment_goals"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="category_name",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="product",
            name="subcategory_name",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.CreateModel(
            name="CategoryScope",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=120, unique=True)),
                ("slug", models.SlugField(max_length=80, unique=True)),
                ("source_url", models.URLField(blank=True)),
                ("is_food", models.BooleanField(default=True)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.RunPython(seed_category_scope, migrations.RunPython.noop),
    ]
