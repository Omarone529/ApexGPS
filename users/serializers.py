from rest_framework import serializers

from .models import CustomUser


class CustomUserSerializer(serializers.ModelSerializer):
    """
    Serializer that maps CustomUser model fields in JSON format.
    Includes role-based permission flags for client applications.
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
            "password",
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

    def create(self, validated_data):
        """Create a new user with encrypted password."""
        password = validated_data.pop("password", None)
        user = CustomUser(**validated_data)
        if password:
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

    def to_representation(self, instance):
        """Add computed permission fields to the serialized output."""
        representation = super().to_representation(instance)

        # Add permission flags based on user role
        representation["is_visitor"] = instance.is_visitor
        representation["is_subscribed"] = instance.is_subscribed
        representation["is_administrator"] = instance.is_administrator
        representation["can_create_routes"] = instance.can_create_private_routes()
        representation["can_publish_routes"] = instance.can_publish_routes()

        return representation
