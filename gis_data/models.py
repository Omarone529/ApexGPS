from django.contrib.gis.db import models
from django.db import connection


class PointOfInterest(models.Model):
    """
    A geographic point representing a location of interest for scenic routing.
    Points of Interest (POIs) enhance route quality calculations by providing
    scenic value to nearby road segments. The system considers POI density
    when calculating scenic scores for road segments.
    """

    CATEGORY_CHOICES = [
        ("panoramic", "Panoramico"),
        ("mountain_pass", "Passo di Montagna"),
        ("twisty_road", "Strada con Curve"),
        ("biker_meeting", "Ritrovo Motociclisti"),
        ("viewpoint", "Punto di Osservazione"),
        ("lake", "Lago"),
        ("sea", "Mare"),
        ("monument", "Monumento"),
        ("historic", "Sito Storico"),
        ("nature", "Area Naturale"),
        ("food", "Ristorante Tipico"),
        ("accommodation", "Alloggio Moto-friendly"),
        ("gas_station", "Stazione di Servizio"),
        ("technical_point", "Punto Tecnico (ponte/tunnel)"),
        ("church", "Chiesa/Santuario"),
        ("castle", "Castello/Fortezza"),
        ("vineyard", "Cantina/Vigneto"),
        ("waterfall", "Cascata"),
        ("thermal", "Terme/Sorgente"),
        ("museum", "Museo"),
    ]

    name = models.CharField(
        max_length=255,
        verbose_name="Nome del Punto",
        help_text="Nome descrittivo del punto di interesse",
    )

    category = models.CharField(
        max_length=50,
        choices=CATEGORY_CHOICES,
        verbose_name="Categoria",
        help_text="Classificazione del punto di interesse",
    )

    location = models.PointField(
        srid=4326,
        verbose_name="Posizione GPS",
        help_text="Coordinate geografiche in formato WGS84 (longitudine, latitudine)",
    )

    description = models.TextField(
        blank=True,
        null=True,
        verbose_name="Descrizione",
        help_text="Informazioni dettagliate sul punto di interesse",
    )

    osm_id = models.BigIntegerField(
        blank=True,
        null=True,
        verbose_name="ID OpenStreetMap",
        unique=True,
        db_index=True,
        help_text="Identificativo originale da OpenStreetMap",
    )

    region = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name="Regione",
        help_text="Regione italiana di appartenenza",
        db_index=True,
    )

    elevation = models.FloatField(
        blank=True,
        null=True,
        verbose_name="Altitudine (m)",
        help_text="Altitudine sul livello del mare in metri",
    )

    importance_score = models.FloatField(
        default=1.0,
        verbose_name="Punteggio Importanza",
        help_text="Peso del POI nel calcolo panoramico (1.0 = normale, 2.0 = doppio)",
    )

    tags = models.JSONField(
        blank=True,
        null=True,
        verbose_name="Tag OSM",
        help_text="Tag originali da OpenStreetMap in formato JSON",
    )

    # Timestamps
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Data creazione",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Data aggiornamento",
    )

    # Verification flags
    is_verified = models.BooleanField(
        default=False,
        verbose_name="Verificato",
        help_text="Indica se il POI è stato verificato manualmente",
    )

    is_active = models.BooleanField(
        default=True,
        verbose_name="Attivo",
        help_text="Indica se il POI è attivo e utilizzabile nei calcoli",
    )

    class Meta:
        """Meta class for POI."""

        verbose_name = "Punto di Interesse"
        verbose_name_plural = "Punti di Interesse"
        indexes = [
            models.Index(fields=["category"]),
            models.Index(fields=["name"]),
            models.Index(fields=["osm_id"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["location"]),
            models.Index(fields=["region"]),
        ]
        ordering = ["name"]

    def __str__(self):
        """This function returns the name of the POI."""
        return f"{self.name} ({self.get_category_display()})"

    def get_scenic_value(self):
        """
        Return the scenic value weight for this POI.
        Some categories are more scenic than others for motorcycle trips.
        """
        scenic_weights = {
            "panoramic": 2.0,
            "mountain_pass": 2.5,
            "twisty_road": 3.0,
            "viewpoint": 2.0,
            "lake": 1.8,
            "sea": 1.5,
            "waterfall": 2.0,
            "castle": 1.5,
            "vineyard": 1.3,
            "default": 1.0,
        }
        return (
                scenic_weights.get(self.category, scenic_weights["default"])
                * self.importance_score
        )


class ScenicArea(models.Model):
    """
    A polygonal area with enhanced scenic value for routing calculations.

    Scenic areas receive bonus scoring in the routing algorithm,
    encouraging routes that pass through or near visually appealing
    geographic regions like national parks, lake districts, or mountain ranges.
    """

    AREA_TYPE_CHOICES = [
        ("national_park", "Parco Nazionale"),
        ("regional_park", "Parco Regionale"),
        ("lake_district", "Distretto Lacustre"),
        ("mountain_range", "Catena Montuosa"),
        ("coastal_area", "Area Costiera"),
        ("vineyard_area", "Zona Vinicola"),
        ("historic_center", "Centro Storico"),
        ("natural_reserve", "Riserva Naturale"),
    ]

    name = models.CharField(
        max_length=255,
        verbose_name="Nome dell'Area",
        help_text="Nome descrittivo dell'area scenica",
    )

    area_type = models.CharField(
        max_length=50,
        choices=AREA_TYPE_CHOICES,
        verbose_name="Tipo di Area",
        help_text="Classificazione dell'area scenica",
    )

    bonus_value = models.FloatField(
        default=1.0,
        verbose_name="Valore Bonus",
        help_text="Moltiplicatore per il punteggio panoramico (es. 1.5 per +50%)",
    )

    area = models.MultiPolygonField(
        srid=4326,
        verbose_name="Area Geografica",
        help_text="Confini dell'area in formato poligonale",
    )

    description = models.TextField(
        blank=True,
        null=True,
        verbose_name="Descrizione",
        help_text="Informazioni dettagliate sull'area scenica",
    )

    # Additional metadata
    osm_id = models.BigIntegerField(
        blank=True,
        null=True,
        verbose_name="ID OpenStreetMap",
        unique=True,
        db_index=True,
        help_text="Identificativo originale da OpenStreetMap",
    )

    # Timestamps
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Data creazione",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Data aggiornamento",
    )

    class Meta:
        """Meta class for ScenicArea."""

        verbose_name = "Area Scenica"
        verbose_name_plural = "Aree Sceniche"
        indexes = [
            models.Index(fields=["area_type"]),
            models.Index(fields=["bonus_value"]),
        ]
        ordering = ["name"]

    def __str__(self):
        """Returns the name of the scenic area."""
        return f"{self.name} ({self.get_area_type_display()})"

    def calculate_bonus_for_segment(self, segment_length, intersection_length):
        """Calculate scenic bonus for a road segment that passes through this area."""
        if intersection_length <= 0:
            return 1.0

        # Calculate coverage ratio
        coverage_ratio = intersection_length / segment_length

        # Apply bonus based on coverage
        # Full bonus if segment is completely inside, proportional otherwise
        bonus = 1.0 + ((self.bonus_value - 1.0) * coverage_ratio)

        return bonus


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
        db_index=True,
        help_text="Identificativo originale da OpenStreetMap",
    )

    region = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name="Regione",
        help_text="Regione italiana di appartenenza del segmento",
        db_index=True,
    )

    name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name="Nome della Strada",
        help_text="Nome ufficiale o comune della strada",
    )

    # Geometry
    geometry = models.LineStringField(
        srid=4326,
        verbose_name="Tracciato",
        help_text="Percorso della strada in formato LineString",
    )

    length_m = models.FloatField(
        default=0.0,
        verbose_name="Lunghezza (metri)",
        help_text="Lunghezza effettiva del segmento in metri",
    )

    # Road classification
    highway = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name="Tipo di Strada",
        help_text="Classificazione secondo lo standard OpenStreetMap",
    )

    maxspeed = models.IntegerField(
        blank=True,
        null=True,
        verbose_name="Velocità Massima (km/h)",
        help_text="Limite di velocità legale",
    )

    oneway = models.BooleanField(
        default=False,
        verbose_name="Senso Unico",
        help_text="Indica se la strada è a senso unico",
    )

    # Physical properties
    surface = models.CharField(
        max_length=50,
        blank=True,
        null=True,
        verbose_name="Superficie",
        help_text="Tipo di superficie stradale",
    )

    lanes = models.IntegerField(
        blank=True,
        null=True,
        verbose_name="Numero di Corsie",
        help_text="Numero totale di corsie (entrambi i sensi)",
    )

    # Elevation data
    elevation_gain = models.FloatField(
        default=0.0,
        verbose_name="Dislivello Positivo (metri)",
        help_text="Guadagno totale di elevazione lungo il segmento",
    )

    elevation_loss = models.FloatField(
        default=0.0,
        verbose_name="Dislivello Negativo (metri)",
        help_text="Perdita totale di elevazione lungo il segmento",
    )

    avg_elevation = models.FloatField(
        blank=True,
        null=True,
        verbose_name="Altitudine Media (m)",
        help_text="Altitudine media sul livello del mare",
    )

    # Scenic metrics (calculated during data preparation)
    curvature = models.FloatField(
        default=1.0,
        verbose_name="Sinuosità",
        help_text="Rapporto tra lunghezza effettiva e distanza in linea d'aria (≥1.0)",
    )

    hairpin_count = models.IntegerField(
        default=0,
        verbose_name="Numero di Tornanti",
        help_text="Numero di curve a gomito strette (≤30m raggio)",
    )

    scenic_rating = models.FloatField(
        default=5.0,
        verbose_name="Valutazione Panoramica",
        help_text="Punteggio di qualità panoramica da 0.0 a 10.0",
    )

    poi_density = models.FloatField(
        default=0.0,
        verbose_name="Densità di Punti di Interesse",
        help_text="Numero di punti di interesse per chilometro",
    )

    # Weighted POI density considering POI importance
    weighted_poi_density = models.FloatField(
        default=0.0,
        verbose_name="Densità POI Ponderata",
        help_text="Densità di POI ponderata per importanza",
    )

    # pgRouting graph structure
    source = models.BigIntegerField(
        blank=True,
        null=True,
        db_index=True,
        verbose_name="Nodo di Partenza",
        help_text="ID del nodo di partenza nel grafo di routing",
    )

    target = models.BigIntegerField(
        blank=True,
        null=True,
        db_index=True,
        verbose_name="Nodo di Arrivo",
        help_text="ID del nodo di arrivo nel grafo di routing",
    )

    x1 = models.FloatField(
        blank=True,
        null=True,
        verbose_name="X Coordinate Start",
        help_text="Start X coordinate for A* algorithm optimization",
    )

    y1 = models.FloatField(
        blank=True,
        null=True,
        verbose_name="Y Coordinate Start",
        help_text="Start Y coordinate for A* algorithm optimization",
    )

    x2 = models.FloatField(
        blank=True,
        null=True,
        verbose_name="X Coordinate End",
        help_text="End X coordinate for A* algorithm optimization",
    )

    y2 = models.FloatField(
        blank=True,
        null=True,
        verbose_name="Y Coordinate End",
        help_text="End Y coordinate for A* algorithm optimization",
    )

    # ADD NEW FIELD for noded network
    old_id = models.BigIntegerField(
        blank=True,
        null=True,
        verbose_name="Original Segment ID",
        help_text="Link to original road segment before noded network creation",
    )

    # Pre-calculated routing costs
    cost_length = models.FloatField(
        default=0.0,
        verbose_name="Costo per Lunghezza",
        help_text="Costo basato sulla sola distanza (α=1, β=0)",
    )

    cost_time = models.FloatField(
        default=0.0,
        verbose_name="Costo per Tempo",
        help_text="Costo basato sul tempo di percorrenza stimato",
    )

    cost_scenic = models.FloatField(
        default=0.0,
        verbose_name="Costo Panoramico",
        help_text="Costo per l'ottimizzazione panoramica",
    )

    cost_balanced = models.FloatField(
        default=0.0,
        verbose_name="Costo Bilanciato",
        help_text="Costo per l'ottimizzazione bilanciata (50/50)",
    )

    # Timestamps
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name="Data creazione",
    )

    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name="Data aggiornamento",
    )

    # Status flags
    is_active = models.BooleanField(
        default=True,
        verbose_name="Attivo",
        help_text="Indica se il segmento è attivo per il routing",
    )

    class Meta:
        """Meta class for RoadSegment."""

        verbose_name = "Segmento Stradale"
        verbose_name_plural = "Segmenti Stradali"
        indexes = [
            models.Index(fields=["highway"]),
            models.Index(fields=["source", "target"]),
            models.Index(fields=["scenic_rating"]),
            models.Index(fields=["curvature"]),
            models.Index(fields=["poi_density"]),
            models.Index(fields=["is_active"]),
            models.Index(fields=["osm_id"]),
            models.Index(fields=["region"]),
        ]
        ordering = ["id"]

    def __str__(self):
        """This function returns the string representation of the object."""
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

            coords = list(self.geometry.coords)
            if coords:
                # Start coordinates (longitude, latitude)
                self.x1, self.y1 = coords[0]
                # End coordinates (longitude, latitude)
                self.x2, self.y2 = coords[-1]

        super().save(*args, **kwargs)

    def calculate_scenic_cost(self, alpha=1.0, beta=0.5):
        """
        Calculate scenic routing cost using project formula.
        Formula: C = α × length - β × scenic_score.
        """
        # Convert scenic rating (0-10) to scenic score (0-1000)
        scenic_score = self.scenic_rating * 100

        # Apply formula: C = α × distance - β × scenic_score
        return (alpha * self.length_m) - (beta * scenic_score)

    def calculate_balanced_cost(self, distance_weight=0.6, scenic_weight=0.4):
        """Calculate balanced routing cost."""
        # Normalize values
        normalized_distance = self.length_m / 1000  # Convert to km for scaling
        normalized_scenic = self.scenic_rating / 10  # Convert to 0-1 range

        # Balanced cost: weighted combination
        return (distance_weight * normalized_distance) - (
                scenic_weight * normalized_scenic
        )

    def get_scenic_category(self):
        """Categorize segment based on scenic rating."""
        if self.scenic_rating >= 8.0:
            return "eccezionale"
        elif self.scenic_rating >= 6.0:
            return "ottimo"
        elif self.scenic_rating >= 4.0:
            return "buono"
        elif self.scenic_rating >= 2.0:
            return "mediocre"
        else:
            return "povero"

    @property
    def length_km(self):
        """Return length in kilometers."""
        return self.length_m / 1000

    @property
    def estimated_time_min(self):
        """Estimate travel time in minutes based on maxspeed."""
        if self.maxspeed and self.maxspeed > 0:
            # Convert maxspeed from km/h to m/s, then calculate time
            speed_mps = self.maxspeed / 3.6
            time_seconds = self.length_m / speed_mps
            return time_seconds / 60
        else:
            # Default speed: 50 km/h for unclassified roads
            default_speed_mps = 50 / 3.6
            time_seconds = self.length_m / default_speed_mps
            return time_seconds / 60


