from django.contrib.gis import admin
from .models import PointOfInterest, ScenicArea

class PointOfInterestAdmin(admin.GISModelAdmin):
    list_display = ('name', 'category', 'location')
    # Default coordinates to center the map in Italy
    default_lon = 1200000
    default_lat = 5000000
    default_zoom = 5

class ScenicAreaAdmin(admin.GISModelAdmin):
    list_display = ('name', 'area_type', 'bonus_value')
    default_lon = 1200000
    default_lat = 5000000
    default_zoom = 5

admin.site.register(PointOfInterest, PointOfInterestAdmin)
admin.site.register(ScenicArea, ScenicAreaAdmin)