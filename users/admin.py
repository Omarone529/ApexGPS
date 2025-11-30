from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import CustomUser

class CustomUserAdmin(UserAdmin):
    """
    Customize the display of the CustomUser template in the Admin panel.
    """
    add_fieldsets = UserAdmin.add_fieldsets + (
        (None, {'fields': ('role',)}),
    )

    fieldsets = UserAdmin.fieldsets + (
        ('ApexGPS Role', {'fields': ('role',)}),
    )

    list_display = UserAdmin.list_display + ('role',)

    list_filter = UserAdmin.list_filter + ('role',)

admin.site.register(CustomUser, CustomUserAdmin)