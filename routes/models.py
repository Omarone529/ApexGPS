from django.contrib.auth import get_user_model
from django.contrib.gis.db import models as gis_models
from django.db import models
from django.utils import timezone

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

    start_location = gis_models.PointField(
        verbose_name="Punto di partenza",
        null=True,
        blank=True,
        help_text="Punto di partenza del percorso",
    )
    end_location = gis_models.PointField(
        verbose_name="Punto di arrivo",
        null=True,
        blank=True,
        help_text="Punto di arrivo del percorso",
    )
    preference = models.CharField(
        max_length=20,
        choices=PREFERENCE_CHOICES,
        default="balanced",
        verbose_name="Preferenza",
    )

    # auto-calculated
    polyline = models.TextField(
        verbose_name="Polilinea codificata",
        null=True,
        blank=True,
        help_text="Rappresentazione codificata del percorso per le mappe",
    )
    distance_km = models.FloatField(
        verbose_name="Distanza (km)",
        null=True,
        blank=True,
        default=0.0,
        help_text="Distanza totale del percorso in chilometri",
    )
    estimated_time_min = models.IntegerField(
        verbose_name="Tempo stimato (minuti)",
        null=True,
        blank=True,
        default=0,
        help_text="Tempo di percorrenza stimato in minuti",
    )
    total_scenic_score = models.FloatField(
        null=True,
        blank=True,
        default=0.0,
        verbose_name="Punteggio panoramico totale",
        help_text="Punteggio panoramico totale del percorso",
    )

    # fields to detect duplicate routes
    fingerprint = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        verbose_name="Impronta univoca",
        help_text="Hash dei dati del percorso per rilevare duplicati",
    )
    #screenshot of the route
    screenshot = models.ImageField(
        upload_to='route_screenshots/',
        null=True,
        blank=True,
        verbose_name="Screenshot del percorso",
        help_text="Immagine statica della mappa con il percorso tracciato"
    )

    hiddenUntil  = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name="hiddenUntil ",
        help_text="Data e ora per privatizzazione tour",
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
        # Enforce uniqueness per owner + fingerprint
        constraints = [
            models.UniqueConstraint(
                fields=['owner', 'fingerprint'],
                name='unique_route_per_owner_fingerprint'
            )
        ]

    def __str__(self):
        """Human-readable string representation of the route."""
        return f"{self.name} - {self.owner.username}"

    def is_public(self):
        """Check if the route is public."""
        return self.visibility == "public"

    def can_view(self, user):
        """Determine if a user can view this route."""
        if self.owner.hiddenUntil and self.owner.hiddenUntil > timezone.now():
            if user.is_authenticated and (user == self.owner or user.is_staff):
                return True
            return False

    def get_stops_count(self):
        """Get the number of stops in this route (excluding start/end)."""
        return self.stops.count()

    def get_all_points_in_order(self):
        """
        Get all points in the route in correct order:
        [start, stop1, stop2, ..., stopN, end].
        """
        points = []
        if self.start_location:
            points.append(self.start_location)

        # Get all stops in order
        stops = self.stops.order_by("order")
        for stop in stops:
            points.append(stop.location)

        if self.end_location:
            points.append(self.end_location)

        return points

    def is_ready_for_calculation(self):
        """Check if route has enough data for route calculation."""
        return bool(self.start_location and self.end_location)


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

        if self.owner.hiddenUntil and self.owner.hiddenUntil > timezone.now():
            self.visibility = 'private'
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
