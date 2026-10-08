from __future__ import annotations

from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.mail import MailDeliveryError, MailNotConfigured
from app.throttles import NewsletterSubscribeThrottle
from newsletter import services
from newsletter.models import Issue
from newsletter.serializers import IssueSerializer, SubscribeSerializer, TokenSerializer
from newsletter.tokens import read_confirm_token, read_unsubscribe_token


def _mail_not_configured():
    return Response({"detail": "Mail is not configured."}, status=status.HTTP_503_SERVICE_UNAVAILABLE)


def _mail_failed():
    return Response({"detail": "Could not send email."}, status=status.HTTP_502_BAD_GATEWAY)


class SubscribeView(APIView):
    """Start a double opt-in. The reply is the same whether or not the address is subscribed."""

    permission_classes = [permissions.AllowAny]
    authentication_classes: list = []
    throttle_classes = [NewsletterSubscribeThrottle]

    def post(self, request):
        serializer = SubscribeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            services.request_subscription(serializer.validated_data["email"])
        except MailNotConfigured:
            return _mail_not_configured()
        except MailDeliveryError:
            return _mail_failed()
        return Response(
            {"detail": "Check your inbox for a link to confirm your subscription."},
            status=status.HTTP_202_ACCEPTED,
        )


class ConfirmView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes: list = []

    def post(self, request):
        serializer = TokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = read_confirm_token(serializer.validated_data["token"])
        if email is None:
            return Response(
                {"detail": "This confirmation link is invalid or has expired."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        services.confirm_subscription(email)
        return Response({"detail": "You are subscribed to the newsletter."})


class UnsubscribeView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes: list = []

    def post(self, request):
        serializer = TokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = read_unsubscribe_token(serializer.validated_data["token"])
        if email is None:
            return Response(
                {"detail": "This unsubscribe link is invalid."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        services.unsubscribe(email)
        return Response({"detail": "You are unsubscribed from the newsletter."})


class IssueViewSet(viewsets.ModelViewSet):
    """Staff write and send issues. Sending goes in batches; call send until remaining is 0."""

    serializer_class = IssueSerializer
    permission_classes = [permissions.IsAdminUser]
    queryset = Issue.objects.all()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def destroy(self, request, *args, **kwargs):
        if self.get_object().sending_started_at is not None:
            return Response(
                {"detail": "An issue cannot be deleted after sending starts."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=["get"])
    def audience(self, request):
        return Response({"recipients": len(services.recipient_emails())})

    @action(detail=True, methods=["post"], url_path="send-test")
    def send_test(self, request, pk=None):
        issue = self.get_object()
        try:
            services.send_test(issue, request.user.email)
        except MailNotConfigured:
            return _mail_not_configured()
        except MailDeliveryError:
            return _mail_failed()
        return Response({"detail": f"Test sent to {request.user.email}."})

    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        issue = self.get_object()
        try:
            result = services.send_issue_batch(issue)
        except MailNotConfigured:
            return _mail_not_configured()
        issue.refresh_from_db()
        return Response({**result, "issue": IssueSerializer(issue).data})
