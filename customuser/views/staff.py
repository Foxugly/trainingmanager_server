"""Administration staff des comptes (spec lot A §A.4). Surface volontairement
minimale : rechercher un compte et basculer son accès offert. Toute autre
édition passe par l'admin Django."""

from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import permissions, serializers
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from ..models import CustomUser
from ..serializers import StaffUserSerializer

SEARCH_LIMIT = 50


class StaffUserListResponseSerializer(serializers.Serializer):
    """Enveloppe de la recherche staff (liste nue, pas de pagination)."""

    results = StaffUserSerializer(many=True)


class StaffUserListView(APIView):
    permission_classes = [permissions.IsAdminUser]

    @extend_schema(
        summary="Search accounts (staff)",
        description=(
            "Search accounts by email or name. Without `q`, returns the accounts "
            "that currently have offered access. Staff only."
        ),
        parameters=[
            OpenApiParameter(
                name="q",
                type=str,
                required=False,
                description="Case-insensitive substring matched against email, first and last name.",
            )
        ],
        responses={200: StaffUserListResponseSerializer},
    )
    def get(self, request):
        q = (request.query_params.get("q") or "").strip()
        if q:
            qs = CustomUser.objects.filter(
                Q(email__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
            )
        else:
            qs = CustomUser.objects.filter(subscription_bypass=True)
        qs = qs.order_by("email")[:SEARCH_LIMIT]
        return Response({"results": StaffUserSerializer(qs, many=True).data})


class StaffUserDetailView(APIView):
    permission_classes = [permissions.IsAdminUser]

    @extend_schema(
        summary="Toggle offered access (staff)",
        description=(
            "Grant or revoke offered access on an account. Granting stamps "
            "`bypass_granted_at`; revoking keeps it, so the original grant stays "
            "auditable. Staff only."
        ),
        request=StaffUserSerializer,
        responses={200: StaffUserSerializer},
    )
    def patch(self, request, pk):
        user = get_object_or_404(CustomUser, pk=pk)
        was_granted = user.subscription_bypass
        serializer = StaffUserSerializer(user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        if user.subscription_bypass and not was_granted:
            user.bypass_granted_at = timezone.now()
            user.save(update_fields=["bypass_granted_at"])
        return Response(StaffUserSerializer(user).data)
