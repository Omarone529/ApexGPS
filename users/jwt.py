from django.contrib.auth import authenticate, get_user_model
from django.db.models import Q
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from users.serializers import CustomUserPublicSerializer

User = get_user_model()

GENERIC_AUTH_ERROR = "No active account found with the given credentials"


class EmailOrUsernameTokenObtainPairSerializer(TokenObtainPairSerializer):
    identifier = serializers.CharField(write_only=True)
    password = serializers.CharField(write_only=True, trim_whitespace=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields.pop(self.username_field, None)

    def validate(self, attrs):
        identifier = attrs.get("identifier")
        password = attrs.get("password")

        user = User.objects.filter(
            Q(email__iexact=identifier) | Q(username__iexact=identifier)
        ).first()

        if not user or not user.is_active:
            raise serializers.ValidationError(GENERIC_AUTH_ERROR)

        auth_user = authenticate(username=user.username, password=password)
        if not auth_user:
            raise serializers.ValidationError(GENERIC_AUTH_ERROR)

        refresh = self.get_token(auth_user)
        data = {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        }

        data["user"] = CustomUserPublicSerializer(auth_user).data
        return data