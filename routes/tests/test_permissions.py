from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase

from routes.models import Route
from routes.permissions import IsAdminOrReadOnly, IsOwnerOrReadOnly

User = get_user_model()


class RoutesPermissionsTest(TestCase):
    """Test base setup for routes permissions."""

    def setUp(self):
        """Create test users and route."""
        self.factory = RequestFactory()

        self.owner = User.objects.create_user(username="owner", password="pass")
        self.other = User.objects.create_user(username="other", password="pass")
        self.admin = User.objects.create_user(
            username="admin", password="pass", is_staff=True
        )

        self.route = Route.objects.create(
            name="Test Route",
            owner=self.owner,
            start_location="POINT(0 0)",
            end_location="POINT(1 1)",
            polyline="test",
            distance_km=100,
            estimated_time_min=60,
            visibility="private",
            preference="fast",
        )


class TestIsOwnerOrReadOnly(RoutesPermissionsTest):
    """Test IsOwnerOrReadOnly permission."""

    def test_owner_can_write(self):
        """Owner should have write access."""
        perm = IsOwnerOrReadOnly()
        request = self.factory.put("/")
        request.user = self.owner

        self.assertTrue(perm.has_object_permission(request, None, self.route))

    def test_others_cannot_write(self):
        """Others should not have write access."""
        perm = IsOwnerOrReadOnly()
        request = self.factory.put("/")
        request.user = self.other

        self.assertFalse(perm.has_object_permission(request, None, self.route))

    def test_public_route_readable_by_all(self):
        """Public routes should be readable by everyone."""
        perm = IsOwnerOrReadOnly()
        self.route.visibility = "public"
        self.route.save()

        # Anonymous user
        request = self.factory.get("/")
        request.user = AnonymousUser()
        self.assertTrue(perm.has_object_permission(request, None, self.route))

        # Other user
        request.user = self.other
        self.assertTrue(perm.has_object_permission(request, None, self.route))

    def test_private_route_only_readable_by_owner(self):
        """Private routes should only be readable by owner."""
        perm = IsOwnerOrReadOnly()

        # Owner can read
        request = self.factory.get("/")
        request.user = self.owner
        self.assertTrue(perm.has_object_permission(request, None, self.route))

        # Others cannot read
        request.user = self.other
        self.assertFalse(perm.has_object_permission(request, None, self.route))


class TestIsAdminOrReadOnly(TestCase):
    """Test IsAdminOrReadOnly permission."""

    def setUp(self):
        self.factory = RequestFactory()
        self.admin = User.objects.create_user(
            username="admin", password="pass", is_staff=True
        )
        self.user = User.objects.create_user(username="user", password="pass")

    def test_safe_methods_allowed_for_all(self):
        """GET, HEAD, OPTIONS should be allowed for everyone."""
        perm = IsAdminOrReadOnly()
        # Anonymous user
        request = self.factory.get("/")
        request.user = AnonymousUser()
        self.assertTrue(perm.has_permission(request, None))

        # Regular user
        request.user = self.user
        self.assertTrue(perm.has_permission(request, None))

        # Admin
        request.user = self.admin
        self.assertTrue(perm.has_permission(request, None))

    def test_write_methods_only_for_admin(self):
        """POST, PUT, PATCH, DELETE should be admin-only."""
        perm = IsAdminOrReadOnly()

        # Regular user cannot write
        request = self.factory.post("/")
        request.user = self.user
        self.assertFalse(perm.has_permission(request, None))

        # Admin can write
        request.user = self.admin
        self.assertTrue(perm.has_permission(request, None))
