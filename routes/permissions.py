from rest_framework import permissions


class IsOwnerOrReadOnly(permissions.BasePermission):
    """
    Custom permission that allows only the owner to modify an object.
    Unauthenticated users can only read public objects.
    Authenticated users can read all objects (public, private if owner)
    but can only modify/delete their own objects.
    """

    def has_object_permission(self, request, view, obj):
        """Check object-level permissions."""
        if request.method in permissions.SAFE_METHODS:
            return obj.can_view(request.user)
        # Write permissions only for the owner
        return obj.owner == request.user


class IsAdminOrReadOnly(permissions.BasePermission):
    """
    Permission that allows administrators all operations,
    while other users can only perform read operations.
    """

    def has_permission(self, request, view):
        """Check view-level permissions."""
        if request.method in permissions.SAFE_METHODS:
            return True
        return request.user and request.user.is_staff
