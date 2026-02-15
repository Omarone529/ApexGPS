from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView


class APIRootView(APIView):
    """Custom API root view that list all ViewSet."""

    def get(self, request, format=None):
        """This function is used to return all ViewSet."""
        return Response(
            {
                # Authentication endpoints (JWT)
                "authentication": {
                    "login": reverse("token_obtain_pair", request=request, format=format),
                    "refresh": reverse("token_refresh", request=request, format=format),
                    "register": reverse("register", request=request, format=format),
                    "me": reverse("me", request=request, format=format),
                    "google": reverse("google_login", request=request, format=format),  # <-- NUOVO
                },
                # Users management
                "users": reverse("customuser-list", request=request, format=format),
                # GIS Data
                "gis_data": {
                    "points-of-interest": reverse(
                        "pointofinterest-list", request=request, format=format
                    ),
                    "scenic-areas": reverse(
                        "scenicarea-list", request=request, format=format
                    ),
                },
                # DEM Data
                "dem_data": {
                    "elevation-queries": reverse(
                        "elevationquery-list", request=request, format=format
                    ),
                    "dem": reverse("dem-list", request=request, format=format),
                },
                # Routes
                "routes": {
                    "routes": reverse("route-list", request=request, format=format),
                    "stops": reverse("stop-list", request=request, format=format),
                    "calculate-benchmark": reverse(
                        "route-calculate-fastest", request=request, format=format
                    ),
                    "calculate-scenic": reverse(
                        "route-calculate-scenic", request=request, format=format
                    ),
                    "my-routes": reverse(
                        "route-my-routes", request=request, format=format
                    ),
                    "public": reverse("route-public", request=request, format=format),
                },
                # Admin
                "admin": reverse("admin:index", request=request, format=format),
            }
        )