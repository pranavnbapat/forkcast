from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Supermarket(TimeStampedModel):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=50, unique=True)
    homepage = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class CrawlSource(TimeStampedModel):
    class SourceType(models.TextChoices):
        CATALOG = "catalog", "Catalog"
        BONUS = "bonus", "Bonus"
        CATEGORY = "category", "Category"
        BRAND = "brand", "Brand"
        LISTING = "listing", "Listing"

    supermarket = models.ForeignKey(
        Supermarket,
        on_delete=models.CASCADE,
        related_name="crawl_sources",
    )
    name = models.CharField(max_length=255)
    source_type = models.CharField(
        max_length=20,
        choices=SourceType.choices,
        default=SourceType.LISTING,
    )
    url = models.URLField()
    is_active = models.BooleanField(default=True)
    last_crawled_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    class Meta:
        ordering = ["supermarket__name", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["supermarket", "url"],
                name="unique_crawl_source_url_per_supermarket",
            )
        ]

    def __str__(self) -> str:
        return f"{self.supermarket.slug}: {self.name}"


class ImportRun(TimeStampedModel):
    supermarket = models.ForeignKey(
        Supermarket,
        on_delete=models.CASCADE,
        related_name="import_runs",
    )
    importer = models.CharField(max_length=50)
    mode = models.CharField(max_length=50)
    query = models.CharField(max_length=120, blank=True)
    sort_on = models.CharField(max_length=50, blank=True)
    start_page = models.PositiveIntegerField(default=0)
    pages_visited = models.PositiveIntegerField(default=0)
    rows_imported = models.PositiveIntegerField(default=0)
    unique_products_added = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, default="completed")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        label = self.query or self.mode
        return f"{self.supermarket.slug}: {label} ({self.sort_on or 'default'})"


class Product(TimeStampedModel):
    supermarket = models.ForeignKey(
        Supermarket,
        on_delete=models.CASCADE,
        related_name="products",
    )
    name = models.CharField(max_length=255)
    brand = models.CharField(max_length=255, blank=True)
    source_url = models.URLField()
    external_id = models.CharField(max_length=120, blank=True)
    category_name = models.CharField(max_length=120, blank=True)
    subcategory_name = models.CharField(max_length=120, blank=True)
    package_size = models.CharField(max_length=120, blank=True)
    description = models.TextField(blank=True)
    ingredients = models.TextField(blank=True)
    allergen_info = models.TextField(blank=True)
    image_url = models.URLField(blank=True)
    nutri_score_grade = models.CharField(max_length=5, blank=True)
    nutri_score_label = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)
    last_scraped_at = models.DateTimeField(null=True, blank=True)
    nutrition_last_attempted_at = models.DateTimeField(null=True, blank=True)
    nutrition_unavailable = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["supermarket", "source_url"],
                name="unique_product_url_per_supermarket",
            ),
            models.UniqueConstraint(
                fields=["supermarket", "external_id"],
                name="unique_product_external_id_per_supermarket",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.supermarket.slug}: {self.name}"


class NutritionFacts(TimeStampedModel):
    product = models.OneToOneField(
        Product,
        on_delete=models.CASCADE,
        related_name="nutrition_facts",
    )
    serving_size = models.CharField(max_length=120, blank=True)
    declaration_basis = models.CharField(max_length=255, blank=True)
    amount_column_label = models.CharField(max_length=120, blank=True)
    reference_intake_column_label = models.CharField(max_length=120, blank=True)
    reference_intake_note = models.TextField(blank=True)
    energy_kj = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    energy_kcal = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    fat_g = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    saturates_g = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    unsaturated_g = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    carbohydrates_g = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    sugars_g = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    fiber_g = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    starch_g = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    protein_g = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    salt_g = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    estimated_energy_kj = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    estimated_energy_kcal = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    calorie_score = models.DecimalField(max_digits=8, decimal_places=5, null=True, blank=True)
    protein_score = models.DecimalField(max_digits=8, decimal_places=5, null=True, blank=True)
    carbohydrates_score = models.DecimalField(max_digits=8, decimal_places=5, null=True, blank=True)
    fibre_score = models.DecimalField(max_digits=8, decimal_places=5, null=True, blank=True)
    saturated_fats_score = models.DecimalField(max_digits=8, decimal_places=5, null=True, blank=True)
    unsaturated_fats_score = models.DecimalField(max_digits=8, decimal_places=5, null=True, blank=True)
    balanced_score = models.DecimalField(max_digits=8, decimal_places=5, null=True, blank=True)
    raw_text = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "nutrition facts"

    def __str__(self) -> str:
        return f"Nutrition for {self.product.name}"


