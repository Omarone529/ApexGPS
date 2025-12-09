from django.test import TestCase
from rest_framework.test import APIRequestFactory

from users.models import CustomUser, UserRoles
from users.serializer import CustomUserSerializer


class CustomUserSerializerTest(TestCase):
    """Test suite for CustomUserSerializer."""

    def setUp(self):
        """Create test data."""
        self.factory = APIRequestFactory()
        self.user_data = {
            "username": "testuser",
            "email": "test@example.com",
            "password": "password123",
            "role": UserRoles.SUBSCRIBED,
            "first_name": "Test",
            "last_name": "User",
        }

        self.user = CustomUser.objects.create_user(**self.user_data)

    def test_user_serialization(self):
        """Test serializing a user object."""
        serializer = CustomUserSerializer(self.user)
        data = serializer.data

        self.assertEqual(data["username"], "testuser")
        self.assertEqual(data["email"], "test@example.com")
        self.assertEqual(data["role"], UserRoles.SUBSCRIBED)
        self.assertEqual(data["first_name"], "Test")
        self.assertEqual(data["last_name"], "User")

        # Check if fields exist
        self.assertIn("is_visitor", data)
        self.assertIn("is_subscribed", data)
        self.assertIn("is_administrator", data)
        self.assertIn("can_create_routes", data)
        self.assertIn("can_publish_routes", data)

        # Check if computed values are for SUBSCRIBED user
        self.assertFalse(data["is_visitor"])
        self.assertTrue(data["is_subscribed"])
        self.assertFalse(data["is_administrator"])
        self.assertTrue(data["can_create_routes"])
        self.assertTrue(data["can_publish_routes"])

    def test_user_deserialization(self):
        """Test deserializing user data."""
        new_user_data = {
            "username": "newuser",
            "email": "new@example.com",
            "password": "newpassword123",
            "role": UserRoles.SUBSCRIBED,
            "first_name": "New",
            "last_name": "User",
        }

        serializer = CustomUserSerializer(data=new_user_data)
        self.assertTrue(serializer.is_valid(), serializer.errors)

        user = serializer.save()
        self.assertEqual(user.username, "newuser")
        self.assertEqual(user.email, "new@example.com")
        self.assertEqual(user.role, UserRoles.SUBSCRIBED)
        # Check if password is hashed
        self.assertTrue(user.check_password("newpassword123"))

    def test_read_only_fields(self):
        """Test that read-only fields cannot be written."""
        # Check that is_superuser is in read_only_fields
        read_only = CustomUserSerializer.Meta.read_only_fields
        self.assertIn("is_superuser", read_only)
        serializer = CustomUserSerializer()
        computed_fields = [
            "is_visitor",
            "is_subscribed",
            "is_administrator",
            "can_create_routes",
            "can_publish_routes",
        ]

        for field_name in computed_fields:
            field = serializer.fields.get(field_name)
            self.assertIsNotNone(
                field, f"Field {field_name} should exist in serializer"
            )
            self.assertTrue(field.read_only, f"Field {field_name} should be read_only")
        update_data = {"is_subscribed": False, "first_name": "Updated"}
        serializer = CustomUserSerializer(self.user, data=update_data, partial=True)
        self.assertTrue(serializer.is_valid())
        updated_user = serializer.save()
        self.assertTrue(updated_user.is_subscribed)
        self.assertEqual(updated_user.first_name, "Updated")

    def test_computed_fields_values(self):
        """Test that computed fields have correct values for different roles."""
        # Test for SUBSCRIBED user
        serializer = CustomUserSerializer(self.user)
        data = serializer.data

        self.assertFalse(data["is_visitor"])
        self.assertTrue(data["is_subscribed"])
        self.assertFalse(data["is_administrator"])
        self.assertTrue(data["can_create_routes"])
        self.assertTrue(data["can_publish_routes"])

        # Test for ADMIN user
        admin_user = CustomUser.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="password123",
            role=UserRoles.ADMIN,
        )
        admin_serializer = CustomUserSerializer(admin_user)
        admin_data = admin_serializer.data

        self.assertFalse(admin_data["is_visitor"])
        self.assertFalse(admin_data["is_subscribed"])
        self.assertTrue(admin_data["is_administrator"])
        self.assertTrue(admin_data["can_create_routes"])
        self.assertTrue(admin_data["can_publish_routes"])

        # Test for VISITOR user
        visitor_user = CustomUser.objects.create_user(
            username="visitor",
            email="visitor@example.com",
            password="password123",
            role=UserRoles.VISITOR,
        )
        visitor_serializer = CustomUserSerializer(visitor_user)
        visitor_data = visitor_serializer.data

        self.assertTrue(visitor_data["is_visitor"])
        self.assertFalse(visitor_data["is_subscribed"])
        self.assertFalse(visitor_data["is_administrator"])
        self.assertFalse(visitor_data["can_create_routes"])
        self.assertFalse(visitor_data["can_publish_routes"])

    def test_update_user(self):
        """Test updating a user."""
        update_data = {
            "first_name": "Updated",
            "last_name": "Name",
            "email": "updated@example.com",
        }

        serializer = CustomUserSerializer(self.user, data=update_data, partial=True)
        self.assertTrue(serializer.is_valid())

        updated_user = serializer.save()
        self.assertEqual(updated_user.first_name, "Updated")
        self.assertEqual(updated_user.last_name, "Name")
        self.assertEqual(updated_user.email, "updated@example.com")
        self.assertEqual(updated_user.username, "testuser")
        self.assertEqual(updated_user.role, UserRoles.SUBSCRIBED)

    def test_create_user_with_password(self):
        """Test creating a user with password."""
        user_data = {
            "username": "passworduser",
            "email": "password@example.com",
            "password": "securepassword123",
            "role": UserRoles.SUBSCRIBED,
            "first_name": "Password",
            "last_name": "User",
        }

        serializer = CustomUserSerializer(data=user_data)
        self.assertTrue(serializer.is_valid())

        user = serializer.save()
        # Check password is set correctly
        self.assertTrue(user.check_password("securepassword123"))

    def test_password_write_only(self):
        """Test that password field is write-only."""
        serializer = CustomUserSerializer()
        password_field = serializer.fields.get("password")

        # Password should be write-only
        self.assertIsNotNone(
            password_field, "Password field should exist in serializer"
        )
        self.assertTrue(
            password_field.write_only, "Password field should be write_only"
        )

        # When serializing a user, password should not be in the output
        user_serializer = CustomUserSerializer(self.user)
        user_data = user_serializer.data
        self.assertNotIn(
            "password", user_data, "Password should not be in serialized output"
        )
