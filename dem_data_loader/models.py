from django.contrib.gis.db import models
from django.db import connection


class DEMTile(models.Model):
    """Represents a single DEM raster tile loaded into PostGIS."""

    rid = models.AutoField(primary_key=True)
    rast = models.RasterField()
    filename = models.CharField(max_length=255, blank=True, null=True)

    class Meta:
        """Meta options for DEM tile."""

        verbose_name = "Modello elevazione terreno"
        verbose_name_plural = "Modello elevazione terreni"

    def __str__(self):
        """String representation of DEM tile."""
        return f"Tile {self.rid}"


class ElevationQuery(models.Model):
    """Stores elevation queries with results."""

    name = models.CharField(max_length=100)
    latitude = models.FloatField()
    longitude = models.FloatField()
    elevation = models.FloatField(blank=True, null=True)
    queried_at = models.DateTimeField(auto_now_add=True)
    success = models.BooleanField(default=False)

    class Meta:
        """Meta options for Elevation Query."""

        verbose_name = "Elevation Query"
        verbose_name_plural = "Elevation Queries"
        ordering = ["-queried_at"]

    def __str__(self):
        """String representation of Elevation Query."""
        return f"{self.name}: {self.elevation or 'N/A'}m"

    def save(self, *args, **kwargs):
        """Save Elevation Query."""
        if (
            self.latitude is not None
            and self.longitude is not None
            and not self.elevation
        ):
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT ST_Value(rast, 1, ST_SetSRID(ST_Point(%s, %s), 4326))
                        FROM dem_data_loader_demtile  # Changed from 'dem'
                        WHERE ST_Intersects(rast, ST_SetSRID(ST_Point(%s, %s), 4326))
                        LIMIT 1;
                    """,
                        [self.longitude, self.latitude, self.longitude, self.latitude],
                    )
                    result = cursor.fetchone()
                    if result and result[0] is not None:
                        self.elevation = float(result[0])
                        self.success = True
            except Exception:
                self.success = False
        super().save(*args, **kwargs)
