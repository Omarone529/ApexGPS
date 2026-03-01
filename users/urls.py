from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView
from .jwt import EmailOrUsernameTokenObtainPairSerializer
from rest_framework_simplejwt.views import TokenObtainPairView
from .views import RegisterView, MeView, CustomUserViewSet, GoogleLoginView

class LoginView(TokenObtainPairView):
    serializer_class = EmailOrUsernameTokenObtainPairSerializer

# Router for the ViewSet
router = DefaultRouter()
router.register(r'', CustomUserViewSet, basename='customuser')

# URL patterns
urlpatterns = [
    # JWT endpoints
    path('login/', LoginView.as_view(), name='token_obtain_pair'),
    path('refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    # Google endpoint
    path('login/google/', GoogleLoginView.as_view(), name='google_login'),

    # User management endpoints
    path('register/', RegisterView.as_view(), name='register'),
    path('me/', MeView.as_view(), name='me'),


    path('', include(router.urls)),
]