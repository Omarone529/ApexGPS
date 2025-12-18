from rest_framework import permissions


class IsOwnerOrReadOnly(permissions.BasePermission):
    """
    Custom permission that allows only the owner to modify an object.
    Unauthenticated users can only read public objects.
    Authenticated users can read all objects (public, private if owner)
    but can only modify/delete their own objects.
    Supports both Route and Stop objects.
    """

    def has_object_permission(self, request, view, obj):
        """Check object-level permissions."""
        # Read permissions are allowed for any request
        if request.method in permissions.SAFE_METHODS:
            if hasattr(obj, "can_view"):
                return obj.can_view(request.user)
            elif hasattr(obj, "route"):
                return obj.route.can_view(request.user)
            elif hasattr(obj, "owner"):
                if hasattr(obj, "can_view"):
                    return obj.can_view(request.user)
                return obj.owner == request.user or request.user.is_staff
            return True

        if hasattr(obj, "owner"):
            return obj.owner == request.user or request.user.is_staff
        elif hasattr(obj, "route"):
            return obj.route.owner == request.user or request.user.is_staff
        return request.user and request.user.is_staff


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
