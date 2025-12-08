from rest_framework import permissions


class BaseRolePermission(permissions.BasePermission):
    """Base permission class that checks if user has required role(s)."""

    allowed_roles = []

    def has_permission(self, request, view):
        """Return True if user has required role(s)."""
        if not request.user or not request.user.is_authenticated:
            return False

        if not hasattr(request.user, "role"):
            return False

        return request.user.role in self.allowed_roles


class AllowAnyUser(permissions.AllowAny):
    """
    Allow any user, including unauthenticated.
    Used for public endpoints.
    """


class IsAuthenticatedUser(permissions.IsAuthenticated):
    """Allow any authenticated user regardless of role."""


class IsSubscribedUser(BaseRolePermission):
    """
    Permission for SUBSCRIBED users only.
    Based on spec: 'Iscritto' users can create/manage private itineraries.
    """

    allowed_roles = ["SUBSCRIBED"]


class IsAdminUser(BaseRolePermission):
    """
    Permission for ADMIN users only.
    Based on spec: Administrators have full control.
    """

    allowed_roles = ["ADMIN"]


class IsRegisteredUser(BaseRolePermission):
    """Permission for authenticated registered users (SUBSCRIBED or ADMIN)."""

    allowed_roles = ["SUBSCRIBED", "ADMIN"]


class IsOwnerOrReadOnly(permissions.BasePermission):
    """
    Object-level permission to allow owners to edit/delete.
    Others can only view.
    """

    def has_object_permission(self, request, view, obj):
        """Check object is owner."""
        if request.method in permissions.SAFE_METHODS:
            return True

        return (hasattr(obj, "user") and obj.user == request.user) or (
            hasattr(obj, "owner") and obj.owner == request.user
        )


class CanCreatePrivateItineraries(BaseRolePermission):
    """
    Permission to create private itineraries.
    Based on spec: Only subscribed users can create private itineraries.
    """

    allowed_roles = ["SUBSCRIBED", "ADMIN"]


class CanPublishItineraries(BaseRolePermission):
    """
    Permission to publish itineraries.
    Based on spec: Subscribed users can publish their itineraries.
    """

    allowed_roles = ["SUBSCRIBED", "ADMIN"]
