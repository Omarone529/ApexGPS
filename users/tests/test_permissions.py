from unittest.mock import Mock

from django.test import TestCase
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from routes.permissions import IsAdminOrReadOnly, IsOwnerOrReadOnly
from users.permissions import (
    AllowAnyUser,
    CanCreatePrivateItineraries,
    CanPublishItineraries,
    IsAdminUser,
    IsAuthenticatedUser,
    IsRegisteredUser,
    IsSubscribedUser,
)
from users.permissions import (
    IsOwnerOrReadOnly as UsersIsOwnerOrReadOnly,
)


class UsersPermissionsTest(TestCase):
    """Test suite for users app permissions."""

    def create_mock_user(self, role, is_authenticated=True):
        """Create a mock user with given role."""
        user = Mock()
        user.role = role
        user.is_authenticated = is_authenticated
        return user

    def create_request(self, user=None):
        """Create a mock request with user."""
        factory = APIRequestFactory()
        request = factory.get("/")
        drf_request = Request(request)
        drf_request.user = user
        return drf_request

    def test_allow_any_user(self):
        """Test AllowAnyUser permission."""
        permission = AllowAnyUser()

        # Request without user
        request = self.create_request(user=None)
        self.assertTrue(permission.has_permission(request, None))

        # Request with user
        user = self.create_mock_user("SUBSCRIBED")
        request = self.create_request(user=user)
        self.assertTrue(permission.has_permission(request, None))

    def test_is_authenticated_user(self):
        """Test IsAuthenticatedUser permission."""
        permission = IsAuthenticatedUser()

        # Unauthenticated request
        request = self.create_request(user=None)
        self.assertFalse(permission.has_permission(request, None))

        # Authenticated request
        user = self.create_mock_user("SUBSCRIBED")
        request = self.create_request(user=user)
        self.assertTrue(permission.has_permission(request, None))

    def test_is_subscribed_user(self):
        """Test IsSubscribedUser permission."""
        permission = IsSubscribedUser()

        # Visitor user
        user = self.create_mock_user("VISITOR")
        request = self.create_request(user=user)
        self.assertFalse(permission.has_permission(request, None))

        # Subscribed user
        user = self.create_mock_user("SUBSCRIBED")
        request = self.create_request(user=user)
        self.assertTrue(permission.has_permission(request, None))

        # Admin user
        user = self.create_mock_user("ADMIN")
        request = self.create_request(user=user)
        self.assertFalse(permission.has_permission(request, None))

    def test_is_admin_user(self):
        """Test IsAdminUser permission."""
        permission = IsAdminUser()

        # Visitor user
        user = self.create_mock_user("VISITOR")
        request = self.create_request(user=user)
        self.assertFalse(permission.has_permission(request, None))

        # Subscribed user
        user = self.create_mock_user("SUBSCRIBED")
        request = self.create_request(user=user)
        self.assertFalse(permission.has_permission(request, None))

        # Admin user
        user = self.create_mock_user("ADMIN")
        request = self.create_request(user=user)
        self.assertTrue(permission.has_permission(request, None))

    def test_is_registered_user(self):
        """Test IsRegisteredUser permission."""
        permission = IsRegisteredUser()

        # Visitor user
        user = self.create_mock_user("VISITOR")
        request = self.create_request(user=user)
        self.assertFalse(permission.has_permission(request, None))

        # Subscribed user
        user = self.create_mock_user("SUBSCRIBED")
        request = self.create_request(user=user)
        self.assertTrue(permission.has_permission(request, None))

        # Admin user
        user = self.create_mock_user("ADMIN")
        request = self.create_request(user=user)
        self.assertTrue(permission.has_permission(request, None))

    def test_is_owner_or_read_only(self):
        """Test IsOwnerOrReadOnly permission (users app)."""
        permission = UsersIsOwnerOrReadOnly()

        # Create mock users
        owner_user = self.create_mock_user("SUBSCRIBED")
        other_user = self.create_mock_user("SUBSCRIBED")

        # Create test object owned by owner_user
        test_object = Mock()
        test_object.user = owner_user
        test_object.owner = owner_user

        # Test with owner user
        request = self.create_request(user=owner_user)
        self.assertTrue(permission.has_object_permission(request, None, test_object))

        # Test with owner user
        factory = APIRequestFactory()
        put_request = factory.put("/")
        drf_put_request = Request(put_request)
        drf_put_request.user = owner_user
        self.assertTrue(
            permission.has_object_permission(drf_put_request, None, test_object)
        )

        # Test with non-owner user
        request = self.create_request(user=other_user)
        self.assertTrue(permission.has_object_permission(request, None, test_object))

        # Test with non-owner user
        put_request = factory.put("/")
        drf_put_request = Request(put_request)
        drf_put_request.user = other_user
        self.assertFalse(
            permission.has_object_permission(drf_put_request, None, test_object)
        )

    def test_can_create_private_itineraries(self):
        """Test CanCreatePrivateItineraries permission."""
        permission = CanCreatePrivateItineraries()

        # Visitor user
        user = self.create_mock_user("VISITOR")
        request = self.create_request(user=user)
        self.assertFalse(permission.has_permission(request, None))

        # Subscribed user
        user = self.create_mock_user("SUBSCRIBED")
        request = self.create_request(user=user)
        self.assertTrue(permission.has_permission(request, None))

        # Admin user
        user = self.create_mock_user("ADMIN")
        request = self.create_request(user=user)
        self.assertTrue(permission.has_permission(request, None))

    def test_can_publish_itineraries(self):
        """Test CanPublishItineraries permission."""
        permission = CanPublishItineraries()

        # Visitor user
        user = self.create_mock_user("VISITOR")
        request = self.create_request(user=user)
        self.assertFalse(permission.has_permission(request, None))

        # Subscribed user
        user = self.create_mock_user("SUBSCRIBED")
        request = self.create_request(user=user)
        self.assertTrue(permission.has_permission(request, None))

        # Admin user
        user = self.create_mock_user("ADMIN")
        request = self.create_request(user=user)
        self.assertTrue(permission.has_permission(request, None))


