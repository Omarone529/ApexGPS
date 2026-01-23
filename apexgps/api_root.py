from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView


class APIRootView(APIView):
    """Custom API root view that list all ViewSet."""

    def get(self, request, format=None):
        """This function is used to return all ViewSet."""
        return Response(
            {
                # all apps with their endpoints
                "users": reverse("customuser-list", request=request, format=format),
                "gis_data": {
                    "points-of-interest": reverse(
                        "pointofinterest-list", request=request, format=format
                    ),
                    "scenic-areas": reverse(
                        "scenicarea-list", request=request, format=format
                    ),
                },
                "dem_data": {
                    "elevation-queries": reverse(
                        "elevationquery-list", request=request, format=format
                    ),
                    "dem": reverse("dem-list", request=request, format=format),
                },
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
                "authentication": reverse(
                    "rest_framework:login", request=request, format=format
                ),
                "admin": reverse("admin:index", request=request, format=format),
            }
        )
