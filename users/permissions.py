from rest_framework import permissions


class BaseRolePermission(permissions.BasePermission):
    """
    Base permission class that checks if user has one of the allowed roles.
    Only authenticated users with a valid role attribute can pass this check.
    """

    allowed_roles = []

    def has_permission(self, request, view):
        """Check if the user is authenticated and has one of the allowed roles."""
        if not request.user or not request.user.is_authenticated:
            return False

        if not hasattr(request.user, "role"):
            return False

        return request.user.role in self.allowed_roles


class IsRegisteredUser(BaseRolePermission):
    """
    Permission class that allows access only to SUBSCRIBED or ADMIN users.
    Use this for endpoints that require registered user privileges.
    """

    allowed_roles = ["SUBSCRIBED", "ADMIN"]


class IsAdminUser(BaseRolePermission):
    """
    Permission class that allows access only to ADMIN users.
    Use this for administrative endpoints requiring full system control.
    """

    allowed_roles = ["ADMIN"]


class IsOwnerOrReadOnly(permissions.BasePermission):
    """
    Object-level permission that allows read-only access to anyone,
    but write access (update/delete) only to the object's owner.

    The owner is determined by checking either 'user' or 'owner' attribute
    on the object model.
    """

    def has_object_permission(self, request, view, obj):
        """
        Check if the request method is safe (GET, HEAD, OPTIONS) or
        if the user is the owner of the object.
        """
        if request.method in permissions.SAFE_METHODS:
            return True

        return (hasattr(obj, "user") and obj.user == request.user) or (
            hasattr(obj, "owner") and obj.owner == request.user
        )
