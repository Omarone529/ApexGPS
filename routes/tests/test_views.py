from django.contrib.auth import get_user_model
from django.contrib.gis.geos import Point
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from routes.models import Route

User = get_user_model()


class RouteViewSetTest(APITestCase):
    """Test suite for RouteViewSet."""

    def setUp(self):
        """Create test data."""
        self.client = APIClient()

        # Create users
        self.user1 = User.objects.create_user(
            username="user1", password="password123", email="user1@example.com"
        )

        self.user2 = User.objects.create_user(
            username="user2", password="password123", email="user2@example.com"
        )

        self.admin_user = User.objects.create_user(
            username="admin",
            password="password123",
            email="admin@example.com",
            is_staff=True,
        )

        # Create routes with different visibilities
        self.public_route = Route.objects.create(
            name="Public Route",
            owner=self.user1,
            visibility="public",
            start_location=Point(9.0, 45.0, srid=4326),
            end_location=Point(10.0, 46.0, srid=4326),
            polyline="public_polyline",
            distance_km=100,
            estimated_time_min=60,
            preference="fast",
        )

        self.private_route = Route.objects.create(
            name="Private Route",
            owner=self.user1,
            visibility="private",
            start_location=Point(9.1, 45.1, srid=4326),
            end_location=Point(10.1, 46.1, srid=4326),
            polyline="private_polyline",
            distance_km=120,
            estimated_time_min=70,
            preference="balanced",
        )

        self.link_route = Route.objects.create(
            name="Link Route",
            owner=self.user1,
            visibility="link",
            start_location=Point(9.2, 45.2, srid=4326),
            end_location=Point(10.2, 46.2, srid=4326),
            polyline="link_polyline",
            distance_km=140,
            estimated_time_min=80,
            preference="most_winding",
        )

        # Create a route owned by user2
        self.user2_route = Route.objects.create(
            name="User2 Route",
            owner=self.user2,
            visibility="private",
            start_location=Point(8.0, 44.0, srid=4326),
            end_location=Point(9.0, 45.0, srid=4326),
            polyline="user2_polyline",
            distance_km=90,
            estimated_time_min=50,
            preference="fast",
        )

        # URLs
        self.route_list_url = reverse("route-list")
        self.route_detail_url = lambda pk: reverse("route-detail", args=[pk])
        self.my_routes_url = reverse("route-my-routes")
        self.public_routes_url = reverse("route-public")
        self.geojson_url = reverse("route-geojson")

    def test_list_routes_unauthenticated(self):
        """Test that unauthenticated users can only see public routes."""
        response = self.client.get(self.route_list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

        route_names = [route["name"] for route in response.data]
        self.assertIn("Public Route", route_names)
        self.assertNotIn("Private Route", route_names)
        self.assertNotIn("Link Route", route_names)
        self.assertNotIn("User2 Route", route_names)

    def test_list_routes_as_owner(self):
        """Test that owners can see all their routes plus public routes."""
        self.client.force_authenticate(user=self.user1)
        response = self.client.get(self.route_list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 3)

        route_names = [route["name"] for route in response.data]
        self.assertIn("Public Route", route_names)
        self.assertIn("Private Route", route_names)
        self.assertIn("Link Route", route_names)
        self.assertNotIn("User2 Route", route_names)

    def test_list_routes_as_admin(self):
        """Test that admins can see all routes."""
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.get(self.route_list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 4)

    def test_retrieve_public_route_unauthenticated(self):
        """Test that unauthenticated users can retrieve public routes."""
        response = self.client.get(self.route_detail_url(self.public_route.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Public Route")

    def test_retrieve_private_route_unauthenticated(self):
        """Test that unauthenticated users cannot retrieve private routes."""
        response = self.client.get(self.route_detail_url(self.private_route.id))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_retrieve_private_route_as_owner(self):
        """Test that owners can retrieve their private routes."""
        self.client.force_authenticate(user=self.user1)

        response = self.client.get(self.route_detail_url(self.private_route.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Private Route")

    def test_retrieve_private_route_as_other_user(self):
        """Test that other users cannot retrieve private routes they don't own."""
        self.client.force_authenticate(user=self.user2)

        response = self.client.get(self.route_detail_url(self.private_route.id))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_route_authenticated(self):
        """Test that authenticated users can create routes."""
        self.client.force_authenticate(user=self.user1)

        new_route_data = {
            "name": "New Route",
            "visibility": "private",
            "start_location": "POINT(9.3 45.3)",
            "end_location": "POINT(10.3 46.3)",
            "preference": "balanced",
            "polyline": "new_polyline",
            "distance_km": 150.5,
            "estimated_time_min": 90,
            "total_scenic_score": 85.0,
        }

        response = self.client.post(self.route_list_url, new_route_data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Route.objects.count(), 5)

        # Owner should be set automatically
        self.assertEqual(response.data["owner_username"], "user1")

    def test_create_route_unauthenticated(self):
        """Test that unauthenticated users cannot create routes."""
        new_route_data = {
            "name": "New Route",
            "visibility": "public",
            "start_location": "POINT(9.3 45.3)",
            "end_location": "POINT(10.3 46.3)",
            "polyline": "new_polyline",
            "distance_km": 150.5,
            "estimated_time_min": 90,
            "preference": "fast",
        }

        response = self.client.post(self.route_list_url, new_route_data)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(Route.objects.count(), 4)

    def test_update_route_as_owner(self):
        """Test that owners can update their routes."""
        self.client.force_authenticate(user=self.user1)

        update_data = {"name": "Updated Route Name"}
        response = self.client.patch(
            self.route_detail_url(self.private_route.id), update_data
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.private_route.refresh_from_db()
        self.assertEqual(self.private_route.name, "Updated Route Name")

    def test_update_route_as_other_user(self):
        """Test that other users cannot update routes they don't own."""
        self.client.force_authenticate(user=self.user2)

        update_data = {"name": "Should Not Update"}
        response = self.client.patch(
            self.route_detail_url(self.private_route.id), update_data
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.private_route.refresh_from_db()
        self.assertNotEqual(self.private_route.name, "Should Not Update")

    def test_delete_route_as_owner(self):
        """Test that owners can delete their routes."""
        self.client.force_authenticate(user=self.user1)

        response = self.client.delete(self.route_detail_url(self.private_route.id))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Route.objects.filter(id=self.private_route.id).exists())

    def test_delete_route_as_other_user(self):
        """Test that other users cannot delete routes they don't own."""
        self.client.force_authenticate(user=self.user2)

        response = self.client.delete(self.route_detail_url(self.private_route.id))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(Route.objects.filter(id=self.private_route.id).exists())

    def test_my_routes_endpoint(self):
        """Test the /my-routes endpoint."""
        self.client.force_authenticate(user=self.user1)

        response = self.client.get(self.my_routes_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 3)  # user1 has 3 routes

        route_names = [route["name"] for route in response.data]
        self.assertIn("Public Route", route_names)
        self.assertIn("Private Route", route_names)
        self.assertIn("Link Route", route_names)

    def test_public_routes_endpoint(self):
        """Test the /public endpoint."""
        # Make another route public for testing
        self.user2_route.visibility = "public"
        self.user2_route.save()

        response = self.client.get(self.public_routes_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)  # Two public routes

        route_names = [route["name"] for route in response.data]
        self.assertIn("Public Route", route_names)
        self.assertIn("User2 Route", route_names)
        self.assertNotIn("Private Route", route_names)

    def test_geojson_endpoint(self):
        """Test the /geojson endpoint."""
        response = self.client.get(self.geojson_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Should return GeoJSON format
        self.assertIn("features", response.data)

        # Only public routes should be included for unauthenticated users
        features = response.data["features"]
        self.assertEqual(len(features), 1)

    def test_toggle_visibility_as_owner(self):
        """Test that owners can toggle route visibility."""
        self.client.force_authenticate(user=self.user1)

        toggle_url = reverse("route-toggle-visibility", args=[self.private_route.id])

        # Toggle from private to public
        response = self.client.post(toggle_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.private_route.refresh_from_db()
        self.assertEqual(self.private_route.visibility, "public")

        # Toggle back to private
        response = self.client.post(toggle_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.private_route.refresh_from_db()
        self.assertEqual(self.private_route.visibility, "private")

    def test_toggle_visibility_as_other_user(self):
        """Test that other users cannot toggle visibility."""
        self.client.force_authenticate(user=self.user2)

        toggle_url = reverse("route-toggle-visibility", args=[self.private_route.id])
        response = self.client.post(toggle_url)

        # get_object() raises 404 when route not in get_queryset() (more secure)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # Visibility should remain unchanged
        original_visibility = self.private_route.visibility
        self.private_route.refresh_from_db()
        self.assertEqual(self.private_route.visibility, original_visibility)

    def test_filter_routes_by_visibility(self):
        """Test filtering routes by visibility."""
        self.client.force_authenticate(user=self.user1)

        # Filter for public routes only
        response = self.client.get(self.route_list_url, {"visibility": "public"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["name"], "Public Route")

        # Filter for private routes only
        response = self.client.get(self.route_list_url, {"visibility": "private"})

        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["name"], "Private Route")

    def test_search_routes(self):
        """Test searching routes by name."""
        self.client.force_authenticate(user=self.user1)

        response = self.client.get(self.route_list_url, {"search": "Public"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["name"], "Public Route")

    def test_order_routes(self):
        """Test ordering routes."""
        self.client.force_authenticate(user=self.user1)

        response = self.client.get(self.route_list_url, {"ordering": "distance_km"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        distances = [route["distance_km"] for route in response.data]
        self.assertEqual(distances, sorted(distances))
        response = self.client.get(self.route_list_url, {"ordering": "-distance_km"})

        distances = [route["distance_km"] for route in response.data]
        self.assertEqual(distances, sorted(distances, reverse=True))
