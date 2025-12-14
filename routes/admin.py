from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin

from .models import Route, Stop


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


@admin.register(Stop)
class StopAdmin(GISModelAdmin):
    """
    Admin configuration for Stop model.
    Provides basic interface for managing stops in Django admin.
    """

    list_display = (
        "route",
        "order",
        "name",
        "added_at",
    )
    list_filter = ("route", "added_at")
    search_fields = ("name", "route__name")
    readonly_fields = ("added_at",)
    ordering = ("route", "order")

    gis_widget_kwargs = {
        "attrs": {
            "default_lon": 12.4964,
            "default_lat": 41.9028,
            "default_zoom": 10,
        }
    }
