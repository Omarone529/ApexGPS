from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.utils.translation import gettext_lazy as _

from users.models import CustomUser, UserRoles


class CustomUserModelTest(TestCase):
    """Test suite for CustomUser model."""

    def setUp(self):
        """Create tests users for each role."""
        self.visitor_user = CustomUser.objects.create_user(
            username="visitor",
            password="password123",
            role=UserRoles.VISITOR,
            email="visitor@example.com",
        )

        self.subscribed_user = CustomUser.objects.create_user(
            username="subscribed",
            password="password123",
            role=UserRoles.SUBSCRIBED,
            email="subscribed@example.com",
        )

        self.admin_user = CustomUser.objects.create_user(
            username="admin",
            password="password123",
            role=UserRoles.ADMIN,
            email="admin@example.com",
        )

    def test_user_creation(self):
        """Test if users can be created with different roles."""
        self.assertEqual(self.visitor_user.role, UserRoles.VISITOR)
        self.assertEqual(self.subscribed_user.role, UserRoles.SUBSCRIBED)
        self.assertEqual(self.admin_user.role, UserRoles.ADMIN)

    def test_role_properties(self):
        """Test role-based properties."""
        # Visitor role tests
        self.assertTrue(self.visitor_user.is_visitor)
        self.assertFalse(self.visitor_user.is_subscribed)
        self.assertFalse(self.visitor_user.is_administrator)

        # Subscribed role tests
        self.assertFalse(self.subscribed_user.is_visitor)
        self.assertTrue(self.subscribed_user.is_subscribed)
        self.assertFalse(self.subscribed_user.is_administrator)

        # Admin role tests
        self.assertFalse(self.admin_user.is_visitor)
        self.assertFalse(self.admin_user.is_subscribed)
        self.assertTrue(self.admin_user.is_administrator)

    def test_permission_methods(self):
        """Test user permission methods."""

        # Test can_view_public_routes (should be True for all)
        self.assertTrue(self.visitor_user.can_view_public_routes())
        self.assertTrue(self.subscribed_user.can_view_public_routes())
        self.assertTrue(self.admin_user.can_view_public_routes())

        # Test can_create_private_routes
        self.assertFalse(self.visitor_user.can_create_private_routes())
        self.assertTrue(self.subscribed_user.can_create_private_routes())
        self.assertTrue(self.admin_user.can_create_private_routes())

        # Test can_publish_routes
        self.assertFalse(self.visitor_user.can_publish_routes())
        self.assertTrue(self.subscribed_user.can_publish_routes())
        self.assertTrue(self.admin_user.can_publish_routes())

        # Test can_moderate_content
        self.assertFalse(self.visitor_user.can_moderate_content())
        self.assertFalse(self.subscribed_user.can_moderate_content())
        self.assertTrue(self.admin_user.can_moderate_content())

        # Test can_manage_users
        self.assertFalse(self.visitor_user.can_manage_users())
        self.assertFalse(self.subscribed_user.can_manage_users())
        self.assertTrue(self.admin_user.can_manage_users())

    def test_string_representation(self):
        """Test the string representation of users."""
        self.assertIn("visitor", str(self.visitor_user))
        self.assertIn("subscribed", str(self.subscribed_user))
        self.assertIn("admin", str(self.admin_user))
        self.assertIn("(", str(self.visitor_user))
        self.assertIn(")", str(self.visitor_user))

    def test_default_role(self):
        """Test if default role is SUBSCRIBED."""
        new_user = CustomUser.objects.create_user(
            username="default_user",
            password="password123",
            email="default@example.com",
        )
        self.assertEqual(new_user.role, UserRoles.SUBSCRIBED)

    def test_groups_and_permissions_relationships(self):
        """Test if groups and permissions relationships work."""
        group = Group.objects.create(name="Test Group")
        permission = Permission.objects.first()

        self.admin_user.groups.add(group)
        self.admin_user.user_permissions.add(permission)

        self.assertIn(group, self.admin_user.groups.all())
        self.assertIn(permission, self.admin_user.user_permissions.all())

    def test_verbose_names(self):
        """Test verbose names in Meta class."""
        self.assertEqual(CustomUser._meta.verbose_name, _("Utente ApexGPS"))
        self.assertEqual(CustomUser._meta.verbose_name_plural, _("Utenti ApexGPS"))