class RoadSegmentNoded(models.Model):
    """
    Noded road network for pgRouting v4.0.
    This model will be created by pgr_nodeNetwork function.
    """
    gid = models.AutoField(primary_key=True)
    old_id = models.BigIntegerField(
        blank=True,
        null=True,
        verbose_name="Original Segment ID",
        db_index=True,
    )

    # Copy fields from RoadSegment
    name = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        verbose_name="Nome della Strada",
    )

    geometry = models.LineStringField(
        srid=4326,
        verbose_name="Tracciato",
    )

    length_m = models.FloatField(
        default=0.0,
        verbose_name="Lunghezza (metri)",
    )

    cost_length = models.FloatField(
        default=0.0,
        verbose_name="Costo per Lunghezza",
    )

    cost_time = models.FloatField(
        default=0.0,
        verbose_name="Costo per Tempo",
    )

    cost_scenic = models.FloatField(
        default=0.0,
        verbose_name="Costo Panoramico",
    )

    cost_balanced = models.FloatField(
        default=0.0,
        verbose_name="Costo Bilanciato",
    )

    # pgRouting topology
    source = models.BigIntegerField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Nodo di Partenza",
    )

    target = models.BigIntegerField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name="Nodo di Arrivo",
    )

    # A* coordinates
    x1 = models.FloatField(blank=True, null=True)
    y1 = models.FloatField(blank=True, null=True)
    x2 = models.FloatField(blank=True, null=True)
    y2 = models.FloatField(blank=True, null=True)

    def save(self, *args, **kwargs):
        # Auto-calculate x1,y1,x2,y2 if geometry exists
        if self.geometry and self.geometry.length > 0:
            coords = list(self.geometry.coords)
            if coords:
                self.x1, self.y1 = coords[0]  # Start point
                self.x2, self.y2 = coords[-1]  # End point
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = "Segmento Stradale (Noded)"
        verbose_name_plural = "Segmenti Stradali (Noded)"
        indexes = [
            models.Index(fields=['source', 'target']),
            models.Index(fields=['source']),
            models.Index(fields=['target']),
            models.Index(fields=['old_id']),
        ]
        db_table = 'road_segment_noded'

    def __str__(self):
        return f"Noded Segment {self.gid} (orig: {self.old_id})"

