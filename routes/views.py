from django.db import models
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Route
from .permissions import IsOwnerOrReadOnly
from .serializers import RouteGeoSerializer, RouteSerializer


class RouteViewSet(viewsets.ModelViewSet):
    """
    ViewSet for complete route management.
    Provides CRUD operations for routes with permission controls
    based on visibility and ownership.
    """

    serializer_class = RouteSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsOwnerOrReadOnly]
    filter_backends = [
        DjangoFilterBackend,
        filters.SearchFilter,
        filters.OrderingFilter,
    ]
    filterset_fields = ["visibility", "preference"]
    search_fields = ["name", "owner__username"]
    ordering_fields = ["created_at", "distance_km", "estimated_time_min"]
    ordering = ["-created_at"]

    def get_queryset(self):
        """Get the queryset of routes based on user permissions."""
        user = self.request.user

        if user.is_staff:
            return Route.objects.all()
        if user.is_authenticated:
            return Route.objects.filter(
                models.Q(owner=user) | models.Q(visibility="public")
            )
        # Anonymous users can only see public routes
        return Route.objects.filter(visibility="public")

    def perform_create(self, serializer):
        """Perform route creation with automatic owner assignment."""
        serializer.save(owner=self.request.user)

    @action(detail=False, methods=["get"])
    def my_routes(self, request):
        """Retrieve routes belonging to the current authenticated user."""
        routes = Route.objects.filter(owner=request.user)
        serializer = self.get_serializer(routes, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def public(self, request):
        """Retrieve only public routes."""
        routes = Route.objects.filter(visibility="public")
        serializer = self.get_serializer(routes, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"], permission_classes=[permissions.AllowAny])
    def geojson(self, request):
        """Retrieve routes in GeoJSON format for map visualization."""
        queryset = self.filter_queryset(self.get_queryset())
        serializer = RouteGeoSerializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["post"])
    def toggle_visibility(self, request, pk=None):
        """Toggle route visibility between private and public."""
        route = self.get_object()

        if route.owner != request.user and not request.user.is_staff:
            return Response(
                {"error": "Only the route owner can change visibility."},
                status=status.HTTP_403_FORBIDDEN,
            )

        if route.visibility == "private":
            route.visibility = "public"
        else:
            route.visibility = "private"

        route.save()
        serializer = self.get_serializer(route)
        return Response(serializer.data)
