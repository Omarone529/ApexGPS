from django.core.management.base import BaseCommand
from django.db import connection
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Set up pgRouting v4.0 topology for new database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--tolerance',
            type=float,
            default=0.00001,
            help='Tolerance for topology creation (default: 0.00001)'
        )
        parser.add_argument(
            '--use-noded',
            action='store_true',
            default=True,
            help='Use noded network (recommended)'
        )
        parser.add_argument(
            '--skip-costs',
            action='store_true',
            default=False,
            help='Skip updating routing costs'
        )

    def handle(self, *args, **options):
        self.stdout.write("Setting up pgRouting v4.0 for new database...")

        # Check if we have data
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM gis_data_roadsegment")
            count = cursor.fetchone()[0]

            if count == 0:
                self.stdout.write(self.style.WARNING("No road segments found. Please import data first."))
                return

        # Import and use topology service
        try:
            from routing.services.topology_service_v4 import TopologyServiceV4
        except ImportError:
            self.stdout.write(self.style.ERROR("Topology service not found. Creating it..."))
            # Create a simple topology service inline
            from django.db import transaction

            self.stdout.write("Creating topology...")

            with connection.cursor() as cursor:
                try:
                    # Simple topology creation
                    cursor.execute(f"""
                        SELECT pgr_createTopology(
                            'gis_data_roadsegment',
                            {options['tolerance']},
                            'geometry',
                            'id',
                            'source',
                            'target',
                            clean := true
                        );
                    """)
                    self.stdout.write(self.style.SUCCESS("✓ Topology created"))

                    # Create indexes
                    cursor.execute("""
                                   CREATE INDEX IF NOT EXISTS roadsegment_source_idx
                                       ON gis_data_roadsegment(source);

                                   CREATE INDEX IF NOT EXISTS roadsegment_target_idx
                                       ON gis_data_roadsegment(target);

                                   CREATE INDEX IF NOT EXISTS roadsegment_geometry_idx
                                       ON gis_data_roadsegment USING GIST(geometry);
                                   """)
                    self.stdout.write(self.style.SUCCESS("✓ Indexes created"))

                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Failed: {e}"))
                    return
        else:
            # Use the topology service
            service = TopologyServiceV4()

            # Create topology
            result = service.create_topology(
                tolerance=options['tolerance'],
                use_noded=options['use_noded']
            )

            if result['success']:
                self.stdout.write(self.style.SUCCESS("✓ Topology created"))
                self.stdout.write(f"  Vertices: {result.get('vertices', 0)}")
                self.stdout.write(f"  Edges: {result.get('edges', 0)}")
            else:
                self.stdout.write(self.style.ERROR(f"Failed: {result.get('error')}"))
                return

        # Update routing costs if requested
        if not options['skip_costs']:
            self.stdout.write("Updating routing costs...")
            with connection.cursor() as cursor:
                # Update scenic cost using your formula
                cursor.execute("""
                               UPDATE gis_data_roadsegment
                               SET cost_scenic   = (1.0 * length_m) - (0.5 * scenic_rating * 100),
                                   cost_balanced = (0.6 * length_m / 1000) - (0.4 * scenic_rating / 10)
                               WHERE geometry IS NOT NULL;
                               """)
                self.stdout.write(self.style.SUCCESS("✓ Routing costs updated"))

        # Final validation
        self.stdout.write("Validating setup...")
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM gis_data_roadsegment_vertices_pgr")
            vertices = cursor.fetchone()[0]

            cursor.execute("""
                           SELECT COUNT(*)
                           FROM gis_data_roadsegment
                           WHERE source IS NOT NULL
                             AND target IS NOT NULL
                           """)
            edges = cursor.fetchone()[0]

            self.stdout.write(self.style.SUCCESS("✓ Setup complete!"))
            self.stdout.write(f"  Routing vertices: {vertices}")
            self.stdout.write(f"  Routable edges: {edges}")
            self.stdout.write(f"  Network coverage: {(edges / vertices * 100):.1f}%" if vertices > 0 else "0%")