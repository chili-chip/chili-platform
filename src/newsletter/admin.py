from django.contrib import admin, messages

from accounts.mail import MailNotConfigured
from newsletter import services
from newsletter.models import Delivery, Issue, Subscriber


@admin.register(Subscriber)
class SubscriberAdmin(admin.ModelAdmin):
    list_display = ("email", "created_at", "confirmed_at", "unsubscribed_at")
    list_filter = ("confirmed_at", "unsubscribed_at")
    search_fields = ("email",)


@admin.register(Issue)
class IssueAdmin(admin.ModelAdmin):
    list_display = ("subject", "created_at", "sending_started_at", "sent_at")
    readonly_fields = ("created_by", "created_at", "updated_at", "sending_started_at", "sent_at")
    actions = ["send_test_to_me", "send_next_batch"]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    @admin.action(description="Send a test to my email")
    def send_test_to_me(self, request, queryset):
        for issue in queryset:
            try:
                services.send_test(issue, request.user.email)
            except Exception as exc:
                self.message_user(request, f"{issue}: {exc}", messages.ERROR)
                return
        self.message_user(request, f"Test sent to {request.user.email}.")

    @admin.action(description="Send next batch to subscribers")
    def send_next_batch(self, request, queryset):
        for issue in queryset:
            try:
                result = services.send_issue_batch(issue)
            except MailNotConfigured as exc:
                self.message_user(request, f"{issue}: {exc}", messages.ERROR)
                return
            self.message_user(
                request,
                f"{issue}: sent {result['sent']}, failed {result['failed']}, "
                f"remaining {result['remaining']}.",
            )


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ("issue", "email", "sent_at", "error")
    list_filter = ("issue",)
    search_fields = ("email",)
