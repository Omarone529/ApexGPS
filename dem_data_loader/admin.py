from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin

from .models import DEMTile, ElevationQuery


@admin.register(DEMTile)
class DEMTileAdmin(GISModelAdmin):
    """Admin interface for DEM tiles."""

    list_display = ("rid", "filename")
    readonly_fields = ("rid", "rast", "filename")
    search_fields = ("filename",)

    def has_add_permission(self, request):
        """Check if user has permission to add DEM tiles."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Check if user has permission to delete DEM tiles."""
        return False


@admin.register(ElevationQuery)
class ElevationQueryAdmin(admin.ModelAdmin):
    """Admin interface for elevation queries."""

    list_display = (
        "name",
        "latitude",
        "longitude",
        "elevation",
        "success",
        "queried_at",
    )
    list_filter = ("success", "queried_at")
    search_fields = ("name",)
    readonly_fields = ("elevation", "success", "queried_at")

    fieldsets = (
        ("Query Information", {"fields": ("name", "latitude", "longitude")}),
        ("Results", {"fields": ("elevation", "success", "queried_at")}),
    )
