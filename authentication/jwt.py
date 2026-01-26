from django.contrib.auth import authenticate, get_user_model
from django.db.models import Q
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer


class EmailOrUsernameTokenObtainPairSerializer(TokenObtainPairSerializer):
    identifier = serializers.CharField(write_only=True)
    password = serializers.CharField(write_only=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Togliamo il campo che SimpleJWT aggiunge automaticamente (di solito "username")
        self.fields.pop(self.username_field, None)

    def validate(self, attrs):
        User = get_user_model()

        identifier = attrs.get("identifier")
        password = attrs.get("password")

        user = User.objects.filter(
            Q(email__iexact=identifier) | Q(username__iexact=identifier)
        ).first()

        if not user:
            raise serializers.ValidationError("No active account found with the given credentials")

        auth_user = authenticate(username=user.username, password=password)
        if not auth_user:
            raise serializers.ValidationError("No active account found with the given credentials")

        refresh = self.get_token(auth_user)
        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        }
