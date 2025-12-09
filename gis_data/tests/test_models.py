from django.contrib.gis.geos import LineString, MultiPolygon, Point, Polygon
from django.test import TestCase

from gis_data.models import PointOfInterest, RoadSegment, ScenicArea


class PointOfInterestModelTest(TestCase):
    """Test suite for PointOfInterest model."""

    def setUp(self):
        """Create test POI."""
        self.poi = PointOfInterest.objects.create(
            name="Test POI",
            category="monument",
            location=Point(9.0, 45.0, srid=4326),
            description="A test point of interest",
        )

    def test_poi_creation(self):
        """Test POI creation."""
        self.assertEqual(self.poi.name, "Test POI")
        self.assertEqual(self.poi.category, "monument")
        self.assertEqual(self.poi.location.x, 9.0)
        self.assertEqual(self.poi.location.y, 45.0)
        self.assertEqual(self.poi.description, "A test point of interest")

    def test_string_representation(self):
        """Test string representation."""
        self.assertEqual(str(self.poi), "Test POI")

    def test_indexes(self):
        """Test that indexes are defined."""
        indexes = [idx.fields for idx in PointOfInterest._meta.indexes]
        self.assertIn(["category"], indexes)
        self.assertIn(["name"], indexes)

    def test_verbose_names(self):
        """Test verbose names."""
        self.assertEqual(PointOfInterest._meta.verbose_name, "Punto di Interesse")
        self.assertEqual(
            PointOfInterest._meta.verbose_name_plural, "Punti di Interesse"
        )


class ScenicAreaModelTest(TestCase):
    """Test suite for ScenicArea model."""

    def setUp(self):
        """Create test scenic area."""
        # Create a simple polygon
        polygon = Polygon(
            ((9.0, 45.0), (10.0, 45.0), (10.0, 46.0), (9.0, 46.0), (9.0, 45.0))
        )
        multi_polygon = MultiPolygon(polygon)

        self.scenic_area = ScenicArea.objects.create(
            name="Test Scenic Area",
            area_type="national_park",
            bonus_value=1.5,
            area=multi_polygon,
        )

    def test_scenic_area_creation(self):
        """Test scenic area creation."""
        self.assertEqual(self.scenic_area.name, "Test Scenic Area")
        self.assertEqual(self.scenic_area.area_type, "national_park")
        self.assertEqual(self.scenic_area.bonus_value, 1.5)
        self.assertIsNotNone(self.scenic_area.area)

    def test_string_representation(self):
        """Test string representation."""
        self.assertEqual(str(self.scenic_area), "Test Scenic Area")

    def test_verbose_names(self):
        """Test verbose names."""
        self.assertEqual(ScenicArea._meta.verbose_name, "Area Scenica")
        self.assertEqual(ScenicArea._meta.verbose_name_plural, "Aree Sceniche")


class RoadSegmentModelTest(TestCase):
    """Test suite for RoadSegment model."""

    def setUp(self):
        """Create test road segment."""
        # Use coordinates that are closer together
        # (9.0, 45.0) to (9.0001, 45.0001) is approximately 15.7m
        line = LineString(((9.0, 45.0), (9.0001, 45.0001)), srid=4326)

        self.road_segment = RoadSegment.objects.create(
            osm_id=123456,
            name="Test Road",
            geometry=line,
            highway="secondary",
            maxspeed=70,
            oneway=False,
            surface="asphalt",
            lanes=2,
            curvature=1.2,
            elevation_gain=50.0,
            scenic_rating=7.5,
            poi_density=2.5,
            source=1,
            target=2,
            cost_length=1000.0,
            cost_time=72.0,
            cost_scenic=250.0,
        )

    def test_road_segment_creation(self):
        """Test road segment creation."""
        self.assertEqual(self.road_segment.osm_id, 123456)
        self.assertEqual(self.road_segment.name, "Test Road")
        self.assertEqual(self.road_segment.highway, "secondary")
        self.assertEqual(self.road_segment.maxspeed, 70)
        self.assertFalse(self.road_segment.oneway)
        self.assertEqual(self.road_segment.surface, "asphalt")
        self.assertEqual(self.road_segment.lanes, 2)
        self.assertEqual(self.road_segment.curvature, 1.2)
        self.assertEqual(self.road_segment.elevation_gain, 50.0)
        self.assertEqual(self.road_segment.scenic_rating, 7.5)
        self.assertEqual(self.road_segment.poi_density, 2.5)
        self.assertEqual(self.road_segment.source, 1)
        self.assertEqual(self.road_segment.target, 2)
        self.assertEqual(self.road_segment.cost_length, 1000.0)
        self.assertEqual(self.road_segment.cost_time, 72.0)
        self.assertEqual(self.road_segment.cost_scenic, 250.0)

        # Check that length was calculated
        self.assertGreater(self.road_segment.length_m, 15.0)
        self.assertLess(self.road_segment.length_m, 16.0)

    def test_string_representation(self):
        """Test string representation."""
        # Test with Name
        self.assertIn("Test Road", str(self.road_segment))
        self.assertIn("m)", str(self.road_segment))
        # Test with OSM ID
        self.road_segment.name = None
        self.road_segment.save()
        self.assertIn("OSM:123456", str(self.road_segment))

        # Test without name or OSM ID
        self.road_segment.osm_id = None
        self.road_segment.save()
        self.assertIn("Segmento", str(self.road_segment))

    def test_save_method_length_calculation(self):
        """Test that length is calculated on save if not provided."""
        # Create a new segment without specifying length
        # Use coordinates that are very close together
        line = LineString(((9.0, 45.0), (9.0001, 45.0001)), srid=4326)
        segment = RoadSegment.objects.create(
            name="New Road", geometry=line, highway="tertiary"
        )

        # Length should be calculated
        self.assertGreater(segment.length_m, 15.0)
        self.assertLess(segment.length_m, 16.0)

    def test_calculate_scenic_cost_method(self):
        """Test scenic cost calculation."""
        # Test with default parameters
        cost = self.road_segment.calculate_scenic_cost()
        # Use the actual length from the segment
        actual_length = self.road_segment.length_m
        expected = (1.0 * actual_length) - (
            0.5 * (self.road_segment.scenic_rating * 100)
        )
        self.assertAlmostEqual(cost, expected, places=2)

        # Test with custom parameters
        cost_custom = self.road_segment.calculate_scenic_cost(alpha=1.5, beta=0.8)
        expected_custom = (1.5 * actual_length) - (
            0.8 * (self.road_segment.scenic_rating * 100)
        )
        self.assertAlmostEqual(cost_custom, expected_custom, places=2)

    def test_indexes(self):
        """Test that indexes are defined."""
        indexes = [idx.fields for idx in RoadSegment._meta.indexes]
        self.assertIn(["highway"], indexes)
        self.assertIn(["source", "target"], indexes)
        self.assertIn(["scenic_rating"], indexes)

    def test_verbose_names(self):
        """Test verbose names."""
        self.assertEqual(RoadSegment._meta.verbose_name, "Segmento Stradale")
        self.assertEqual(RoadSegment._meta.verbose_name_plural, "Segmenti Stradali")
