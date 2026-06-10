from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from tools.ai import call_claude
from tools.throttling import AIPingThrottle


class AIPingView(APIView):
    """POST /api/v1/ai/ping/ — minimal Claude call for diagnostics.

    Staff-only: this forwards an arbitrary free-text prompt to the paid
    Anthropic API and is a diagnostic, not a product feature. Gating it to
    is_staff removes the arbitrary-prompt-to-LLM cost/abuse channel that an
    IsTrainer gate (satisfied by creating any team) left open.
    """

    permission_classes = [IsAuthenticated, IsAdminUser]
    throttle_classes = [AIPingThrottle]

    @extend_schema(
        request=inline_serializer(
            name="AIPingRequest",
            fields={
                "prompt": serializers.CharField(
                    required=False,
                    help_text="Prompt to send to Claude. Defaults to a hello-world prompt.",
                ),
            },
        ),
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="AIPingResponse",
                    fields={
                        "text": serializers.CharField(),
                        "model": serializers.CharField(),
                        "input_tokens": serializers.IntegerField(),
                        "output_tokens": serializers.IntegerField(),
                        "stop_reason": serializers.CharField(),
                    },
                ),
                description="Claude responded successfully",
            ),
            400: OpenApiResponse(description="Invalid prompt"),
            500: OpenApiResponse(description="AI configuration error"),
            502: OpenApiResponse(description="AI service error"),
        },
        description="Diagnostic ping to the Anthropic API.",
    )
    def post(self, request):
        prompt = request.data.get("prompt", "Say hello in one word.")
        if not isinstance(prompt, str) or not prompt.strip():
            return Response(
                {"code": "prompt_empty", "detail": _("prompt must be a non-empty string.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(prompt) > 5000:
            return Response(
                {
                    "code": "prompt_too_long",
                    "detail": _("prompt too long (max 5000 chars for ping)."),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        result = call_claude(
            prompt,
            track_kwargs={
                "team": None,
                "user": request.user if request.user.is_authenticated else None,
                "endpoint": "ping",
            },
        )
        return Response(result, status=status.HTTP_200_OK)
