from django.apps import AppConfig


class CatalogConfig(AppConfig):
    name = "catalog"

    def ready(self):
        import catalog.signals  # noqa: F401
        from catalog.startup import start_background_sync

        start_background_sync()
