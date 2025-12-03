from django.contrib.auth.models import AbstractUser, Group, Permission
from django.db import models
from django.utils.translation import gettext_lazy as _


class UserRoles(models.TextChoices):
    """Defines the available roles for users in ApexGPS using Enums"""
    VISITOR = 'VISITOR', _('Visitatore (Non autenticato)')
    SUBSCRIBED = 'SUBSCRIBED', _('Utente Registrato')
    ADMIN = 'ADMIN', _('Amministratore')


class CustomUser(AbstractUser):
    """
    Custom User Model for ApexGPS, extending Django's AbstractUser
    to include a specific role for permissions management.
    """

    role = models.CharField(
        max_length=20,
        choices=UserRoles.choices,
        default=UserRoles.SUBSCRIBED,
        verbose_name=_('Ruolo Utente')
    )

    groups = models.ManyToManyField(
        Group,
        verbose_name=_('Gruppi'),
        blank=True,
        help_text=_(
            'I gruppi a cui appartiene questo utente. Un utente otterrà '
            'tutti i permessi assegnati a ciascuno dei suoi gruppi.'
        ),
        related_name="custom_user_set",
        related_query_name="user",
    )
    user_permissions = models.ManyToManyField(
        Permission,
        verbose_name=_('Permessi utente'),
        blank=True,
        help_text=_('Permessi specifici per questo utente.'),
        related_name="custom_user_permissions",
        related_query_name="user",
    )

    class Meta:
        """Meta class for CustomUser"""
        verbose_name = _('Utente ApexGPS')
        verbose_name_plural = _('Utenti ApexGPS')

    def __str__(self):
        """Returns the username and their current role."""
        return f"{self.username} ({self.get_role_display()})"

    def is_administrator(self):
        """Checks if the user has Administrator privileges."""
        return self.role == UserRoles.ADMIN