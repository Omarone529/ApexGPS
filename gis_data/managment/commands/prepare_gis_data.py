"""
GIS Data Preparation Management Command.

Initializes and prepares the spatial database for routing operations,
including topology creation, metric calculations, and scenic scoring.
"""

import sys
import logging
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.contrib.gis.geos import LineString, Point

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """
    Prepares GIS database for ApexGPS routing operations.

    Executes a six-step preparation pipeline:
    1. Verifies PostgreSQL extensions
    2. Creates sample data (optional/development)
    3. Builds routing topology with pgRouting
    4. Calculates road metrics
    5. Computes scenic scores
    6. Pre-calculates routing costs
    """

    help = "Prepares GIS database for routing operations"

    def add_arguments(self, parser):
        """
        Define command-line arguments for data preparation.
        """
        parser.add_argument(
            '--area',
            type=str,
            default='italy',
            help='Geographic region identifier'
        )
        parser.add_argument(
            '--tolerance',
            type=float,
            default=0.00001,
            help='Topology tolerance in degrees (~1 meter)'
        )
        parser.add_argument(
            '--sample',
            action='store_true',
            help='Generate sample data for testing'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force topology recreation'
        )

    def handle(self, *args, **options):
        """Execute GIS data preparation pipeline."""
        self.stdout.write(self.style.SUCCESS("Starting GIS data preparation..."))

        try:
            # Pipeline execution
            self._check_postgis_extensions()

            if options['sample'] or self._is_database_empty():
                self._create_sample_data()

            self._create_pgrouting_topology(
                tolerance=options['tolerance'],
                force=options.get('force', False)
            )

            self._calculate_road_metrics()
            self._calculate_scenic_scores()
            self._calculate_routing_costs()

            self.stdout.write(self.style.SUCCESS("GIS data preparation completed."))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Preparation failed: {str(e)}"))
            logger.exception("GIS data preparation error")
            sys.exit(1)

    def _check_postgis_extensions(self):
        """
        Verify if required PostgreSQL extensions are available.
        install pgRouting if missing. Raises exception on failure.
        """
        required_extensions = ['postgis', 'pgrouting']

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT extname, extversion 
                FROM pg_extension 
                WHERE extname = ANY(%s)
            """, [required_extensions])

            installed = {row[0]: row[1] for row in cursor.fetchall()}

        for ext in required_extensions:
            if ext in installed:
                self.stdout.write(f"  {ext}: v{installed[ext]}")
            elif ext == 'pgrouting':
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("CREATE EXTENSION IF NOT EXISTS pgrouting")
                    self.stdout.write(f"  {ext}: installed")
                except Exception as e:
                    raise Exception(f"Cannot install {ext}: {str(e)}")
            else:
                raise Exception(f"Extension {ext} not installed")

    def _is_database_empty(self):
        """Determine if road segment data needs initialization."""
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT EXISTS(
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'gis_data_roadsegment'
                )
            """)
            table_exists = cursor.fetchone()[0]

            if not table_exists:
                return True

            cursor.execute("SELECT COUNT(*) FROM gis_data_roadsegment")
            return cursor.fetchone()[0] == 0

    def _create_sample_data(self):
        """
        Generate sample road network and points of interest.
        Creates a basic grid-based road network with representative Points of Interest for system testing.
        """
        from gis_data.models import RoadSegment, PointOfInterest

        # Clear existing sample data
        RoadSegment.objects.all().delete()
        PointOfInterest.objects.all().delete()

        # Create sample Points of Interest
        sample_pois = [
            PointOfInterest(
                name="Lake Como",
                category="lake",
                location=Point(9.2669, 46.0160, srid=4326),
                description="Alpine lake in Northern Italy"
            ),
            PointOfInterest(
                name="Stelvio Pass",
                category="mountain_pass",
                location=Point(10.4531, 46.5286, srid=4326),
                description="Alpine pass with 48 hairpin turns"
            ),
        ]

        for poi in sample_pois:
            poi.save()

        # Create road segment grid
        segments = []

        # Horizontal roads
        for lat in [46.0, 46.1, 46.2]:
            geometry = LineString((9.0, lat, 9.5, lat), srid=4326)
            segments.append(RoadSegment(
                name=f"Road at {lat}°N",
                highway="secondary",
                geometry=geometry,
                maxspeed=70,
                oneway=False
            ))

        # Vertical roads
        for lon in [9.0, 9.25, 9.5]:
            geometry = LineString((lon, 46.0, lon, 46.2), srid=4326)
            segments.append(RoadSegment(
                name=f"Road at {lon}°E",
                highway="secondary",
                geometry=geometry,
                maxspeed=70,
                oneway=False
            ))

        # Scenic mountain road
        geometry = LineString(
            [(9.1, 46.05), (9.15, 46.08), (9.2, 46.06),
             (9.25, 46.09), (9.3, 46.07)],
            srid=4326
        )
        segments.append(RoadSegment(
            name="Scenic Mountain Road",
            highway="tertiary",
            geometry=geometry,
            maxspeed=50,
            oneway=False,
            scenic_rating=8.5
        ))

        RoadSegment.objects.bulk_create(segments)

        self.stdout.write(f"  Created {len(segments)} sample road segments")
        self.stdout.write(f"  Created {len(sample_pois)} sample Points of Interest")

    @transaction.atomic
    def _create_pgrouting_topology(self, tolerance=0.00001, force=False):
        """Build routing graph topology using pgRouting."""
        self.stdout.write(f"Creating pgRouting topology (tolerance: {tolerance}°)")

        with connection.cursor() as cursor:
            # Check existing topology columns
            cursor.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'gis_data_roadsegment' 
                AND column_name IN ('source', 'target')
            """)
            existing_columns = {row[0] for row in cursor.fetchall()}

            if {'source', 'target'}.issubset(existing_columns) and not force:
                self.stdout.write("  Topology exists (use --force to recreate)")
                return

            # Add missing columns
            if 'source' not in existing_columns:
                cursor.execute("ALTER TABLE gis_data_roadsegment ADD COLUMN source INTEGER")
            if 'target' not in existing_columns:
                cursor.execute("ALTER TABLE gis_data_roadsegment ADD COLUMN target INTEGER")

            # Ensure spatial index exists
            cursor.execute("""
                SELECT COUNT(*) FROM pg_indexes 
                WHERE tablename = 'gis_data_roadsegment' 
                AND indexdef LIKE '%geometry%'
            """)
            if cursor.fetchone()[0] == 0:
                cursor.execute("""
                    CREATE INDEX roadsegment_geometry_idx 
                    ON gis_data_roadsegment USING GIST (geometry)
                """)

            # Execute pgRouting topology creation
            cursor.execute(f"""
                SELECT pgr_createTopology(
                    'gis_data_roadsegment',
                    {tolerance},
                    'geometry',
                    'id',
                    'source',
                    'target',
                    rows_where := 'geometry IS NOT NULL',
                    clean := TRUE
                )
            """)

            result = cursor.fetchone()[0]
            self.stdout.write(f"  pgr_createTopology: {result}")

            # Count resulting graph elements
            cursor.execute("SELECT COUNT(*) FROM gis_data_roadsegment_vertices_pgr")
            vertices = cursor.fetchone()[0]

            cursor.execute("""
                SELECT COUNT(*) FROM gis_data_roadsegment 
                WHERE source IS NOT NULL AND target IS NOT NULL
            """)
            edges = cursor.fetchone()[0]

            self.stdout.write(f"  Graph vertices: {vertices}")
            self.stdout.write(f"  Valid edges: {edges}")

    def _calculate_road_metrics(self):
        """Calculate physical road segment metrics."""
        self.stdout.write("Calculating road metrics...")

        with connection.cursor() as cursor:
            # Calculate geographic length
            cursor.execute("""
                UPDATE gis_data_roadsegment 
                SET length_m = ST_Length(geometry::geography)
                WHERE geometry IS NOT NULL 
                AND (length_m = 0 OR length_m IS NULL)
            """)
            self.stdout.write(f"  Lengths calculated: {cursor.rowcount}")

            # Calculate curvature
            cursor.execute("""
                UPDATE gis_data_roadsegment 
                SET curvature = CASE 
                    WHEN ST_Length(geometry::geography) > 0 
                    THEN ST_Length(geometry::geography) / 
                         ST_Distance(ST_StartPoint(geometry), ST_EndPoint(geometry)::geography)
                    ELSE 1.0 
                END
                WHERE geometry IS NOT NULL
            """)
            self.stdout.write(f"  Curvature calculated: {cursor.rowcount}")

    def _calculate_scenic_scores(self):
        """
        Compute scenic quality scores for road segments.

        Scores are based on:
        1. Density of nearby Points of Interest
        2. Road type classification (pre-assigned scores)
        """
        self.stdout.write("Calculating scenic scores...")

        with connection.cursor() as cursor:
            # POI density within 1km buffer
            cursor.execute("""
                UPDATE gis_data_roadsegment rs
                SET poi_density = (
                    SELECT COUNT(*)::float / GREATEST(rs.length_m, 1000)
                    FROM gis_data_pointofinterest poi
                    WHERE ST_DWithin(
                        rs.geometry::geography,
                        poi.location::geography,
                        1000
                    )
                )
                WHERE rs.geometry IS NOT NULL
            """)
            self.stdout.write(f"  POI density calculated: {cursor.rowcount}")

            # Assign scenic ratings by road type
            cursor.execute("""
                UPDATE gis_data_roadsegment 
                SET scenic_rating = CASE 
                    WHEN highway IN ('trunk', 'primary') THEN 3.0
                    WHEN highway IN ('secondary', 'tertiary') THEN 5.0
                    WHEN highway IN ('unclassified', 'residential') THEN 4.0
                    WHEN highway IN ('track', 'path') THEN 7.0
                    ELSE 5.0 
                END
                WHERE scenic_rating = 0
            """)
            self.stdout.write(f"  Scenic ratings assigned: {cursor.rowcount}")

    def _calculate_routing_costs(self):
        """
        Pre-calculate routing costs for different optimization strategies.

        Cost functions implement the formula: C = α·distance - β·scenic_score
        where α and β are tunable parameters for different routing preferences.
        """
        self.stdout.write("Calculating routing costs...")

        with connection.cursor() as cursor:
            # Distance-based cost (α=1, β=0)
            cursor.execute("""
                UPDATE gis_data_roadsegment 
                SET 
                    cost_length = length_m,
                    cost_time = CASE 
                        WHEN maxspeed > 0 THEN length_m / (maxspeed / 3.6)
                        ELSE length_m / (50 / 3.6)
                    END,
                    cost_scenic = length_m * (10 - scenic_rating) / 10
                WHERE length_m > 0
            """)
            self.stdout.write(f"  Costs calculated for {cursor.rowcount} segments")

            # Create performance indexes
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_roadsegment_source_target 
                ON gis_data_roadsegment(source, target)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_roadsegment_geometry 
                ON gis_data_roadsegment USING GIST(geometry)
            """)
            self.stdout.write("  Performance indexes created")