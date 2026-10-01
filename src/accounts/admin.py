from django.contrib import admin

from accounts.models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("username", "email", "email_verified", "is_staff", "created_at")
    list_filter = ("email_verified", "is_staff")
    search_fields = ("username", "email")
