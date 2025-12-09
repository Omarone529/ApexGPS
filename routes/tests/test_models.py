from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.gis.geos import Point
from django.test import TestCase

from routes.models import Route

User = get_user_model()


class RouteModelTest(TestCase):
    """Test suite for Route model."""

    def setUp(self):
        """Create mock data."""
        self.user = User.objects.create_user(
            username="testuser", password="password123", email="test@example.com"
        )

        self.route = Route.objects.create(
            name="Test Route",
            owner=self.user,
            visibility="private",
            start_location=Point(9.0, 45.0, srid=4326),
            end_location=Point(10.0, 46.0, srid=4326),
            preference="balanced",
            polyline="test_polyline_string",
            distance_km=150.5,
            estimated_time_min=120,
            total_scenic_score=85.5,
        )

    def test_route_creation(self):
        """Test route creation with all fields."""
        self.assertEqual(self.route.name, "Test Route")
        self.assertEqual(self.route.owner, self.user)
        self.assertEqual(self.route.visibility, "private")
        self.assertEqual(self.route.preference, "balanced")
        self.assertEqual(self.route.distance_km, 150.5)
        self.assertEqual(self.route.estimated_time_min, 120)
        self.assertEqual(self.route.total_scenic_score, 85.5)
        self.assertIsNotNone(self.route.created_at)
        self.assertIsNotNone(self.route.updated_at)

    def test_string_representation(self):
        """Test string representation."""
        self.assertEqual(str(self.route), "Test Route - testuser")

    def test_is_public_method(self):
        """Test is_public method."""
        # Test private route
        self.assertFalse(self.route.is_public())

        # Test public route
        self.route.visibility = "public"
        self.assertTrue(self.route.is_public())

        # Test link-shared route
        self.route.visibility = "link"
        self.assertFalse(self.route.is_public())

    def test_can_view_method(self):
        """Test can_view method with different users."""
        # Create different users
        anonymous_user = AnonymousUser()
        other_user = User.objects.create_user(
            username="otheruser", password="password123"
        )
        staff_user = User.objects.create_user(
            username="staff", password="password123", is_staff=True
        )

        # Test private route
        self.route.visibility = "private"
        self.assertTrue(self.route.can_view(self.user))
        self.assertTrue(self.route.can_view(staff_user))
        self.assertFalse(self.route.can_view(other_user))
        self.assertFalse(self.route.can_view(anonymous_user))

        # Test public route
        self.route.visibility = "public"
        self.assertTrue(self.route.can_view(self.user))
        self.assertTrue(self.route.can_view(other_user))
        self.assertTrue(self.route.can_view(anonymous_user))

        # Test link-shared route
        self.route.visibility = "link"
        self.assertTrue(self.route.can_view(self.user))
        self.assertTrue(self.route.can_view(other_user))
        self.assertFalse(self.route.can_view(anonymous_user))

    def test_ordering(self):
        """Test default ordering by created_at descending."""
        # Create additional routes
        route2 = Route.objects.create(
            name="Route 2",
            owner=self.user,
            start_location=Point(9.0, 45.0, srid=4326),
            end_location=Point(10.0, 46.0, srid=4326),
            polyline="test",
            distance_km=100,
            estimated_time_min=60,
            preference="fast",
        )

        route3 = Route.objects.create(
            name="Route 3",
            owner=self.user,
            start_location=Point(9.0, 45.0, srid=4326),
            end_location=Point(10.0, 46.0, srid=4326),
            polyline="test",
            distance_km=100,
            estimated_time_min=60,
            preference="fast",
        )

        routes = list(Route.objects.all())
        # Most recent first
        self.assertEqual(routes[0], route3)
        self.assertEqual(routes[1], route2)
        self.assertEqual(routes[2], self.route)

    def test_indexes(self):
        """Test that indexes are defined."""
        indexes = [idx.fields for idx in Route._meta.indexes]
        expected_indexes = [["owner", "visibility"], ["visibility"], ["created_at"]]

        for expected in expected_indexes:
            self.assertIn(expected, indexes)

    def test_verbose_names(self):
        """Test verbose names."""
        self.assertEqual(Route._meta.verbose_name, "Percorso")
        self.assertEqual(Route._meta.verbose_name_plural, "Percorsi")
