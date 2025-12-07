from rest_framework import viewsets

from .models import CustomUser
from .serializer import CustomUserSerializer


class CustomUserViewSet(viewsets.ModelViewSet):
    """
    ViewSet that provides all CRUD (Create, Read, Update, Delete) operations
    for the CustomUser model.
    """

    queryset = CustomUser.objects.all()
    serializer_class = CustomUserSerializer