class NutritionEntry(TimeStampedModel):
    nutrition_facts = models.ForeignKey(
        NutritionFacts,
        on_delete=models.CASCADE,
        related_name="entries",
    )
    position = models.PositiveIntegerField(default=0)
    label = models.CharField(max_length=255)
    value_text = models.CharField(max_length=255, blank=True)
    reference_intake_text = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["nutrition_facts", "position"],
                name="unique_nutrition_entry_position_per_factset",
            )
        ]

    def __str__(self) -> str:
        return f"{self.label}: {self.value_text}"


class ProductSnapshot(TimeStampedModel):
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="snapshots",
    )
    price_amount = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    price_text = models.CharField(max_length=120, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    scraped_at = models.DateTimeField()

    class Meta:
        ordering = ["-scraped_at"]

    def __str__(self) -> str:
        return f"Snapshot for {self.product.name} at {self.scraped_at:%Y-%m-%d %H:%M}"


class ProductQualityProfile(TimeStampedModel):
    class SourceType(models.TextChoices):
        LLM = "llm", "LLM"
        MANUAL = "manual", "Manual"
        PAPER = "paper", "Paper"
        WEB = "web", "Web"
        SENSOR = "sensor", "Sensor"

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="quality_profiles",
    )
    source_type = models.CharField(max_length=20, choices=SourceType.choices, default=SourceType.LLM)
    source_name = models.CharField(max_length=120, default="default")
    source_reference = models.URLField(blank=True)
    is_estimated = models.BooleanField(default=True)
    confidence_label = models.CharField(max_length=20, blank=True)
    assumptions_text = models.TextField(blank=True)
    storage_notes = models.TextField(blank=True)
    ambient_days_min = models.PositiveIntegerField(null=True, blank=True)
    ambient_days_max = models.PositiveIntegerField(null=True, blank=True)
    refrigerated_days_min = models.PositiveIntegerField(null=True, blank=True)
    refrigerated_days_max = models.PositiveIntegerField(null=True, blank=True)
    frozen_days_min = models.PositiveIntegerField(null=True, blank=True)
    frozen_days_max = models.PositiveIntegerField(null=True, blank=True)
    nutrient_degradation_summary = models.TextField(blank=True)
    nutrient_degradation_json = models.JSONField(default=dict, blank=True)
    spoilage_summary = models.TextField(blank=True)
    odor_notes = models.TextField(blank=True)
    color_change_notes = models.TextField(blank=True)
    texture_change_notes = models.TextField(blank=True)
    visible_signs_json = models.JSONField(default=list, blank=True)
    spoilage_processes_json = models.JSONField(default=list, blank=True)
    airborne_molecules_json = models.JSONField(default=list, blank=True)
    sensor_targets_json = models.JSONField(default=list, blank=True)
    safety_risk_notes = models.TextField(blank=True)
    discard_guidance = models.TextField(blank=True)
    raw_response_text = models.TextField(blank=True)
    raw_response_json = models.JSONField(default=dict, blank=True)
    last_generated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["product__name", "source_type", "source_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "source_type", "source_name"],
                name="unique_quality_profile_per_product_source",
            )
        ]

    def __str__(self) -> str:
        return f"{self.product.name} quality ({self.source_type}:{self.source_name})"


