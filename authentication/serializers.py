from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

User = get_user_model()


class EmailOrUsernameTokenObtainPairSerializer(TokenObtainPairSerializer):
    identifier = serializers.CharField(required=False, write_only=True)
    email = serializers.EmailField(required=False, write_only=True)
    username = serializers.CharField(required=False, write_only=True)  # opzionale
    password = serializers.CharField(write_only=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # IMPORTANTISSIMO: SimpleJWT crea "username" come required=True → lo rendiamo opzionale
        if self.username_field in self.fields:
            self.fields[self.username_field].required = False

    def validate(self, attrs):
        password = attrs.get("password")

        value = attrs.get("identifier") or attrs.get("email") or attrs.get("username")
        if not value:
            raise serializers.ValidationError(
                {"identifier": "Send identifier (or email/username) + password."}
            )

        user = User.objects.filter(
            Q(email__iexact=value) | Q(username__iexact=value)
        ).first()

        if not user:
            raise serializers.ValidationError(
                "No active account found with the given credentials"
            )

        # super() vuole username+password
        return super().validate({"username": user.username, "password": password})
    
class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ("email", "username", "password")

    def create(self, validated_data):
        return User.objects.create_user(
            email=validated_data["email"],
            username=validated_data["username"],
            password=validated_data["password"],
        )

