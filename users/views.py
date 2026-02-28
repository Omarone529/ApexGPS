from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CustomUser
from .permissions import IsAdminUser
from .serializers import (
    CustomUserPublicSerializer,
    CustomUserWriteSerializer,
    RegisterSerializer,
    GoogleAuthSerializer, HiddenUntilSerializer,
)


class IsAdminOrOwner(permissions.BasePermission):
    """Custom permission to allow either admin users or the object owner."""

    def has_permission(self, request, view):
        """List/create: Admin only. Other actions: Any authenticated user."""
        if view.action in ["list", "create"]:
            return IsAdminUser().has_permission(request, view)
        return request.user and request.user.is_authenticated

    def has_object_permission(self, request, view, obj):
        """Admin can access any object, users can only access their own."""
        if IsAdminUser().has_permission(request, view):
            return True
        return obj == request.user


class RegisterView(APIView):
    """
    API endpoint for user registration.
    Public endpoint - no authentication required.
    """
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(
            {
                "detail": "Registrazione completata.",
                "user": CustomUserPublicSerializer(user).data,
            },
            status=status.HTTP_201_CREATED,
        )


class MeView(APIView):
    """
    API endpoint for current authenticated user.
    Returns the user's public profile.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        return Response(
            CustomUserPublicSerializer(request.user).data,
            status=status.HTTP_200_OK
        )

@method_decorator(csrf_exempt, name='dispatch')
class GoogleLoginView(APIView):
    """
    API endpoint for Google OAuth login.
    Accepts Google access token and returns JWT tokens.
    """
    permission_classes = [permissions.AllowAny]
    serializer_class = GoogleAuthSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)

        return Response(
            serializer.validated_data,
            status=status.HTTP_200_OK
        )


class CustomUserViewSet(viewsets.ModelViewSet):
    """
    ViewSet that provides all CRUD (Create, Read, Update, Delete) operations
    for the CustomUser model.
    """

    queryset = CustomUser.objects.all()

    def get_serializer_class(self):
        """Use different serializers for read vs write operations."""
        if self.action in ['create', 'update', 'partial_update']:
            return CustomUserWriteSerializer
        return CustomUserPublicSerializer

    def get_permissions(self):
        """
        Apply permissions based on user role and action:
        - Only ADMIN can list all users or create new users
        - ADMIN or the user themselves can view/edit/delete specific user.
        """
        if self.action in ["list", "create"]:
            return [IsAdminUser()]
        elif self.action in ["retrieve", "update", "partial_update", "destroy"]:
            return [IsAdminOrOwner()]
        elif self.action == "me":
            return [permissions.IsAuthenticated()]

        return super().get_permissions()

    @action(detail=False, methods=["get"])
    def me(self, request):
        """
        Get current authenticated user's data.
        Available to all authenticated users.
        """
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], permission_classes=[IsAdminUser])
    def ban(self, request, pk=None):
        """Imposta hiddenUntil per l'utente e rende privati tutti i suoi percorsi."""
        user = self.get_object()
        serializer = HiddenUntilSerializer(data=request.data)
        if serializer.is_valid():
            user.hiddenUntil = serializer.validated_data.get('hidden_until')
            user.save(update_fields=['hiddenUntil'])
            # Forza tutti i percorsi dell'utente a privati
            user.routes.update(visibility='private')
            return Response({
                'status': 'user banned',
                'hidden_until': user.hiddenUntil
            })
        return Response(serializer.errors, status=400)

    @action(detail=True, methods=['delete'], permission_classes=[IsAdminUser])
    def unban(self, request, pk=None):
        """Rimuove il ban (hiddenUntil = null)."""
        user = self.get_object()
        user.hiddenUntil = None
        user.save(update_fields=['hiddenUntil'])
        return Response({'status': 'user unbanned'})