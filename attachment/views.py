import logging

from django.utils.translation import gettext_lazy as _
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response

from . import s3
from .models import Attachment
from .permissions import (
    can_read_target,
    can_write_target,
    content_type_for_label,
    resolve_target,
)
from .serializers import AttachmentSerializer, PresignUploadRequestSerializer

logger = logging.getLogger("attachment")


def _unconfigured_response():
    return Response(
        {
            "code": "attachments_unconfigured",
            "detail": _("File attachments are not configured on this server."),
        },
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


class AttachmentViewSet(viewsets.GenericViewSet):
    """File attachments on Events and Messages, stored in private S3.

    URL base: /api/v1/attachments/

    The browser uploads/downloads directly to/from S3 via short-lived presigned
    URLs the backend mints with the instance-role credentials; every endpoint is
    permission-gated through ``attachment.permissions``.
    """

    serializer_class = AttachmentSerializer
    queryset = Attachment.objects.all()

    # ---- helpers ----------------------------------------------------------

    def _get_attachment(self, pk) -> Attachment:
        attachment = Attachment.objects.filter(pk=pk).select_related(
            "content_type", "uploaded_by"
        ).first()
        if attachment is None:
            raise NotFound(_("Attachment not found."))
        return attachment

    # ---- 1. presign upload ------------------------------------------------

    @extend_schema(
        request=PresignUploadRequestSerializer,
        responses={
            201: OpenApiResponse(
                response=inline_serializer(
                    name="PresignUploadResponse",
                    fields={
                        "attachment_id": serializers.IntegerField(),
                        "upload_url": serializers.CharField(),
                        "s3_key": serializers.CharField(),
                        "headers": inline_serializer(
                            name="PresignUploadHeaders",
                            fields={"Content-Type": serializers.CharField()},
                        ),
                    },
                ),
                description="Presigned PUT URL issued; a pending Attachment row was created.",
            ),
            400: OpenApiResponse(
                description=(
                    "Validation error. `code` is one of: `mime_not_allowed`, "
                    "`file_too_large`, `invalid_target_type`, `validation_error`."
                )
            ),
            403: OpenApiResponse(description="You may not attach files to this target."),
            404: OpenApiResponse(description="Target not found."),
            503: OpenApiResponse(description="Attachments unconfigured (`attachments_unconfigured`)."),
        },
        description=(
            "Request a presigned S3 PUT URL to upload a file and create a "
            "`pending` Attachment for an Event or Message target. Validates the "
            "MIME allow-list and the size cap, and that the caller may write to "
            "the target (event-team manager / message author or coach)."
        ),
    )
    @action(detail=False, methods=["post"], url_path="presign")
    def presign(self, request):
        if not s3.attachments_configured():
            return _unconfigured_response()

        body = PresignUploadRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data

        target = resolve_target(data["target_type"], data["target_id"])
        if target is None:
            raise NotFound(_("Target not found."))
        if not can_write_target(request.user, target):
            raise PermissionDenied(_("You may not attach files to this target."))

        from django.conf import settings

        if data["content_type"] not in settings.ATTACHMENTS_ALLOWED_MIME:
            raise ValidationError(
                {"code": "mime_not_allowed", "detail": _("This file type is not allowed.")}
            )
        if not (0 < data["size"] <= settings.ATTACHMENTS_MAX_BYTES):
            raise ValidationError(
                {"code": "file_too_large", "detail": _("File exceeds the maximum allowed size.")}
            )

        content_type = content_type_for_label(data["target_type"])
        key = s3.build_key(data["target_type"], target.pk, data["filename"])

        attachment = Attachment.objects.create(
            content_type=content_type,
            object_id=target.pk,
            s3_key=key,
            filename=s3.sanitize_filename(data["filename"]),
            content_type_mime=data["content_type"],
            size_bytes=data["size"],
            uploaded_by=request.user,
            status=Attachment.PENDING,
        )

        upload_url = s3.presign_put(
            key, data["content_type"], settings.ATTACHMENTS_PRESIGN_EXPIRY
        )
        return Response(
            {
                "attachment_id": attachment.pk,
                "upload_url": upload_url,
                "s3_key": key,
                "headers": {"Content-Type": data["content_type"]},
            },
            status=status.HTTP_201_CREATED,
        )

    # ---- 2. complete ------------------------------------------------------

    @extend_schema(
        request=None,
        responses={
            200: AttachmentSerializer,
            400: OpenApiResponse(description="Upload not found in S3 (`upload_not_found`)."),
            403: OpenApiResponse(description="Not allowed to complete this attachment."),
            404: OpenApiResponse(description="Attachment not found."),
            503: OpenApiResponse(description="Attachments unconfigured (`attachments_unconfigured`)."),
        },
        description=(
            "Finalize an upload: HEAD the S3 object, record its real size, and "
            "flip the Attachment to `ready`. Caller must be the uploader or able "
            "to write the target."
        ),
    )
    @action(detail=True, methods=["post"], url_path="complete")
    def complete(self, request, pk=None):
        if not s3.attachments_configured():
            return _unconfigured_response()

        attachment = self._get_attachment(pk)
        target = attachment.content_object
        if not (
            attachment.uploaded_by_id == request.user.pk
            or can_write_target(request.user, target)
        ):
            raise PermissionDenied(_("You may not complete this attachment."))

        head = s3.head_object(attachment.s3_key)
        if head is None:
            raise ValidationError(
                {"code": "upload_not_found", "detail": _("Uploaded file not found in storage.")}
            )

        attachment.size_bytes = int(head.get("ContentLength", attachment.size_bytes))
        attachment.status = Attachment.READY
        attachment.save(update_fields=["size_bytes", "status"])
        return Response(AttachmentSerializer(attachment).data, status=status.HTTP_200_OK)

    # ---- 3. download ------------------------------------------------------

    @extend_schema(
        request=None,
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="AttachmentDownloadResponse",
                    fields={"url": serializers.CharField()},
                ),
                description="Presigned GET URL.",
            ),
            403: OpenApiResponse(description="No read access to this attachment's target."),
            404: OpenApiResponse(description="Attachment not found."),
            409: OpenApiResponse(description="Attachment not ready yet."),
            503: OpenApiResponse(description="Attachments unconfigured (`attachments_unconfigured`)."),
        },
        description=(
            "Mint a short-lived presigned GET URL to download a ready "
            "attachment. Caller must be able to read the target."
        ),
    )
    @action(detail=True, methods=["get"], url_path="download")
    def download(self, request, pk=None):
        if not s3.attachments_configured():
            return _unconfigured_response()

        attachment = self._get_attachment(pk)
        target = attachment.content_object
        if not can_read_target(request.user, target):
            raise PermissionDenied(_("You may not access this attachment."))

        if attachment.status != Attachment.READY:
            return Response(
                {"code": "attachment_not_ready", "detail": _("Attachment is not ready.")},
                status=status.HTTP_409_CONFLICT,
            )

        from django.conf import settings

        url = s3.presign_get(
            attachment.s3_key, attachment.filename, settings.ATTACHMENTS_PRESIGN_EXPIRY
        )
        return Response({"url": url}, status=status.HTTP_200_OK)

    # ---- 4. list by target ------------------------------------------------

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "target_type",
                OpenApiTypes.STR,
                OpenApiParameter.QUERY,
                required=True,
                enum=["event", "message"],
            ),
            OpenApiParameter(
                "target_id", OpenApiTypes.INT, OpenApiParameter.QUERY, required=True
            ),
        ],
        responses={
            200: AttachmentSerializer(many=True),
            400: OpenApiResponse(
                description="Missing/invalid query params (`missing_params`, `invalid_target_type`)."
            ),
            403: OpenApiResponse(description="No read access to this target."),
            404: OpenApiResponse(description="Target not found."),
        },
        description=(
            "List the READY attachments for a target the caller can read. Both "
            "`target_type` and `target_id` are required query params."
        ),
    )
    def list(self, request):
        target_type = request.query_params.get("target_type")
        target_id = request.query_params.get("target_id")
        if not target_type or not target_id:
            raise ValidationError(
                {
                    "code": "missing_params",
                    "detail": _("target_type and target_id are required."),
                }
            )
        try:
            target_id = int(target_id)
        except (TypeError, ValueError):
            raise ValidationError(
                {"code": "missing_params", "detail": _("target_id must be an integer.")}
            )

        target = resolve_target(target_type, target_id)
        if target is None:
            raise NotFound(_("Target not found."))
        if not can_read_target(request.user, target):
            raise PermissionDenied(_("You may not access this target."))

        content_type = content_type_for_label(target_type)
        qs = (
            Attachment.objects.filter(
                content_type=content_type,
                object_id=target.pk,
                status=Attachment.READY,
            )
            .select_related("uploaded_by")
            .order_by("-created_at")
        )

        page = self.paginate_queryset(qs)
        if page is not None:
            return self.get_paginated_response(AttachmentSerializer(page, many=True).data)
        return Response(AttachmentSerializer(qs, many=True).data)

    # ---- 5. delete --------------------------------------------------------

    @extend_schema(
        responses={
            204: OpenApiResponse(description="Attachment deleted."),
            403: OpenApiResponse(description="Not allowed to delete this attachment."),
            404: OpenApiResponse(description="Attachment not found."),
            503: OpenApiResponse(description="Attachments unconfigured (`attachments_unconfigured`)."),
        },
        description=(
            "Delete an attachment: best-effort removal of the S3 object then the "
            "row. Caller must be the uploader or able to write the target."
        ),
    )
    def destroy(self, request, pk=None):
        if not s3.attachments_configured():
            return _unconfigured_response()

        attachment = self._get_attachment(pk)
        target = attachment.content_object
        if not (
            attachment.uploaded_by_id == request.user.pk
            or can_write_target(request.user, target)
        ):
            raise PermissionDenied(_("You may not delete this attachment."))

        # Best-effort S3 delete: if it fails we still drop the row (and log) so a
        # storage hiccup never strands an orphan row the user can't remove. The
        # bucket's incomplete-MPU lifecycle rule covers truly orphaned objects.
        try:
            s3.delete_object(attachment.s3_key)
        except Exception:  # noqa: BLE001 - best-effort cleanup
            logger.exception("S3 delete failed for attachment %s (key=%s)", attachment.pk, attachment.s3_key)

        # Derive the target's team defensively for audit scoping; fall back to
        # None if the target shape is unexpected.
        audit_team = None
        try:
            if target is not None:
                if getattr(target, "refer_program", None) is not None:
                    audit_team = target.refer_program.team
                elif getattr(target, "topic", None) is not None:
                    audit_team = target.topic.team
        except Exception:  # noqa: BLE001 - audit scoping must never block the delete
            audit_team = None

        att_id, att_filename = attachment.pk, attachment.filename
        attachment.delete()

        # Audit the deletion (best-effort; never breaks the action).
        from audit.models import AuditAction
        from audit.services import record

        record(
            AuditAction.ATTACHMENT_DELETED,
            actor=request.user,
            team=audit_team,
            target_repr=f"Attachment #{att_id} ({att_filename})",
            request=request,
        )

        return Response(status=status.HTTP_204_NO_CONTENT)
