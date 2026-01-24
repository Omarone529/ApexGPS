import logging
import time

from django.contrib import admin, messages
from django.contrib.gis.admin import GISModelAdmin
from django.http import HttpResponseRedirect
from django.urls import path, reverse

from .models import Route, Stop

logger = logging.getLogger(__name__)

try:
    from routes.services.routing.fast_routing import FastRoutingService
    from routes.services.routing.route_validator import RouteValidator

    logger.info("Successfully imported routing services from routes.services.routing")
except ImportError as e:
    logger.error(f"Failed to import routing services: {e}")
    FastRoutingService = None
    RouteValidator = None


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
        "coordinates_status",
    )
    list_filter = ("visibility", "preference", "created_at", "owner")
    search_fields = ("name", "owner__username", "owner__email")
    readonly_fields = (
        "created_at",
        "updated_at",
        "route_info_summary",
        "admin_calculate_fastest_route",
        "routing_services_status",
    )
    ordering = ("-created_at",)

    # Fields to show in the change form
    fieldsets = (
        (
            "Basic Information",
            {
                "fields": ("name", "owner", "visibility", "preference"),
                "classes": ("wide",),
            },
        ),
        (
            "Route Coordinates",
            {
                "fields": ("start_location", "end_location"),
                "classes": ("wide",),
            },
        ),
        (
            "Calculated Metrics",
            {
                "fields": ("distance_km", "estimated_time_min", "polyline"),
                "classes": ("wide",),
            },
        ),
        (
            "System Status",
            {
                "fields": ("routing_services_status",),
                "classes": ("wide",),
            },
        ),
        (
            "Admin Actions",
            {
                "fields": ("admin_calculate_fastest_route",),
                "classes": ("wide",),
            },
        ),
        (
            "Route Details",
            {
                "fields": ("route_info_summary",),
                "classes": ("collapse",),
            },
        ),
        (
            "Timestamps",
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    gis_widget_kwargs = {
        "attrs": {
            "default_lon": 12.4964,
            "default_lat": 41.9028,
            "default_zoom": 6,
        }
    }

    actions = ["admin_action_calculate_fastest_route"]

    def coordinates_status(self, obj):
        """Display coordinate status in list view."""
        has_start = hasattr(obj, "start_location") and obj.start_location
        has_end = hasattr(obj, "end_location") and obj.end_location

        if has_start and has_end:
            return "Complete"
        elif has_start or has_end:
            return "Partial"
        return "Missing"

    coordinates_status.short_description = "Coordinates"
    coordinates_status.admin_order_field = "start_location"

    def routing_services_status(self, obj):
        """Display routing services status."""
        if FastRoutingService and RouteValidator:
            return "Routing services: AVAILABLE"
        else:
            return "Routing services: NOT AVAILABLE"

    routing_services_status.short_description = "System Status"

    def admin_calculate_fastest_route(self, obj):
        """Display admin action for calculating fastest route."""
        if not obj.id:
            return "Save route first"

        if not FastRoutingService or not RouteValidator:
            return "Routing services not available"

        if not hasattr(obj, "start_location") or not obj.start_location:
            return "Start location required"

        if not hasattr(obj, "end_location") or not obj.end_location:
            return "End location required"

        return "Route can be calculated"

    admin_calculate_fastest_route.short_description = "Calculation Status"

    def route_info_summary(self, obj):
        """Display route information summary."""
        info_parts = []

        if obj.distance_km:
            info_parts.append(f"Distance: {obj.distance_km:.2f} km")

        if obj.estimated_time_min:
            hours = int(obj.estimated_time_min // 60)
            minutes = int(obj.estimated_time_min % 60)
            if hours > 0:
                info_parts.append(f"Estimated time: {hours}h {minutes}min")
            else:
                info_parts.append(f"Estimated time: {minutes} min")

        if hasattr(obj, "start_location") and obj.start_location:
            lon, lat = obj.start_location.coords
            info_parts.append(f"Start: {lat:.6f}, {lon:.6f}")

        if hasattr(obj, "end_location") and obj.end_location:
            lon, lat = obj.end_location.coords
            info_parts.append(f"End: {lat:.6f}, {lon:.6f}")

        if not info_parts:
            return "No route information available"

        return "\n".join(info_parts)

    route_info_summary.short_description = "Route Summary"

    def save_model(self, request, obj, form, change):
        """Custom save method to handle initial route creation."""
        # If this is a new route (not change), set the owner
        if not change and not obj.owner_id:
            obj.owner = request.user

        super().save_model(request, obj, form, change)

    def get_urls(self):
        """Add custom URLs for admin actions."""
        from django.urls import path

        urls = super().get_urls()

        custom_urls = [
            path(
                "<path:object_id>/calculate-fastest/",
                self.admin_site.admin_view(self._admin_calculate_fastest_view),
                name="routes_route_calculate_fastest",
            ),
        ]
        return custom_urls + urls

    def _admin_calculate_fastest_view(self, request, object_id):
        """Admin view to calculate fastest route for a specific route."""
        if not FastRoutingService or not RouteValidator:
            messages.error(
                request,
                "Routing services are not available. Check server logs for details.",
            )
            return HttpResponseRedirect(
                reverse("admin:routes_route_change", args=[object_id])
            )

        try:
            route = Route.objects.get(id=object_id)

            if not (
                hasattr(route, "start_location")
                and route.start_location
                and hasattr(route, "end_location")
                and route.end_location
            ):
                messages.error(
                    request, "Route is missing start and/or end coordinates."
                )
                return HttpResponseRedirect(
                    reverse("admin:routes_route_change", args=[object_id])
                )

            start_lon, start_lat = route.start_location.coords
            end_lon, end_lat = route.end_location.coords

            fast_service = FastRoutingService()
            validator = RouteValidator()

            validation_result = validator.full_route_validation(
                start_lat=start_lat,
                start_lon=start_lon,
                end_lat=end_lat,
                end_lon=end_lon,
                max_distance_km=1000.0,
            )

            if not validation_result["is_valid"]:
                error_msg = "Route validation failed."
                if validation_result.get("errors"):
                    error_msg += " Errors: " + ", ".join(validation_result["errors"])
                messages.error(request, error_msg)
                return HttpResponseRedirect(
                    reverse("admin:routes_route_change", args=[object_id])
                )

            calculation_start = time.time()

            try:
                fastest_route = fast_service.calculate_fastest_route(
                    start_lat=start_lat,
                    start_lon=start_lon,
                    end_lat=end_lat,
                    end_lon=end_lon,
                    vertex_threshold=0.01,
                )

                if not fastest_route:
                    messages.error(
                        request, "No route found between the specified points."
                    )
                    return HttpResponseRedirect(
                        reverse("admin:routes_route_change", args=[object_id])
                    )

                route.distance_km = fastest_route["total_distance_km"]
                route.estimated_time_min = fastest_route["total_time_minutes"]
                route.polyline = fastest_route.get("polyline", "")
                route.save()

                processing_time = round((time.time() - calculation_start) * 1000, 2)

                messages.success(
                    request,
                    f"Fastest route calculated successfully. "
                    f"Distance: {fastest_route['total_distance_km']:.2f} km, "
                    f"Time: {fastest_route['total_time_minutes']:.1f} minutes, "
                    f"Processing: {processing_time} ms",
                )

            except Exception as e:
                messages.error(request, f"Error calculating route: {str(e)}")

        except Route.DoesNotExist:
            messages.error(request, "Route not found.")
        except Exception as e:
            messages.error(request, f"Unexpected error: {str(e)}")

        return HttpResponseRedirect(
            reverse("admin:routes_route_change", args=[object_id])
        )

    def admin_action_calculate_fastest_route(self, request, queryset):
        """Admin action to calculate fastest route for selected routes."""
        if not FastRoutingService or not RouteValidator:
            messages.error(request, "Routing services are not available")
            return

        fast_service = FastRoutingService()
        validator = RouteValidator()

        success_count = 0
        error_count = 0

        for route in queryset:
            try:
                if not (
                    hasattr(route, "start_location")
                    and route.start_location
                    and hasattr(route, "end_location")
                    and route.end_location
                ):
                    error_count += 1
                    continue

                start_lon, start_lat = route.start_location.coords
                end_lon, end_lat = route.end_location.coords

                validation = validator.full_route_validation(
                    start_lat, start_lon, end_lat, end_lon
                )

                if not validation["is_valid"]:
                    error_count += 1
                    continue

                route_result = fast_service.calculate_fastest_route(
                    start_lat, start_lon, end_lat, end_lon
                )

                if route_result:
                    route.distance_km = route_result["total_distance_km"]
                    route.estimated_time_min = route_result["total_time_minutes"]
                    route.polyline = route_result.get("polyline", "")
                    route.save()
                    success_count += 1
                else:
                    error_count += 1

            except Exception:
                error_count += 1

        if success_count > 0:
            self.message_user(
                request,
                f"Calculated fastest routes for {success_count} routes.",
                messages.SUCCESS,
            )

        if error_count > 0:
            self.message_user(
                request,
                f"Failed to calculate routes for {error_count} routes. "
                f"Check individual routes for details.",
                messages.WARNING,
            )

    admin_action_calculate_fastest_route.short_description = "Calculate fastest route"


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

    def get_urls(self):
        """Add custom URLs for admin actions."""
        urls = super().get_urls()

        custom_urls = [
            path(
                "<path:object_id>/save-calculated/",
                self.admin_site.admin_view(self._admin_save_calculated_view),
                name="routes_route_save_calculated",
            ),
        ]
        return custom_urls + urls

    def _admin_save_calculated_view(self, request, object_id):
        """Admin view to save a calculated route."""
        try:
            route = Route.objects.get(id=object_id)

            # Crea una copia del percorso calcolato
            if route.start_location and route.end_location:
                new_route = Route.objects.create(
                    name=f"Copia di {route.name}",
                    owner=request.user,
                    visibility="private",
                    preference=route.preference,
                    start_location=route.start_location,
                    end_location=route.end_location,
                    polyline=route.polyline,
                    distance_km=route.distance_km,
                    estimated_time_min=route.estimated_time_min,
                    total_scenic_score=route.total_scenic_score,
                )

                messages.success(
                    request,
                    f"Percorso salvato come '{new_route.name}' (ID: {new_route.id})",
                )
            else:
                messages.error(
                    request, "Percorso senza coordinate, non può essere salvato"
                )

        except Route.DoesNotExist:
            messages.error(request, "Percorso non trovato")

        return HttpResponseRedirect(reverse("admin:routes_route_changelist"))
