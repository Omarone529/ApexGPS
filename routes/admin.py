from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin

from .models import Route


@admin.register(Route)
class RouteAdmin(GISModelAdmin):
    """
    Admin configuration for Route model.
    Provides interface for managing routes in Django admin,
    including spatial visualization capabilities.
    """

    list_display = (
        "name",
        "owner",
        "visibility",
        "distance_km",
        "estimated_time_min",
        "created_at",
    )
    list_filter = ("visibility", "preference", "created_at", "owner")
    search_fields = ("name", "owner__username", "owner__email")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-created_at",)
    gis_widget_kwargs = {
        "attrs": {
            "default_lon": 12.4964,  # Rome coordinates
            "default_lat": 41.9028,
            "default_zoom": 6,
        }
    }
