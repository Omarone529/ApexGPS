from django.contrib.gis.geos import MultiPolygon, Point, Polygon
from django.test import TestCase

from gis_data.models import PointOfInterest, ScenicArea
from gis_data.serializers import PointOfInterestSerializer, ScenicAreaSerializer


class PointOfInterestSerializerTest(TestCase):
    """Test suite for PointOfInterestSerializer."""

    def setUp(self):
        """Create test data."""
        self.poi = PointOfInterest.objects.create(
            name="Test POI",
            category="monument",
            location=Point(9.0, 45.0, srid=4326),
            description="A test point",
        )

    def test_poi_serialization(self):
        """Test serializing a POI object."""
        serializer = PointOfInterestSerializer(self.poi)
        data = serializer.data

        self.assertEqual(data["name"], "Test POI")
        self.assertEqual(data["category"], "monument")
        self.assertEqual(data["description"], "A test point")

        # Check that lat/lon are in the output (from to_representation)
        self.assertEqual(data["latitude"], 45.0)
        self.assertEqual(data["longitude"], 9.0)

    def test_poi_deserialization_with_coordinates(self):
        """Test deserializing POI data with lat/lon."""
        new_poi_data = {
            "name": "New POI",
            "category": "lake",
            "latitude": 46.0,
            "longitude": 10.0,
            "description": "A new point",
        }

        serializer = PointOfInterestSerializer(data=new_poi_data)
        self.assertTrue(serializer.is_valid(), serializer.errors)

        poi = serializer.save()
        self.assertEqual(poi.name, "New POI")
        self.assertEqual(poi.location.x, 10.0)  # longitude
        self.assertEqual(poi.location.y, 46.0)  # latitude
        self.assertEqual(poi.category, "lake")

    def test_poi_deserialization_with_location_wkt(self):
        """Test deserializing POI data with WKT location."""
        new_poi_data = {
            "name": "WKT POI",
            "category": "park",
            "location": "POINT(11.0 47.0)",  # WKT format
            "description": "With WKT location",
        }

        serializer = PointOfInterestSerializer(data=new_poi_data)
        self.assertTrue(serializer.is_valid(), serializer.errors)

        poi = serializer.save()
        self.assertEqual(poi.name, "WKT POI")
        self.assertEqual(poi.location.x, 11.0)
        self.assertEqual(poi.location.y, 47.0)
        self.assertEqual(poi.category, "park")

    def test_poi_deserialization_missing_location(self):
        """Test that deserialization fails when no location data is provided."""
        new_poi_data = {
            "name": "Invalid POI",
            "category": "park",
            "description": "Missing location data",
        }

        serializer = PointOfInterestSerializer(data=new_poi_data)
        self.assertFalse(serializer.is_valid())
        self.assertIn("non_field_errors", serializer.errors)

    def test_poi_deserialization_partial_coordinates(self):
        """Test that deserialization fails when only one coordinate is provided."""
        # Test with only latitude
        new_poi_data = {
            "name": "Invalid POI",
            "category": "park",
            "latitude": 46.0,
            "description": "Missing longitude",
        }

        serializer = PointOfInterestSerializer(data=new_poi_data)
        self.assertFalse(serializer.is_valid())
        self.assertIn("non_field_errors", serializer.errors)

        # Test with only longitude
        new_poi_data = {
            "name": "Invalid POI",
            "category": "park",
            "longitude": 10.0,
            "description": "Missing latitude",
        }

        serializer = PointOfInterestSerializer(data=new_poi_data)
        self.assertFalse(serializer.is_valid())
        self.assertIn("non_field_errors", serializer.errors)

    def test_poi_update_with_coordinates(self):
        """Test updating a POI with new coordinates."""
        update_data = {"name": "Updated POI", "latitude": 46.5, "longitude": 10.5}

        serializer = PointOfInterestSerializer(self.poi, data=update_data, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)

        updated_poi = serializer.save()
        self.assertEqual(updated_poi.name, "Updated POI")
        self.assertEqual(updated_poi.location.x, 10.5)
        self.assertEqual(updated_poi.location.y, 46.5)
        # Other fields should remain unchanged
        self.assertEqual(updated_poi.category, "monument")

    def test_poi_update_with_location(self):
        """Test updating a POI with WKT location."""
        update_data = {"name": "Updated with WKT", "location": "POINT(12.0 48.0)"}

        serializer = PointOfInterestSerializer(self.poi, data=update_data, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)

        updated_poi = serializer.save()
        self.assertEqual(updated_poi.name, "Updated with WKT")
        self.assertEqual(updated_poi.location.x, 12.0)
        self.assertEqual(updated_poi.location.y, 48.0)
        # Other fields should remain unchanged
        self.assertEqual(updated_poi.category, "monument")

    def test_read_only_fields(self):
        """Test that id field is read-only."""
        self.assertIn("id", PointOfInterestSerializer.Meta.read_only_fields)


class ScenicAreaSerializerTest(TestCase):
    """Test suite for ScenicAreaSerializer."""

    def setUp(self):
        """Create test data."""
        # Create a simple polygon
        polygon = Polygon(
            ((9.0, 45.0), (10.0, 45.0), (10.0, 46.0), (9.0, 46.0), (9.0, 45.0))
        )
        multi_polygon = MultiPolygon(polygon)

        self.scenic_area = ScenicArea.objects.create(
            name="Test Area",
            area_type="national_park",
            bonus_value=1.5,
            area=multi_polygon,
        )

    def test_scenic_area_serialization(self):
        """Test serializing a scenic area object."""
        serializer = ScenicAreaSerializer(self.scenic_area)

        self.assertEqual(serializer.data["name"], "Test Area")
        self.assertEqual(serializer.data["area_type"], "national_park")
        self.assertEqual(serializer.data["bonus_value"], 1.5)
        self.assertIsNotNone(serializer.data["area"])

    def test_scenic_area_deserialization(self):
        """Test deserializing scenic area data."""
        # Create a new polygon for the test
        new_polygon = Polygon(
            ((8.0, 44.0), (9.0, 44.0), (9.0, 45.0), (8.0, 45.0), (8.0, 44.0))
        )
        new_multi_polygon = MultiPolygon(new_polygon)

        new_area_data = {
            "name": "New Scenic Area",
            "area_type": "lake_district",
            "bonus_value": 2.0,
            "area": new_multi_polygon,
        }

        serializer = ScenicAreaSerializer(data=new_area_data)
        self.assertTrue(serializer.is_valid())

        area = serializer.save()
        self.assertEqual(area.name, "New Scenic Area")
        self.assertEqual(area.area_type, "lake_district")
        self.assertEqual(area.bonus_value, 2.0)

    def test_read_only_fields(self):
        """Test that id field is read-only."""
        self.assertIn("id", ScenicAreaSerializer.Meta.read_only_fields)
