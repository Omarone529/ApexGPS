from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from users.serializers import CustomUserSerializer
from .serializers import RegisterSerializer


class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            {"detail": "Registrazione completata.", "user": CustomUserSerializer(user).data},
            status=status.HTTP_201_CREATED,
        )


class MeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(CustomUserSerializer(request.user).data, status=200)
