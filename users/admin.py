from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.translation import gettext_lazy as _

from .models import CustomUser, UserRoles


class CustomUserAdmin(UserAdmin):
    """
    Customize the display of the CustomUser model in the Admin panel.
    Extends Django's default UserAdmin to include role management.
    """

    # Add 'role' field to user creation form
    add_fieldsets = UserAdmin.add_fieldsets + ((None, {"fields": ("role",)}),)

    # Add 'role' field to user edit form
    fieldsets = UserAdmin.fieldsets + ((_("Ruolo ApexGPS"), {"fields": ("role",)}),)

    list_display = UserAdmin.list_display + ("role",)

    # Add role filter
    list_filter = UserAdmin.list_filter + ("role",)

    actions = ["make_subscribed", "make_admin", "make_visitor"]

    def make_subscribed(self, request, queryset):
        """Mark selected users as Subscribed."""
        updated = queryset.update(role=UserRoles.SUBSCRIBED)
        self.message_user(request, f"{updated} utenti segnati come 'Utenti Iscritti'.")

    make_subscribed.short_description = "Segna come Utenti Iscritti"

    def make_admin(self, request, queryset):
        """Mark selected users as Administrators."""
        updated = queryset.update(role=UserRoles.ADMIN)
        self.message_user(request, f"{updated} utenti segnati come 'Amministratori'.")

    make_admin.short_description = "Segna come Amministratori"

    def make_visitor(self, request, queryset):
        """Mark selected users as Visitors."""
        updated = queryset.update(role=UserRoles.VISITOR)
        self.message_user(request, f"{updated} utenti segnati come 'Visitatori'.")

    make_visitor.short_description = "Segna come Visitatori"


# Register the custom user model with admin
admin.site.register(CustomUser, CustomUserAdmin)
