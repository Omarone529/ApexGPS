from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import CustomUser

User = get_user_model()

class CustomUserPublicSerializer(serializers.ModelSerializer):
    """
    Serializer that maps CustomUser model fields in JSON format.

    Includes role-based permission flags for client applications.
    Safe serializer for frontend responses.
    Use it for /api/auth/me/ and for responses (register/login).
    """

    is_visitor = serializers.BooleanField(read_only=True)
    is_subscribed = serializers.BooleanField(read_only=True)
    is_administrator = serializers.BooleanField(read_only=True)
    can_create_routes = serializers.BooleanField(read_only=True)
    can_publish_routes = serializers.BooleanField(read_only=True)

    class Meta:
        """Meta class for CustomUser."""
        model = CustomUser
        fields = (
            "id",
            "username",
            "email",
            "role",
            "first_name",
            "last_name",
            "is_superuser",
            "is_visitor",
            "is_subscribed",
            "is_administrator",
            "can_create_routes",
            "can_publish_routes",
        )
        read_only_fields = ("is_superuser",)
        extra_kwargs = {"password": {"write_only": True}}

    def to_representation(self, instance):
        """Add computed permission fields to the serialized output."""
        rep = super().to_representation(instance)
        rep["is_visitor"] = instance.is_visitor
        rep["is_subscribed"] = instance.is_subscribed
        rep["is_administrator"] = instance.is_administrator
        rep["can_create_routes"] = instance.can_create_private_routes()
        rep["can_publish_routes"] = instance.can_publish_routes()
        return rep


# Alias
CustomUserSerializer = CustomUserPublicSerializer


class CustomUserWriteSerializer(serializers.ModelSerializer):
    """
    Write serializer for create/update operations.

    Do NOT use it for /me responses.
    Use it only for user management endpoints.
    """

    password = serializers.CharField(write_only=True, trim_whitespace=False)

    class Meta:
        model = CustomUser
        fields = ("email", "username", "password", "first_name", "last_name", "role")

    def validate_password(self, value):
        """Validate password using Django validators."""
        validate_password(value)
        return value

    def create(self, validated_data):
        """Create a new user with encrypted password."""
        password = validated_data.pop("password")
        user = CustomUser(**validated_data)
        user.set_password(password)
        user.save()
        return user

    def update(self, instance, validated_data):
        """Update a user, handling password separately."""
        password = validated_data.pop("password", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.save()
        return instance


class RegisterSerializer(serializers.ModelSerializer):
    """
    Serializer for user registration.
    Specifico per la registrazione pubblica.
    """
    password = serializers.CharField(
        write_only=True,
        min_length=8,
        trim_whitespace=False,
        style={'input_type': 'password'}
    )

    class Meta:
        model = CustomUser
        fields = ("email", "username", "password", "first_name", "last_name")

    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Email già utilizzata.")
        return value

    def validate_username(self, value):
        if User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError("Username già utilizzato.")
        return value

    def validate_password(self, value):
        # Django's password validator
        validate_password(value)
        return value

    def create(self, validated_data):
        # Always use create_user for hashing and model manager rules
        return User.objects.create_user(**validated_data)