class RoadSegmentPOIRelation(models.Model):
    """
    Pre-computed relationships between road segments and Points of Interest.
    This dramatically speeds up POI queries during route calculation.
    Instead of doing expensive spatial joins at query time, RoadSegmentPOIRelation pre-compute
    which POIs are near which road segments and cache the results.
    """

    road_segment = models.ForeignKey(
        'gis_data.RoadSegment',
        on_delete=models.CASCADE,
        related_name='poi_relations',
        db_index=True,
        verbose_name="Road Segment"
    )

    poi = models.ForeignKey(
        'gis_data.PointOfInterest',
        on_delete=models.CASCADE,
        related_name='segment_relations',
        db_index=True,
        verbose_name="Point of Interest"
    )

    distance_m = models.FloatField(
        verbose_name="Distance (meters)",
        help_text="Distance from POI to road segment in meters"
    )

    # For filtering and scoring
    is_within_max_distance = models.BooleanField(
        default=True,
        db_index=True,
        verbose_name="Within Max Distance",
        help_text="Whether this POI is within the maximum configured distance"
    )

    # For different preference types (fast, balanced, most_winding)
    scenic_value_cache = models.FloatField(
        default=0.0,
        verbose_name="Cached Scenic Value",
        help_text="Pre-calculated scenic value for this POI-segment pair"
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Road Segment-POI Relation"
        verbose_name_plural = "Road Segment-POI Relations"
        unique_together = [['road_segment', 'poi']]
        indexes = [
            models.Index(fields=['road_segment', 'is_within_max_distance', 'scenic_value_cache']),
            models.Index(fields=['poi', 'is_within_max_distance']),
            models.Index(fields=['scenic_value_cache']),
        ]

    def __str__(self):
        return f"Segment {self.road_segment_id} - POI {self.poi_id} ({self.distance_m:.1f}m)"