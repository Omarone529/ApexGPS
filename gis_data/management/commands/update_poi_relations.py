from django.core.management.base import BaseCommand
from django.core.management import call_command
from gis_data.models import RoadSegment, PointOfInterest, RoadSegmentPOIRelation


class Command(BaseCommand):
    help = 'Update POI relations incrementally (new segments/POIs only)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--full',
            action='store_true',
            help='Perform full rebuild instead of incremental update'
        )
        parser.add_argument(
            '--region',
            type=str,
            help='Update only a specific region'
        )
        parser.add_argument(
            '--max-distance',
            type=float,
            default=2500.0,
            help='Maximum distance in meters to consider (default: 2500)'
        )

    def handle(self, *args, **options):
        self.stdout.write("=" * 70)
        self.stdout.write("POI RELATIONS INCREMENTAL UPDATE")
        self.stdout.write("=" * 70)

        if options['full']:
            self.stdout.write("\nPerforming full rebuild...")
            args = ['precompute_poi_relations', '--clear']
            if options['region']:
                args.extend(['--region', options['region']])
            if options['max_distance']:
                args.extend(['--max-distance', str(options['max_distance'])])
            call_command(*args)
            return

        self.stdout.write("\nChecking for new data...")

        # Find segments without relations
        filters = {'is_active': True}
        if options['region']:
            filters['region'] = options['region']

        segments_without = RoadSegment.objects.filter(**filters).exclude(
            id__in=RoadSegmentPOIRelation.objects.values('road_segment_id')
        )
        count_segments = segments_without.count()

        if count_segments > 0:
            self.stdout.write(f"  Found {count_segments} new segments")
            # Process only new segments
            args = ['precompute_poi_relations']
            if options['region']:
                args.extend(['--region', options['region']])
            if options['max_distance']:
                args.extend(['--max-distance', str(options['max_distance'])])
            args.extend(['--batch-size', '100'])
            call_command(*args)
        else:
            self.stdout.write("  No new segments found")

        # Check for new POIs
        pois_without = PointOfInterest.objects.filter(is_active=True).exclude(
            id__in=RoadSegmentPOIRelation.objects.values('poi_id')
        )
        count_pois = pois_without.count()

        if count_pois > 0:
            self.stdout.write(f"  Found {count_pois} new POIs")
            self.stdout.write("  Running full rebuild to include new POIs...")
            args = ['precompute_poi_relations', '--clear', '--batch-size', '500']
            if options['region']:
                args.extend(['--region', options['region']])
            if options['max_distance']:
                args.extend(['--max-distance', str(options['max_distance'])])
            call_command(*args)
        else:
            self.stdout.write("  No new POIs found")

        # Final status
        total = RoadSegmentPOIRelation.objects.filter(
            road_segment__is_active=True,
            poi__is_active=True
        ).count()

        self.stdout.write("\n" + "-" * 70)
        self.stdout.write(
            self.style.SUCCESS(f"✓ Update completed. Total relations: {total:,}")
        )