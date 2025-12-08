# routes/models.py
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
