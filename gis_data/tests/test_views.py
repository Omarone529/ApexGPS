from django.contrib.auth import get_user_model
from django.contrib.gis.geos import MultiPolygon, Point, Polygon
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from gis_data.models import PointOfInterest, ScenicArea
from users.models import UserRoles

User = get_user_model()


class PointOfInterestViewSetTest(APITestCase):
    """Test suite for PointOfInterestViewSet."""

    def setUp(self):
        """Create test data."""
        self.client = APIClient()

        # Create users with different roles
        self.visitor_user = User.objects.create_user(
            username="visitor", password="password123", role=UserRoles.VISITOR
        )

        self.subscribed_user = User.objects.create_user(
            username="subscribed", password="password123", role=UserRoles.SUBSCRIBED
        )

        self.admin_user = User.objects.create_user(
            username="admin", password="password123", role=UserRoles.ADMIN
        )

        # Create a sample POI
        self.poi = PointOfInterest.objects.create(
            name="Test POI",
            category="monument",
            location=Point(9.0, 45.0, srid=4326),
            description="A test point of interest",
        )

        # URLs
        self.poi_list_url = reverse("pointofinterest-list")
        self.poi_detail_url = lambda pk: reverse("pointofinterest-detail", args=[pk])

    def test_list_pois_unauthenticated(self):
        """Test that anyone can list POIs."""
        response = self.client.get(self.poi_list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_list_pois_as_subscribed(self):
        """Test that subscribed users can list POIs."""
        self.client.force_authenticate(user=self.subscribed_user)

        response = self.client.get(self.poi_list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_retrieve_poi_unauthenticated(self):
        """Test that anyone can retrieve a POI."""
        response = self.client.get(self.poi_detail_url(self.poi.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Test POI")

    def test_create_poi_as_subscribed(self):
        """Test that subscribed users can create POIs."""
        self.client.force_authenticate(user=self.subscribed_user)

        new_poi_data = {
            "name": "New POI",
            "category": "lake",
            "latitude": 46.0,
            "longitude": 10.0,
            "description": "A beautiful lake",
        }

        response = self.client.post(self.poi_list_url, new_poi_data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(PointOfInterest.objects.count(), 2)

    def test_create_poi_as_visitor(self):
        """Test that visitors cannot create POIs."""
        self.client.force_authenticate(user=self.visitor_user)

        new_poi_data = {
            "name": "New POI",
            "category": "lake",
            "latitude": 46.0,
            "longitude": 10.0,
        }

        response = self.client.post(self.poi_list_url, new_poi_data)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(PointOfInterest.objects.count(), 1)  # No new POI created

    def test_create_poi_unauthenticated(self):
        """Test that unauthenticated users cannot create POIs."""
        new_poi_data = {
            "name": "New POI",
            "category": "lake",
            "latitude": 46.0,
            "longitude": 10.0,
        }

        response = self.client.post(self.poi_list_url, new_poi_data)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(PointOfInterest.objects.count(), 1)

    def test_update_poi_as_admin(self):
        """Test that only admins can update POIs."""
        self.client.force_authenticate(user=self.admin_user)

        # Provide complete data for update to avoid validation errors
        update_data = {
            "name": "Updated POI",
            "category": "monument",
            "description": "A test point of interest",
        }

        response = self.client.patch(self.poi_detail_url(self.poi.id), update_data)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.poi.refresh_from_db()
        self.assertEqual(self.poi.name, "Updated POI")

    def test_update_poi_as_subscribed(self):
        """Test that subscribed users cannot update POIs."""
        self.client.force_authenticate(user=self.subscribed_user)

        update_data = {"name": "Should Not Update"}
        response = self.client.patch(self.poi_detail_url(self.poi.id), update_data)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.poi.refresh_from_db()
        self.assertNotEqual(self.poi.name, "Should Not Update")

    def test_delete_poi_as_admin(self):
        """Test that only admins can delete POIs."""
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.delete(self.poi_detail_url(self.poi.id))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(PointOfInterest.objects.count(), 0)

    def test_delete_poi_as_subscribed(self):
        """Test that subscribed users cannot delete POIs."""
        self.client.force_authenticate(user=self.subscribed_user)

        response = self.client.delete(self.poi_detail_url(self.poi.id))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(PointOfInterest.objects.count(), 1)  # POI still exists


class ScenicAreaViewSetTest(APITestCase):
    """Test suite for ScenicAreaViewSet."""

    def setUp(self):
        """Create test data."""
        self.client = APIClient()

        # Create users
        self.subscribed_user = User.objects.create_user(
            username="subscribed", password="password123", role=UserRoles.SUBSCRIBED
        )

        self.admin_user = User.objects.create_user(
            username="admin", password="password123", role=UserRoles.ADMIN
        )

        # Create a sample scenic area
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
        self.scenic_area_list_url = reverse("scenicarea-list")
        self.scenic_area_detail_url = lambda pk: reverse("scenicarea-detail", args=[pk])

    def test_list_scenic_areas_unauthenticated(self):
        """Test that anyone can list scenic areas."""
        response = self.client.get(self.scenic_area_list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_retrieve_scenic_area_unauthenticated(self):
        """Test that anyone can retrieve a scenic area."""
        response = self.client.get(self.scenic_area_detail_url(self.scenic_area.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["name"], "Test Scenic Area")

    def test_create_scenic_area_as_subscribed(self):
        """Test that subscribed users can create scenic areas."""
        self.client.force_authenticate(user=self.subscribed_user)

        # Create a simple polygon
        new_polygon = Polygon(
            ((8.0, 44.0), (9.0, 44.0), (9.0, 45.0), (8.0, 45.0), (8.0, 44.0))
        )
        new_multi_polygon = MultiPolygon(new_polygon)

        new_area_data = {
            "name": "New Scenic Area",
            "area_type": "lake_district",
            "bonus_value": 2.0,
            "area": new_multi_polygon.wkt,
        }

        response = self.client.post(self.scenic_area_list_url, new_area_data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(ScenicArea.objects.count(), 2)

    def test_update_scenic_area_as_admin(self):
        """Test that only admins can update scenic areas."""
        self.client.force_authenticate(user=self.admin_user)

        update_data = {"name": "Updated Scenic Area"}
        response = self.client.patch(
            self.scenic_area_detail_url(self.scenic_area.id), update_data
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.scenic_area.refresh_from_db()
        self.assertEqual(self.scenic_area.name, "Updated Scenic Area")

    def test_update_scenic_area_as_subscribed(self):
        """Test that subscribed users cannot update scenic areas."""
        self.client.force_authenticate(user=self.subscribed_user)

        update_data = {"name": "Should Not Update"}
        response = self.client.patch(
            self.scenic_area_detail_url(self.scenic_area.id), update_data
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.scenic_area.refresh_from_db()
        self.assertNotEqual(self.scenic_area.name, "Should Not Update")

    def test_delete_scenic_area_as_admin(self):
        """Test that only admins can delete scenic areas."""
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.delete(self.scenic_area_detail_url(self.scenic_area.id))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(ScenicArea.objects.count(), 0)

    def test_delete_scenic_area_as_subscribed(self):
        """Test that subscribed users cannot delete scenic areas."""
        self.client.force_authenticate(user=self.subscribed_user)

        response = self.client.delete(self.scenic_area_detail_url(self.scenic_area.id))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(ScenicArea.objects.count(), 1)  # Still exists
