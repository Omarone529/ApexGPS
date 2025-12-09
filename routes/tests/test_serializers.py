from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.test import TestCase
from rest_framework.test import APIRequestFactory

from routes.models import Route
from routes.serializers import RouteGeoSerializer, RouteSerializer

User = get_user_model()


class RouteSerializerTest(TestCase):
    """Test suite for RouteSerializer."""

    def setUp(self):
        """Create test data."""
        self.factory = APIRequestFactory()
        self.user = User.objects.create_user(
            username="testuser", password="password123", email="test@example.com"
        )

        self.route_data = {
            "name": "Test Route",
            "visibility": "private",
            "start_location": Point(9.0, 45.0, srid=4326),
            "end_location": Point(10.0, 46.0, srid=4326),
            "preference": "balanced",
            "polyline": "test_polyline_string",
            "distance_km": 150.5,
            "estimated_time_min": 120,
            "total_scenic_score": 85.5,
        }

        self.route = Route.objects.create(owner=self.user, **self.route_data)

    def test_route_serialization(self):
        """Test serializing a route object."""
        serializer = RouteSerializer(self.route)

        self.assertEqual(serializer.data["name"], "Test Route")
        self.assertEqual(serializer.data["visibility"], "private")
        self.assertEqual(serializer.data["preference"], "balanced")
        self.assertEqual(serializer.data["distance_km"], 150.5)
        self.assertEqual(serializer.data["estimated_time_min"], 120)
        self.assertEqual(serializer.data["total_scenic_score"], 85.5)
        self.assertEqual(serializer.data["owner_username"], "testuser")

        # Check read-only fields
        self.assertIn("created_at", serializer.data)
        self.assertIn("updated_at", serializer.data)
        self.assertIn("owner", serializer.data)

    def test_route_deserialization(self):
        """Test deserializing route data."""
        new_route_data = {
            "name": "New Route",
            "visibility": "public",
            "start_location": Point(9.1, 45.1, srid=4326),
            "end_location": Point(10.1, 46.1, srid=4326),
            "preference": "fast",
            "polyline": "new_polyline",
            "distance_km": 200.0,
            "estimated_time_min": 150,
            "total_scenic_score": 70.0,
        }

        # Create a mock request with user
        request = self.factory.post("/")
        request.user = self.user

        serializer = RouteSerializer(data=new_route_data, context={"request": request})

        self.assertTrue(serializer.is_valid())

        route = serializer.save()
        self.assertEqual(route.name, "New Route")
        # Owner should be set automatically
        self.assertEqual(route.owner, self.user)
        self.assertEqual(route.visibility, "public")

    def test_owner_field_read_only(self):
        """Test that owner field is read-only."""
        other_user = User.objects.create_user(
            username="otheruser", password="password123"
        )

        # Try to create route with different owner (should not be allowed)
        route_data = {
            "name": "Other Route",
            "owner": other_user.id,
            "visibility": "private",
            "start_location": Point(9.0, 45.0, srid=4326),
            "end_location": Point(10.0, 46.0, srid=4326),
            "preference": "fast",
            "polyline": "test",
            "distance_km": 100,
            "estimated_time_min": 60,
        }

        request = self.factory.post("/")
        request.user = self.user

        serializer = RouteSerializer(data=route_data, context={"request": request})

        self.assertTrue(serializer.is_valid())
        route = serializer.save()

        # Owner should be set from request.user, not from input data
        self.assertEqual(route.owner, self.user)
        self.assertNotEqual(route.owner, other_user)


class RouteGeoSerializerTest(TestCase):
    """Test suite for RouteGeoSerializer (GeoJSON)."""

    def setUp(self):
        """Create test data."""
        self.user = User.objects.create_user(
            username="testuser", password="password123"
        )

        self.route = Route.objects.create(
            name="Test Route",
            owner=self.user,
            start_location=Point(9.0, 45.0, srid=4326),
            end_location=Point(10.0, 46.0, srid=4326),
            polyline="test",
            distance_km=100,
            estimated_time_min=60,
            visibility="public",
            preference="fast",
        )

    def test_geojson_serialization(self):
        """Test serializing route to GeoJSON format."""
        serializer = RouteGeoSerializer(self.route)
        data = serializer.data

        # Check GeoJSON structure
        self.assertIn("type", data)
        self.assertEqual(data["type"], "Feature")

        # ID should be at the top level (not in properties)
        self.assertIn("id", data)
        self.assertEqual(data["id"], self.route.id)

        self.assertIn("geometry", data)
        self.assertIn("properties", data)

        # Geometry is a WKT (Well-Known-Text) string
        geometry = data["geometry"]
        self.assertEqual(geometry, "SRID=4326;POINT (9 45)")

        # Check properties
        properties = data["properties"]
        self.assertEqual(properties["name"], "Test Route")
        self.assertEqual(properties["visibility"], "public")
        self.assertEqual(properties["distance_km"], 100)

    def test_geo_field_configuration(self):
        """Test that geo_field is correctly set."""
        self.assertEqual(RouteGeoSerializer.Meta.geo_field, "start_location")

        # Check fields
        self.assertIn("id", RouteGeoSerializer.Meta.fields)
        self.assertIn("name", RouteGeoSerializer.Meta.fields)
        self.assertIn("owner", RouteGeoSerializer.Meta.fields)
        self.assertIn("visibility", RouteGeoSerializer.Meta.fields)
        self.assertIn("distance_km", RouteGeoSerializer.Meta.fields)
