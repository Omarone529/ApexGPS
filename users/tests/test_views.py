from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from users.models import UserRoles

User = get_user_model()


class CustomUserViewSetTest(APITestCase):
    """Test suite for CustomUserViewSet."""

    def setUp(self):
        """Create test data."""
        self.client = APIClient()

        self.visitor_user = User.objects.create_user(
            username="visitor",
            password="password123",
            role=UserRoles.VISITOR,
            email="visitor@example.com",
        )

        self.subscribed_user = User.objects.create_user(
            username="subscribed",
            password="password123",
            role=UserRoles.SUBSCRIBED,
            email="subscribed@example.com",
        )

        self.admin_user = User.objects.create_user(
            username="admin",
            password="password123",
            role=UserRoles.ADMIN,
            email="admin@example.com",
        )

        self.user_list_url = reverse("customuser-list")
        self.user_detail_url = lambda pk: reverse("customuser-detail", args=[pk])
        self.user_me_url = reverse("customuser-me")

    def test_list_users_as_admin(self):
        """Test if only admins can list all users."""
        # Authenticate as admin
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.get(self.user_list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 3)  # Should see all 3 users

    def test_list_users_as_subscribed(self):
        """Test if subscribed users cannot list all users."""
        self.client.force_authenticate(user=self.subscribed_user)

        response = self.client.get(self.user_list_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_users_as_visitor(self):
        """Test if visitors cannot list users."""
        self.client.force_authenticate(user=self.visitor_user)

        response = self.client.get(self.user_list_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_list_users_unauthenticated(self):
        """Test if unauthenticated users cannot list users."""
        response = self.client.get(self.user_list_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_user_as_admin(self):
        """Test if only admins can create users."""
        self.client.force_authenticate(user=self.admin_user)

        new_user_data = {
            "username": "newuser",
            "password": "newpassword123",
            "email": "new@example.com",
            "role": UserRoles.SUBSCRIBED,
            "first_name": "New",
            "last_name": "User",
        }

        response = self.client.post(self.user_list_url, new_user_data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(User.objects.count(), 4)

    def test_create_user_as_non_admin(self):
        """Test if non-admins cannot create users."""
        self.client.force_authenticate(user=self.subscribed_user)

        new_user_data = {
            "username": "newuser",
            "password": "newpassword123",
            "email": "new@example.com",
        }

        response = self.client.post(self.user_list_url, new_user_data)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(User.objects.count(), 3)  # No new user created

    def test_retrieve_user_as_admin(self):
        """Test if admins can retrieve any user."""
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.get(self.user_detail_url(self.subscribed_user.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["username"], "subscribed")

    def test_retrieve_user_as_self(self):
        """Test if users can retrieve their own data."""
        self.client.force_authenticate(user=self.subscribed_user)

        response = self.client.get(self.user_detail_url(self.subscribed_user.id))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["username"], "subscribed")

    def test_retrieve_user_as_other(self):
        """Test if users cannot retrieve other users' data."""
        self.client.force_authenticate(user=self.subscribed_user)

        response = self.client.get(self.user_detail_url(self.visitor_user.id))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_update_user_as_admin(self):
        """Test if admins can update any user."""
        self.client.force_authenticate(user=self.admin_user)

        update_data = {"first_name": "Updated"}
        response = self.client.patch(
            self.user_detail_url(self.subscribed_user.id), update_data
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.subscribed_user.refresh_from_db()
        self.assertEqual(self.subscribed_user.first_name, "Updated")

    def test_update_user_as_self(self):
        """Test if users can update their own data."""
        self.client.force_authenticate(user=self.subscribed_user)

        update_data = {"first_name": "UpdatedSelf"}
        response = self.client.patch(
            self.user_detail_url(self.subscribed_user.id), update_data
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.subscribed_user.refresh_from_db()
        self.assertEqual(self.subscribed_user.first_name, "UpdatedSelf")

    def test_update_user_as_other(self):
        """Test if users cannot update other users' data."""
        self.client.force_authenticate(user=self.subscribed_user)

        update_data = {"first_name": "ShouldNotWork"}
        response = self.client.patch(
            self.user_detail_url(self.visitor_user.id), update_data
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.visitor_user.refresh_from_db()
        self.assertNotEqual(self.visitor_user.first_name, "ShouldNotWork")

    def test_delete_user_as_admin(self):
        """Test if admins can delete any user."""
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.delete(self.user_detail_url(self.subscribed_user.id))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(User.objects.filter(id=self.subscribed_user.id).exists())

    def test_delete_user_as_self(self):
        """Test if users can delete their own account."""
        self.client.force_authenticate(user=self.subscribed_user)

        response = self.client.delete(self.user_detail_url(self.subscribed_user.id))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(User.objects.filter(id=self.subscribed_user.id).exists())

    def test_delete_user_as_other(self):
        """Test if users cannot delete other users' accounts."""
        self.client.force_authenticate(user=self.subscribed_user)

        response = self.client.delete(self.user_detail_url(self.visitor_user.id))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(User.objects.filter(id=self.visitor_user.id).exists())

    def test_me_endpoint_authenticated(self):
        """Test the /me endpoint for authenticated users."""
        self.client.force_authenticate(user=self.subscribed_user)

        response = self.client.get(self.user_me_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["username"], "subscribed")
        self.assertEqual(response.data["email"], "subscribed@example.com")

    def test_me_endpoint_unauthenticated(self):
        """Test the /me endpoint for unauthenticated users."""
        response = self.client.get(self.user_me_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
