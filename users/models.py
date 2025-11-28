from django.contrib.auth.models import AbstractUser, Group, Permission
from django.db import models
from django.utils.translation import gettext_lazy as _


class UserRoles(models.TextChoices):
    """Defines the available roles for users in ApexGPS using Enums"""
    VISITOR = 'VISITOR', _('Visitor (Unauthenticated)')
    SUBSCRIBED = 'SUBSCRIBED', _('Subscribed User')
    ADMIN = 'ADMIN', _('Administrator')


class CustomUser(AbstractUser):
    """
    Custom User Model for ApexGPS, extending Django's AbstractUser
    to include a specific role for permissions management.
    """

    role = models.CharField(
        max_length=20,
        choices=UserRoles.choices,
        default=UserRoles.SUBSCRIBED,
        verbose_name=_('User Role')
    )

    groups = models.ManyToManyField(
        Group,
        verbose_name=_('groups'),
        blank=True,
        help_text=_(
            'The groups this user belongs to. A user will get all permissions '
            'granted to each of their groups.'
        ),
        related_name="custom_user_set",
        related_query_name="user",
    )
    user_permissions = models.ManyToManyField(
        Permission,
        verbose_name=_('user permissions'),
        blank=True,
        help_text=_('Specific permissions for this user.'),
        related_name="custom_user_permissions",
        related_query_name="user",
    )

    class Meta:
        verbose_name = _('ApexGPS User')
        verbose_name_plural = _('ApexGPS Users')

    def __str__(self):
        """Returns the username and their current role."""
        return f"{self.username} ({self.get_role_display()})"

    def is_administrator(self):
        """Checks if the user has Administrator privileges."""
        return self.role == UserRoles.ADMIN