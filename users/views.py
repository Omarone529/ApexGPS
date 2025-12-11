from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import CustomUser
from .permissions import IsAdminUser, IsOwnerOrReadOnly
from .serializers import CustomUserSerializer


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
            return [IsAdminUser() | IsOwnerOrReadOnly()]
        elif self.action == "me":
            from rest_framework.permissions import IsAuthenticated

            return [IsAuthenticated()]

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
