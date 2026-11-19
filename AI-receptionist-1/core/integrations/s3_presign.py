"""
Presigned S3 GET URLs (AWS Signature Version 4, query-string auth) — for call recording playback
in the dashboard (Roadmap Task 2.10). Standard library only; works with AWS S3 and S3-compatible
stores (R2, MinIO) via RECORDING_S3_ENDPOINT (path-style URLs).
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote, urlparse

from config import settings


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def presign_get(key: str, **kw: object) -> str:
    """A time-limited GET URL for one object (dashboard playback)."""
    return presign(key, method="GET", **kw)  # type: ignore[arg-type]


def presign(
    key: str,
    *,
    method: str = "GET",
    bucket: Optional[str] = None,
    expires: int = 600,
    now: Optional[datetime] = None,
    region: Optional[str] = None,
    endpoint: Optional[str] = None,
    access_key: Optional[str] = None,
    secret_key: Optional[str] = None,
    host: Optional[str] = None,
) -> str:
    bucket = bucket or settings.RECORDING_S3_BUCKET
    region = region or settings.RECORDING_S3_REGION or "us-east-1"
    endpoint = endpoint if endpoint is not None else settings.RECORDING_S3_ENDPOINT
    access_key = access_key or settings.RECORDING_S3_ACCESS_KEY
    secret_key = secret_key or settings.RECORDING_S3_SECRET
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    amz_date, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")

    key_path = "/" + quote(key.lstrip("/"), safe="/~")
    if endpoint:                                        # path-style: https://endpoint/bucket/key
        parsed = urlparse(endpoint)
        scheme, host = parsed.scheme or "https", host or parsed.netloc
        path = f"/{quote(bucket)}{key_path}"
    else:                                               # virtual-hosted AWS
        scheme, host = "https", host or f"{bucket}.s3.{region}.amazonaws.com"
        path = key_path

    scope = f"{day}/{region}/s3/aws4_request"
    params = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(max(1, min(expires, 604800))),
        "X-Amz-SignedHeaders": "host",
    }
    query = "&".join(f"{quote(k, safe='~')}={quote(v, safe='~')}" for k, v in sorted(params.items()))
    canonical = "\n".join([method.upper(), path, query, f"host:{host}", "", "host", "UNSIGNED-PAYLOAD"])
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    k = _sign(_sign(_sign(_sign(f"AWS4{secret_key}".encode(), day), region), "s3"), "aws4_request")
    signature = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"{scheme}://{host}{path}?{query}&X-Amz-Signature={signature}"
