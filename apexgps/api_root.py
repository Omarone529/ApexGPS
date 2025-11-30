from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.reverse import reverse


class APIRootView(APIView):
    """
    Custom API root view that list all ViewSet
    """
    def get(self,request,format=None):
        return Response({
            #all apps with their endpoints
            'users': reverse('customuser-list', request = request, format=format),
        })
