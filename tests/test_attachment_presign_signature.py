"""The presigned PUT signs Content-Length (audit 2026-10-18 S-M4).

Built with real botocore and dummy credentials: presigning is a local
computation, no AWS call is made.
"""

from urllib.parse import parse_qs, urlparse

from attachment import s3


def test_presign_put_signs_content_length(settings, monkeypatch):
    settings.ATTACHMENTS_S3_BUCKET = "test-bucket"
    settings.ATTACHMENTS_S3_REGION = "eu-west-1"
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.delenv("AWS_PROFILE", raising=False)

    url = s3.presign_put("attachments/x/1/abc/f.pdf", "application/pdf", 60, content_length=1234)

    signed = parse_qs(urlparse(url).query)["X-Amz-SignedHeaders"][0].split(";")
    assert "content-length" in signed
    assert "content-type" in signed
