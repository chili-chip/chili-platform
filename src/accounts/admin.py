from django.contrib import admin

from accounts.models import User, UserSettings


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("username", "email", "email_verified", "is_staff", "created_at")
    list_filter = ("email_verified", "is_staff")
    search_fields = ("username", "email")
    readonly_fields = ("terms_accepted_at", "privacy_accepted_at", "seller_terms_accepted_at")


@admin.register(UserSettings)
class UserSettingsAdmin(admin.ModelAdmin):
    list_display = ("user", "locale", "theme", "newsletter_opt_in", "updated_at")
    list_filter = ("newsletter_opt_in", "locale", "theme")
    search_fields = ("user__username", "user__email")
