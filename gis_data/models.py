from django.contrib.gis.db import models

class PointOfInterest(models.Model):
    """
    A geographic point representing a location of interest for scenic routing.
    Points of Interest (POIs) enhance route quality calculations by providing
    scenic value to nearby road segments. The system considers POI density
    when calculating scenic scores for road segments.
    """

    name = models.CharField(
        max_length=255,
        verbose_name="Nome del Punto",
        help_text="Nome descrittivo del punto di interesse"
    )

    category = models.CharField(
        max_length=100,
        verbose_name="Categoria",
        help_text="Classificazione del punto (es. monumento, lago, passo)"
    )

    location = models.PointField(
        srid=4326,
        verbose_name="Posizione GPS",
        help_text="Coordinate geografiche in formato WGS84 (latitudine, longitudine)"
    )

    description = models.TextField(
        blank=True,
        null=True,
        verbose_name="Descrizione",
        help_text="Informazioni dettagliate sul punto di interesse"
    )

    class Meta:
        verbose_name = "Punto di Interesse"
        verbose_name_plural = "Punti di Interesse"
        indexes = [
            models.Index(fields=['category']),
            models.Index(fields=['name']),
        ]

    def __str__(self):
        return self.name


class ScenicArea(models.Model):
    """
    A polygonal area with enhanced scenic value for routing calculations.

    Scenic areas receive bonus scoring in the routing algorithm,
    encouraging routes that pass through or near visually appealing
    geographic regions like national parks, lake districts, or mountain ranges.
    """

    name = models.CharField(
        max_length=255,
        verbose_name="Nome dell'Area",
        help_text="Nome descrittivo dell'area scenica"
    )

    area_type = models.CharField(
        max_length=100,
        verbose_name="Tipo di Area",
        help_text="Classificazione dell'area scenica"
    )

    bonus_value = models.FloatField(
        default=1.0,
        verbose_name="Valore Bonus",
        help_text="Moltiplicatore per il punteggio panoramico (es. 1.5 per +50%)"
    )

    area = models.MultiPolygonField(
        srid=4326,
        verbose_name="Area Geografica",
        help_text="Confini dell'area in formato poligonale"
    )

    class Meta:
        verbose_name = "Area Scenica"
        verbose_name_plural = "Aree Sceniche"

    def __str__(self):
        return self.name


class RoadSegment(models.Model):
    """
    A directed edge in the road network graph for pgRouting calculations.

    Represents a segment of road with geometric, functional, and scenic
    properties. This model stores pre-calculated costs for different
    routing strategies (fastest, most scenic, balanced).

    The routing cost formula for this project is implemented as:
    C_segment = (α × Distance) - (β × Scenic_Score)
    where α and β are user preference coefficients.
    """

    # Identification
    osm_id = models.BigIntegerField(
        blank=True,
        null=True,
        verbose_name="ID OpenStreetMap",
        help_text="Identificativo originale da OpenStreetMap"
    )

    name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name="Nome della Strada",
        help_text="Nome ufficiale o comune della strada"
    )

    # Geometry
    geometry = models.LineStringField(
        srid=4326,
        verbose_name="Tracciato",
        help_text="Percorso della strada in formato LineString"
    )

    length_m = models.FloatField(
        default=0.0,
        verbose_name="Lunghezza (metri)",
        help_text="Lunghezza effettiva del segmento in metri"
    )

    # Road classification
    highway = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name="Tipo di Strada",
        help_text="Classificazione secondo lo standard OpenStreetMap"
    )

    maxspeed = models.IntegerField(
        blank=True,
        null=True,
        verbose_name="Velocità Massima (km/h)",
        help_text="Limite di velocità legale"
    )

    oneway = models.BooleanField(
        default=False,
        verbose_name="Senso Unico",
        help_text="Indica se la strada è a senso unico"
    )

    # Physical properties
    surface = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name="Superficie",
        help_text="Tipo di superficie stradale"
    )

    lanes = models.IntegerField(
        blank=True,
        null=True,
        verbose_name="Numero di Corsie",
        help_text="Numero totale di corsie (entrambi i sensi)"
    )

    # Scenic metrics (calculated during data preparation)
    curvature = models.FloatField(
        default=1.0,
        verbose_name="Sinuosità",
        help_text="Rapporto tra lunghezza effettiva e distanza in linea d'aria (≥1.0)"
    )

    elevation_gain = models.FloatField(
        default=0.0,
        verbose_name="Dislivello Positivo (metri)",
        help_text="Guadagno totale di elevazione lungo il segmento"
    )

    scenic_rating = models.FloatField(
        default=5.0,
        verbose_name="Valutazione Panoramica",
        help_text="Punteggio di qualità panoramica da 0.0 (brutto) a 10.0 (eccezionale)"
    )

    poi_density = models.FloatField(
        default=0.0,
        verbose_name="Densità di Punti di Interesse",
        help_text="Numero di punti di interesse per chilometro"
    )

    # pgRouting graph structure
    source = models.IntegerField(
        blank=True,
        null=True,
        db_index=True,
        verbose_name="Nodo di Partenza",
        help_text="ID del nodo di partenza nel grafo di routing"
    )

    target = models.IntegerField(
        blank=True,
        null=True,
        db_index=True,
        verbose_name="Nodo di Arrivo",
        help_text="ID del nodo di arrivo nel grafo di routing"
    )

    # Pre-calculated routing costs
    cost_length = models.FloatField(
        default=0.0,
        verbose_name="Costo per Lunghezza",
        help_text="Costo basato sulla sola distanza (α=1, β=0)"
    )

    cost_time = models.FloatField(
        default=0.0,
        verbose_name="Costo per Tempo",
        help_text="Costo basato sul tempo di percorrenza stimato"
    )

    cost_scenic = models.FloatField(
        default=0.0,
        verbose_name="Costo Panoramico",
        help_text="Costo per l'ottimizzazione panoramica"
    )

    class Meta:
        verbose_name = "Segmento Stradale"
        verbose_name_plural = "Segmenti Stradali"
        indexes = [
            models.Index(fields=['highway']),
            models.Index(fields=['source', 'target']),
            models.Index(fields=['scenic_rating']),
        ]

    def __str__(self):
        if self.name:
            return f"{self.name} ({self.length_m:.0f}m)"
        elif self.osm_id:
            return f"Strada OSM:{self.osm_id} ({self.length_m:.0f}m)"
        return f"Segmento {self.pk} ({self.length_m:.0f}m)"

    def save(self, *args, **kwargs):
        """
        Calculate approximate length when saving.

        Note: Accurate geographic length calculation is performed
        in the management command using PostGIS ST_Length function.
        This provides an approximate value for display purposes.
        """
        if self.geometry and self.geometry.length > 0:
            # Approximate conversion: 1 degree ≈ 111.32 km at equator
            self.length_m = self.geometry.length * 111319.9
        super().save(*args, **kwargs)

    def calculate_scenic_cost(self, alpha=1.0, beta=0.5):
        """
        Calculate scenic routing cost using project formula.
        Formula: C = α × length - β × scenic_score
        """
        scenic_score = self.scenic_rating * 100
        return (alpha * self.length_m) - (beta * scenic_score)