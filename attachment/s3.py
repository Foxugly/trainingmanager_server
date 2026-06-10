"""S3 helpers for presigned file attachments.

All boto3 access goes through ``_client()``, which constructs an S3 client with
NO explicit credentials: boto3's default credential chain resolves to the EC2
instance role in prod (OPERATIONS.md §3.10 / §3.11). Never put AWS keys here or
in settings.

These are pure functions over the configured bucket; permission checks live in
``attachment.permissions`` and the views, not here.
"""

import re
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from django.conf import settings


def attachments_configured() -> bool:
    """True when a target bucket is configured (feature enabled)."""
    return bool(settings.ATTACHMENTS_S3_BUCKET)


def _client():
    """An S3 client using the default credential chain (instance role in prod).

    Forces SigV4 + virtual-hosted addressing so presigned URLs point at the
    REGIONAL endpoint (``<bucket>.s3.<region>.amazonaws.com``). Without this,
    boto3 may emit a legacy SigV2 URL against the global ``s3.amazonaws.com``
    host; for a non-us-east-1 bucket the browser PUT then hits a redirect that
    breaks CORS (net::ERR_FAILED). SigV4 regional URLs work with the bucket CORS.
    """
    return boto3.client(
        "s3",
        region_name=settings.ATTACHMENTS_S3_REGION,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "virtual"},
        ),
    )


# Keep alphanumerics, dash, underscore, dot; collapse everything else.
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
_MAX_FILENAME_LEN = 200


def sanitize_filename(filename: str) -> str:
    """Strip path separators and dangerous chars from a client filename.

    Keeps alnum / dash / underscore / dot; spaces become underscores; any other
    run of characters is collapsed to a single underscore. The basename only
    (no directory components) is kept, length-capped, and never empty.
    """
    # Drop any directory component the client may have sent.
    base = filename.replace("\\", "/").split("/")[-1]
    base = base.strip().replace(" ", "_")
    base = _SAFE_CHARS.sub("_", base)
    base = base.strip("._") or "file"
    return base[:_MAX_FILENAME_LEN]


def build_key(content_type_label: str, object_id, filename: str) -> str:
    """Build a unique, collision-proof S3 object key for an upload.

    Layout: ``attachments/<label>/<object_id>/<uuid4hex>/<safe_filename>`` — the
    uuid segment guarantees uniqueness even for identical filenames on the same
    target, so the model's unique(s3_key) never collides in practice.
    """
    safe = sanitize_filename(filename)
    return f"attachments/{content_type_label}/{object_id}/{uuid.uuid4().hex}/{safe}"


def presign_put(key: str, content_type: str, expires: int) -> str:
    """Presigned URL the browser PUTs the file bytes to (with Content-Type)."""
    return _client().generate_presigned_url(
        "put_object",
        Params={
            "Bucket": settings.ATTACHMENTS_S3_BUCKET,
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=expires,
    )


def presign_get(key: str, filename: str, expires: int, content_type: str | None = None) -> str:
    """Presigned URL the browser GETs the file from, forcing a download name.

    Pins ``ResponseContentType`` to the attachment's declared MIME (when given)
    so the browser can't be coerced into sniffing the bytes into an active type;
    combined with the ``attachment`` disposition the file is always downloaded,
    never rendered inline.
    """
    params = {
        "Bucket": settings.ATTACHMENTS_S3_BUCKET,
        "Key": key,
        "ResponseContentDisposition": f'attachment; filename="{filename}"',
    }
    if content_type:
        params["ResponseContentType"] = content_type
    return _client().generate_presigned_url(
        "get_object",
        Params=params,
        ExpiresIn=expires,
    )


def head_object(key: str):
    """Return the HEAD of the object (incl. ContentLength) or None if 404."""
    try:
        return _client().head_object(Bucket=settings.ATTACHMENTS_S3_BUCKET, Key=key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in ("404", "NoSuchKey", "NotFound") or status == 404:
            return None
        raise


def delete_object(key: str) -> None:
    """Delete the object from the bucket (no-op-safe; S3 delete is idempotent)."""
    _client().delete_object(Bucket=settings.ATTACHMENTS_S3_BUCKET, Key=key)