class RoutesPermissionsTest(TestCase):
    """Test suite for routes app permissions."""

    def create_mock_user(self, role, is_authenticated=True, is_staff=False, pk=1):
        """Create a mock user with given attributes."""
        user = Mock()
        user.role = role
        user.is_authenticated = is_authenticated
        user.is_staff = is_staff
        user.pk = pk
        return user

    def create_request(self, user=None, method="GET"):
        """Create a mock request with user."""
        factory = APIRequestFactory()
        request = factory.generic(method, "/")
        drf_request = Request(request)
        drf_request.user = user
        return drf_request

    def test_is_owner_or_read_only(self):
        """Test IsOwnerOrReadOnly permission (routes app)."""
        permission = IsOwnerOrReadOnly()

        # Create mock users
        owner_user = self.create_mock_user("SUBSCRIBED", pk=1)
        other_user = self.create_mock_user("SUBSCRIBED", pk=2)
        admin_user = self.create_mock_user("ADMIN", is_staff=True, pk=3)

        # Create mock route owned by owner_user
        route = Mock()
        route.owner = owner_user
        route.visibility = "private"
        # Mock the can_view method
        route.can_view = Mock(
            side_effect=lambda user: (
                user == owner_user
                or (hasattr(user, "is_staff") and user.is_staff)
                or route.visibility == "public"
            )
        )

        # Test with owner user
        request = self.create_request(user=owner_user, method="GET")
        self.assertTrue(permission.has_object_permission(request, None, route))

        # Test with other user (private route)
        request = self.create_request(user=other_user, method="GET")
        self.assertFalse(permission.has_object_permission(request, None, route))

        # Test with admin user (private route)
        request = self.create_request(user=admin_user, method="GET")
        self.assertTrue(permission.has_object_permission(request, None, route))

        # Test public route
        route.visibility = "public"
        request = self.create_request(user=other_user, method="GET")
        self.assertTrue(permission.has_object_permission(request, None, route))

    def test_is_admin_or_read_only(self):
        """Test IsAdminOrReadOnly permission."""
        permission = IsAdminOrReadOnly()

        # Unauthenticated user - safe methods only
        request = self.create_request(user=None, method="GET")
        self.assertTrue(permission.has_permission(request, None))

        request = self.create_request(user=None, method="POST")
        self.assertFalse(permission.has_permission(request, None))

        # Regular user - safe methods only
        regular_user = self.create_mock_user("SUBSCRIBED")
        request = self.create_request(user=regular_user, method="GET")
        self.assertTrue(permission.has_permission(request, None))

        request = self.create_request(user=regular_user, method="POST")
        self.assertFalse(permission.has_permission(request, None))

        # Admin user - all methods
        admin_user = self.create_mock_user("ADMIN", is_staff=True)
        request = self.create_request(user=admin_user, method="GET")
        self.assertTrue(permission.has_permission(request, None))

        request = self.create_request(user=admin_user, method="POST")
        self.assertTrue(permission.has_permission(request, None))
