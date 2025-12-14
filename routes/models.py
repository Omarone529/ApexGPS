from django.contrib.auth import get_user_model
from django.contrib.gis.db import models as gis_models
from django.db import models

User = get_user_model()


class Route(models.Model):
    """
    Model representing a tourist or scenic itinerary/route.
    This class defines a route calculated by the user, which can be saved,
    shared, and displayed. Each route is associated with an owner and has
    different visibility levels to manage user access.
    """

    VISIBILITY_CHOICES = [
        ("private", "Privato"),
        ("public", "Pubblico"),
        ("link", "Condiviso con link"),
    ]

    PREFERENCE_CHOICES = [
        ("fast", "Veloce"),
        ("balanced", "Equilibrata"),
        ("most_winding", "Sinuosa Massima"),
    ]

    # Route metadata
    name = models.CharField(max_length=255, verbose_name="Nome percorso")
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="routes",
        verbose_name="Proprietario",
    )
    visibility = models.CharField(
        max_length=20,
        choices=VISIBILITY_CHOICES,
        default="private",
        verbose_name="Visibilità",
    )

    # Geographic data
    start_location = gis_models.PointField(verbose_name="Punto di partenza")
    end_location = gis_models.PointField(verbose_name="Punto di arrivo")
    preference = models.CharField(
        max_length=20, choices=PREFERENCE_CHOICES, verbose_name="Preferenza"
    )

    # Calculation results
    polyline = models.TextField(verbose_name="Polilinea codificata")
    distance_km = models.FloatField(verbose_name="Distanza (km)")
    estimated_time_min = models.IntegerField(verbose_name="Tempo stimato (minuti)")
    total_scenic_score = models.FloatField(
        null=True, blank=True, verbose_name="Punteggio panoramico totale"
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Data creazione")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Data aggiornamento")

    class Meta:
        """Metadata configuration for the Route model."""

        verbose_name = "Percorso"
        verbose_name_plural = "Percorsi"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner", "visibility"]),
            models.Index(fields=["visibility"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        """Human-readable string representation of the route."""
        return f"{self.name} - {self.owner.username}"

    def is_public(self):
        """Check if the route is public."""
        return self.visibility == "public"

    def can_view(self, user):
        """Determine if a user can view this route."""
        if not user.is_authenticated:
            return self.visibility == "public"

        if user == self.owner or user.is_staff:
            return True

        return self.visibility in ["public", "link"]

    def get_stops_count(self):
        """Get the number of stops in this route (excluding start/end)."""
        return self.stops.count()

    def get_all_points_in_order(self):
        """
        Get all points in the route in correct order:
        [start, stop1, stop2, ..., stopN, end].
        """
        points = [self.start_location]

        # Get all stops in order
        stops = self.stops.order_by("order")
        for stop in stops:
            points.append(stop.location)

        points.append(self.end_location)
        return points


class Stop(models.Model):
    """
    Model representing a user-added stop along a scenic route.
    Stops are intermediate points that the route must pass through.
    The route is recalculated to be scenic between each consecutive point.
    """

    route = models.ForeignKey(
        Route, on_delete=models.CASCADE, related_name="stops", verbose_name="Percorso"
    )
    order = models.PositiveIntegerField(
        verbose_name="Ordine",
        help_text="Posizione nella sequenza (1 = prima tappa dopo la partenza)",
    )
    location = gis_models.PointField(verbose_name="Posizione")
    name = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Nome tappa (opzionale)",
        help_text="Nome descrittivo della tappa",
    )

    # Timestamp
    added_at = models.DateTimeField(auto_now_add=True, verbose_name="Data aggiunta")

    class Meta:
        """Metadata configuration for the Stop model."""

        verbose_name = "Tappa"
        verbose_name_plural = "Tappe"
        ordering = ["route", "order"]
        unique_together = ["route", "order"]
        indexes = [
            models.Index(fields=["route", "order"]),
        ]

    def __str__(self):
        """Human-readable string representation of the stop."""
        if self.name:
            return f"Tappa {self.order}: {self.name}"
        return f"Tappa {self.order}"

    def save(self, *args, **kwargs):
        """Ensure order is unique and sequential within the route."""
        # If this is a new stop and no order specified, put it at the end
        if not self.pk and not self.order:
            last_stop = Stop.objects.filter(route=self.route).order_by("-order").first()
            self.order = (last_stop.order + 1) if last_stop else 1

        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """When deleting a stop, reorder remaining stops."""
        route_id = self.route_id
        order_to_delete = self.order

        super().delete(*args, **kwargs)

        # Reorder remaining stops in the same route
        stops_to_reorder = Stop.objects.filter(
            route_id=route_id, order__gt=order_to_delete
        )
        for stop in stops_to_reorder:
            stop.order -= 1
            stop.save()
