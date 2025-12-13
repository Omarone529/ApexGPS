from django.apps import AppConfig


class DemDataLoaderConfig(AppConfig):
    """Dem Data Configuration."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "dem_data_loader"
    verbose_name = "Gestione dati DEM"
