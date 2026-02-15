from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView
from .jwt import EmailOrUsernameTokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView
from .views import RegisterView, MeView, CustomUserViewSet
from .views import GoogleLoginView


class LoginView(TokenObtainPairView):
    """
    Custom login view that accepts either email or username.
    Returns JWT tokens and user data.
    """
    serializer_class = EmailOrUsernameTokenObtainPairSerializer


# Router for ViewSet
router = DefaultRouter()
router.register(r'users', CustomUserViewSet, basename='customuser')

# Authentication URLs
urlpatterns = [
    # JWT endpoints
    path('login/', LoginView.as_view(), name='token_obtain_pair'),
    path('refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('login/google/', GoogleLoginView.as_view(), name='google_login'),

    # User management endpoints
    path('register/', RegisterView.as_view(), name='register'),
    path('me/', MeView.as_view(), name='me'),

    # Include ViewSet URLs (for admin user management)
    path('', include(router.urls)),
]