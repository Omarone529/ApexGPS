from django.contrib.gis import admin
from .models import PointOfInterest, ScenicArea, RoadSegment


class PointOfInterestAdmin(admin.GISModelAdmin):
    """Admin interface for Points of Interest."""
    list_display = ('name', 'category', 'location')
    list_filter = ('category',)
    search_fields = ('name', 'description')
    # Center map on Italy (coordinates in EPSG:3857 for web maps)
    default_lon = 1200000
    default_lat = 5000000
    default_zoom = 5


class ScenicAreaAdmin(admin.GISModelAdmin):
    """Admin interface for Scenic Areas."""
    list_display = ('name', 'area_type', 'bonus_value')
    list_filter = ('area_type',)
    search_fields = ('name',)
    # Center map on Italy
    default_lon = 1200000
    default_lat = 5000000
    default_zoom = 5

class RoadSegmentAdmin(admin.GISModelAdmin):
    """
    Admin interface for Road Segments.

    Road segments form the routing network. They can be created:
    1. Automatically from OpenStreetMap data (via management commands)
    2. Manually by admins for custom/private roads
    3. By users for their personal routes
    """

    list_display = ('name', 'highway', 'length_m', 'scenic_rating', 'source', 'target')
    list_filter = ('highway', 'surface', 'oneway')
    search_fields = ('name', 'osm_id')
    readonly_fields = ('cost_length', 'cost_time', 'cost_scenic', 'curvature', 'poi_density')
    # Center map on Italy
    default_lon = 1200000
    default_lat = 5000000
    default_zoom = 5

    fieldsets = (
        ('Identificazione', {
            'fields': ('name', 'osm_id', 'highway')
        }),
        ('Geometria', {
            'fields': ('geometry', 'length_m')
        }),
        ('Proprietà Stradali', {
            'fields': ('maxspeed', 'oneway', 'surface', 'lanes')
        }),
        ('Metriche Panoramiche', {
            'fields': ('scenic_rating', 'elevation_gain', 'curvature', 'poi_density')
        }),
        ('Routing Graph', {
            'fields': ('source', 'target'),
            'description': 'Campi per pgRouting (compilati automaticamente)'
        }),
        ('Costi Precalcolati', {
            'fields': ('cost_length', 'cost_time', 'cost_scenic'),
            'classes': ('collapse',),
            'description': 'Costi per diversi algoritmi di routing'
        }),
    )

admin.site.register(PointOfInterest, PointOfInterestAdmin)
admin.site.register(ScenicArea, ScenicAreaAdmin)
admin.site.register(RoadSegment, RoadSegmentAdmin)