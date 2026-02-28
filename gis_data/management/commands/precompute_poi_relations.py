import logging
import time
from django.core.management.base import BaseCommand
from django.db import connection, transaction

from gis_data.models import RoadSegment, PointOfInterest, RoadSegmentPOIRelation
from gis_data.utils.poi_scoring import calculate_poi_scenic_value

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Pre-compute relationships between road segments and POIs for faster routing'

    def add_arguments(self, parser):
        parser.add_argument(
            '--batch-size',
            type=int,
            default=1000,
            help='Number of segments to process per batch (default: 1000)'
        )
        parser.add_argument(
            '--max-distance',
            type=float,
            default=2500.0,
            help='Maximum distance in meters to consider (default: 2500)'
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing relations before computing'
        )
        parser.add_argument(
            '--region',
            type=str,
            help='Process only a specific region (optional)'
        )
        parser.add_argument(
            '--verbose',
            action='store_true',
            help='Show detailed output'
        )

    def handle(self, *args, **options):
        batch_size = options['batch_size']
        max_distance = options['max_distance']
        region = options.get('region')

        self.stdout.write("=" * 70)
        self.stdout.write("POI-ROAD SEGMENT RELATIONS PRE-COMPUTATION")
        self.stdout.write("=" * 70)
        self.stdout.write(f"Max distance: {max_distance}m")
        self.stdout.write(f"Batch size: {batch_size}")
        if region:
            self.stdout.write(f"Region: {region}")

        # Clear existing relations if requested
        if options['clear']:
            self._clear_relations(region)

        # Get counts for progress tracking
        road_filters = {'is_active': True}
        if region:
            road_filters['region'] = region

        total_segments = RoadSegment.objects.filter(**road_filters).count()
        total_pois = PointOfInterest.objects.filter(is_active=True).count()

        if total_segments == 0:
            self.stdout.write(self.style.WARNING("\nNo road segments found. Nothing to do."))
            return

        if total_pois == 0:
            self.stdout.write(self.style.WARNING("\nNo POIs found. Nothing to do."))
            return

        self.stdout.write(f"\nProcessing:")
        self.stdout.write(f"  • {total_segments:,} active road segments")
        self.stdout.write(f"  • {total_pois:,} active POIs")
        self.stdout.write(f"  • Estimated relations: ~{total_segments * min(total_pois, 10):,}")

        # Get segment IDs to process
        segment_ids = list(
            RoadSegment.objects.filter(**road_filters)
            .values_list('id', flat=True)
            .order_by('id')
        )

        processed = 0
        created = 0
        start_time = time.time()

        self.stdout.write(f"\nStarting batch processing...")

        for i in range(0, len(segment_ids), batch_size):
            batch = segment_ids[i:i + batch_size]

            # Process this batch
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("""
                        INSERT INTO gis_data_roadsegmentpoirelation 
                            (road_segment_id, poi_id, distance_m, 
                             is_within_max_distance, scenic_value_cache,
                             created_at, updated_at)
                        SELECT 
                            rs.id,
                            poi.id,
                            ST_Distance(rs.geometry::geography, poi.location::geography) as distance,
                            ST_DWithin(rs.geometry::geography, poi.location::geography, %s),
                            0.0,
                            NOW(), NOW()
                        FROM gis_data_roadsegment rs
                        CROSS JOIN gis_data_pointofinterest poi
                        WHERE rs.id = ANY(%s)
                          AND poi.is_active = true
                          AND ST_DWithin(rs.geometry::geography, poi.location::geography, %s)
                        ON CONFLICT (road_segment_id, poi_id) 
                        DO UPDATE SET 
                            distance_m = EXCLUDED.distance_m,
                            is_within_max_distance = EXCLUDED.is_within_max_distance,
                            updated_at = NOW();
                    """, [max_distance, batch, max_distance])

                    batch_created = cursor.rowcount
                    created += batch_created

                processed += len(batch)

                # Progress update
                if options['verbose'] or (i + batch_size) % (batch_size * 5) == 0:
                    elapsed = time.time() - start_time
                    rate = processed / elapsed if elapsed > 0 else 0
                    pct = (processed / total_segments) * 100
                    self.stdout.write(
                        f"  Progress: {processed:6,}/{total_segments:,} segments "
                        f"({pct:4.1f}%) - "
                        f"relations: {created:8,} - "
                        f"rate: {rate:5.1f} seg/sec"
                    )

        elapsed_total = time.time() - start_time
        self.stdout.write("\n" + "-" * 70)
        self.stdout.write(
            self.style.SUCCESS(
                f"✓ Processed {processed:,} segments, "
                f"created {created:,} POI relations "
                f"in {elapsed_total:.1f} seconds"
            )
        )

        # Update scenic value cache
        self.stdout.write("\n📊 Updating scenic value cache...")
        self._update_scenic_value_cache(max_distance, region, options['verbose'])

        # Final statistics
        self._show_statistics(region)

    def _clear_relations(self, region=None):
        """Clear existing relations."""
        self.stdout.write("\nClearing existing relations...")

        if region:
            deleted = RoadSegmentPOIRelation.objects.filter(
                road_segment__region=region
            ).delete()[0]
            self.stdout.write(f"  Deleted {deleted} relations for region {region}")
        else:
            count = RoadSegmentPOIRelation.objects.count()
            RoadSegmentPOIRelation.objects.all().delete()
            self.stdout.write(f"  Deleted {count} relations")

    def _update_scenic_value_cache(self, max_distance, region=None, verbose=False):
        """Update the cached scenic value for all relations."""
        # Get all relations within max distance
        filters = {
            'is_within_max_distance': True,
            'distance_m__lte': max_distance
        }
        if region:
            filters['road_segment__region'] = region

        relations = RoadSegmentPOIRelation.objects.filter(
            **filters
        ).select_related('poi')

        total = relations.count()
        if total == 0:
            self.stdout.write("  No relations to update")
            return

        # Pre-calculate segment counts per POI for efficiency
        self.stdout.write(f"  Calculating segment counts for {total} relations...")

        poi_segment_counts = {}
        for rel in relations.only('poi_id').iterator():
            if rel.poi_id not in poi_segment_counts:
                count = RoadSegmentPOIRelation.objects.filter(
                    poi_id=rel.poi_id,
                    is_within_max_distance=True
                ).count()
                poi_segment_counts[rel.poi_id] = count

        # Update in chunks
        updated = 0
        start_time = time.time()
        chunk_size = 500

        for i in range(0, total, chunk_size):
            chunk = relations[i:i + chunk_size]

            with transaction.atomic():
                for relation in chunk:
                    segment_count = poi_segment_counts.get(relation.poi_id, 1)

                    scenic_value = calculate_poi_scenic_value(
                        category=relation.poi.category,
                        importance_score=relation.poi.importance_score,
                        segment_count=segment_count,
                        distance_m=relation.distance_m,
                        max_distance_m=max_distance
                    )

                    relation.scenic_value_cache = scenic_value
                    relation.save(update_fields=['scenic_value_cache'])
                    updated += 1

            if verbose and (i + chunk_size) % (chunk_size * 4) == 0:
                elapsed = time.time() - start_time
                pct = (updated / total) * 100
                self.stdout.write(
                    f"    Scenic values: {updated:6,}/{total:,} ({pct:4.1f}%)"
                )

        elapsed = time.time() - start_time
        self.stdout.write(
            self.style.SUCCESS(
                f"  ✓ Updated scenic values for {updated:,} relations "
                f"in {elapsed:.1f} seconds"
            )
        )

    def _show_statistics(self, region=None):
        """Show statistics about the relations."""
        filters = {}
        if region:
            filters['road_segment__region'] = region

        total = RoadSegmentPOIRelation.objects.filter(**filters).count()
        within = RoadSegmentPOIRelation.objects.filter(
            **filters, is_within_max_distance=True
        ).count()

        # Get unique POIs with relations
        unique_pois = RoadSegmentPOIRelation.objects.filter(
            **filters
        ).values('poi').distinct().count()

        # Get segments with at least one POI
        segments_with_pois = RoadSegmentPOIRelation.objects.filter(
            **filters, is_within_max_distance=True
        ).values('road_segment').distinct().count()

        total_segments = RoadSegment.objects.filter(
            is_active=True, **(filters if region else {})
        ).count()

        self.stdout.write("\n" + "=" * 70)
        self.stdout.write("RELATIONS STATISTICS")
        self.stdout.write("=" * 70)
        self.stdout.write(f"Total relations: {total:,}")
        self.stdout.write(f"Within max distance: {within:,}")
        self.stdout.write(f"Unique POIs with relations: {unique_pois:,}")

        if total_segments > 0:
            pct = (segments_with_pois / total_segments) * 100
            self.stdout.write(
                f"Segments with POIs: {segments_with_pois:,} ({pct:.1f}% of total)"
            )

        if segments_with_pois > 0:
            avg_pois = within / segments_with_pois
            self.stdout.write(f"Average POIs per segment: {avg_pois:.1f}")

        # Show top categories
        self.stdout.write("\nTop POI categories:")
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT poi.category, COUNT(*)
                FROM gis_data_roadsegmentpoirelation rel
                JOIN gis_data_pointofinterest poi ON rel.poi_id = poi.id
                WHERE rel.is_within_max_distance = true
                GROUP BY poi.category
                ORDER BY COUNT(*) DESC
                LIMIT 5
            """)
            for category, count in cursor.fetchall():
                self.stdout.write(f"  • {category}: {count:,}")

        self.stdout.write("=" * 70)