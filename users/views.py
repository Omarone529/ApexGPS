from rest_framework import permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import CustomUser
from .permissions import IsAdminUser, IsOwnerOrReadOnly
from .serializers import CustomUserSerializer


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


class CustomUserViewSet(viewsets.ModelViewSet):
    """
    ViewSet that provides all CRUD (Create, Read, Update, Delete) operations
    for the CustomUser model.
    """

    queryset = CustomUser.objects.all()
    serializer_class = CustomUserSerializer

    def get_permissions(self):
        """
        Apply permissions based on user role and action:
        - Only ADMIN can list all users or create new users
        - ADMIN or the user themselves can view/edit/delete specific user.
        """
        if self.action in ["list", "create"]:
            return [IsAdminUser()]
        elif self.action in ["retrieve", "update", "partial_update", "destroy"]:
            # Using the custom class
            return [IsAdminOrOwner()]
        elif self.action == "me":
            return [permissions.IsAuthenticated()]

        return super().get_permissions()

    # @action adds custom endpoints to the ViewSet
    @action(detail=False, methods=["get"])
    def me(self, request):
        """
        Get current authenticated user's data.
        Available to all authenticated users.
        """
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)