class Goal(TimeStampedModel):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=50, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class CategoryScope(TimeStampedModel):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=80, unique=True)
    taxonomy_id = models.PositiveIntegerField(null=True, blank=True)
    source_url = models.URLField(blank=True)
    is_food = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class CultureOption(TimeStampedModel):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=60, unique=True)
    region = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class CuisineOption(TimeStampedModel):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=60, unique=True)
    region = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class PlannerProfile(TimeStampedModel):
    class Gender(models.TextChoices):
        FEMALE = "female", "Female"
        MALE = "male", "Male"
        NON_BINARY = "non_binary", "Non-binary"
        OTHER = "other", "Other"
        PREFER_NOT_TO_SAY = "prefer_not_to_say", "Prefer not to say"

    class Lifestyle(models.TextChoices):
        SEDENTARY = "sedentary", "Sedentary"
        LIGHTLY_ACTIVE = "lightly_active", "Lightly active"
        ACTIVE = "active", "Active"
        VERY_ACTIVE = "very_active", "Very active"

    class DietStyle(models.TextChoices):
        OMNIVORE = "omnivore", "Omnivore"
        VEGETARIAN = "vegetarian", "Vegetarian"
        VEGAN = "vegan", "Vegan"
        PESCATARIAN = "pescatarian", "Pescatarian"
        POULTRY = "poultry", "Poultry-focused"
        MEAT_BASED = "meat_based", "Meat-based"

    name = models.CharField(max_length=120, unique=True, default="Default Planner Profile")
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="planner_profile",
        null=True,
        blank=True,
    )
    primary_goal = models.ForeignKey(
        Goal,
        on_delete=models.PROTECT,
        related_name="primary_profiles",
        null=True,
        blank=True,
    )
    secondary_goal = models.ForeignKey(
        Goal,
        on_delete=models.PROTECT,
        related_name="secondary_profiles",
        null=True,
        blank=True,
    )
    gender = models.CharField(max_length=30, choices=Gender.choices, blank=True)
    age = models.PositiveIntegerField(null=True, blank=True)
    height_cm = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    weight_kg = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    culture_option = models.ForeignKey(
        CultureOption,
        on_delete=models.SET_NULL,
        related_name="profiles",
        null=True,
        blank=True,
    )
    cuisine_option = models.ForeignKey(
        CuisineOption,
        on_delete=models.SET_NULL,
        related_name="profiles",
        null=True,
        blank=True,
    )
    culture = models.CharField(max_length=120, blank=True)
    lifestyle = models.CharField(max_length=30, choices=Lifestyle.choices, blank=True)
    fasting_pattern = models.CharField(max_length=120, blank=True)
    diet_style = models.CharField(max_length=30, choices=DietStyle.choices, blank=True)
    allergies = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class IngredientPlan(TimeStampedModel):
    class Horizon(models.TextChoices):
        TOMORROW = "tomorrow", "Tomorrow"
        FEW_DAYS = "few_days", "Coming days"
        WEEK = "week", "Week"
        MONTH = "month", "Month"

    name = models.CharField(max_length=120, unique=True, default="Default Ingredient Plan")
    profile = models.ForeignKey(
        PlannerProfile,
        on_delete=models.SET_NULL,
        related_name="ingredient_plans",
        null=True,
        blank=True,
    )
    horizon = models.CharField(max_length=20, choices=Horizon.choices, default=Horizon.WEEK)
    notes = models.TextField(blank=True)
    last_generated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return self.name


class IngredientPlanItem(TimeStampedModel):
    plan = models.ForeignKey(
        IngredientPlan,
        on_delete=models.CASCADE,
        related_name="items",
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="ingredient_plan_items",
    )
    quantity = models.DecimalField(max_digits=8, decimal_places=2, default=1)
    unit = models.CharField(max_length=30, default="unit")
    is_pantry_staple = models.BooleanField(default=False)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["product__name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["plan", "product"],
                name="unique_product_per_ingredient_plan",
            )
        ]

    def __str__(self) -> str:
        return f"{self.product.name} x {self.quantity} {self.unit}"


class RecipeSuggestionRun(TimeStampedModel):
    plan = models.ForeignKey(
        IngredientPlan,
        on_delete=models.CASCADE,
        related_name="recipe_runs",
    )
    profile = models.ForeignKey(
        PlannerProfile,
        on_delete=models.SET_NULL,
        related_name="recipe_runs",
        null=True,
        blank=True,
    )
    model_name = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=20, default="completed")
    prompt_text = models.TextField(blank=True)
    response_text = models.TextField(blank=True)
    response_json = models.JSONField(default=dict, blank=True)
    error_text = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.plan.name} at {self.created_at:%Y-%m-%d %H:%M}"


class IngredientImageAnalysis(TimeStampedModel):
    plan = models.ForeignKey(
        IngredientPlan,
        on_delete=models.CASCADE,
        related_name="image_analyses",
    )
    profile = models.ForeignKey(
        PlannerProfile,
        on_delete=models.SET_NULL,
        related_name="image_analyses",
        null=True,
        blank=True,
    )
    image = models.FileField(upload_to="planner_uploads/%Y/%m/%d")
    model_name = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=20, default="completed")
    prompt_text = models.TextField(blank=True)
    response_text = models.TextField(blank=True)
    response_json = models.JSONField(default=dict, blank=True)
    extracted_items = models.JSONField(default=list, blank=True)
    error_text = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Image analysis for {self.plan.name} at {self.created_at:%Y-%m-%d %H:%M}"
