"""Authentication for the RockBLOCK webhook.

The webhook is the only unauthenticated write path in the app, so this
module enforces a strict fail-closed policy:

- ``WEBHOOK_SHARED_SECRET`` MUST be set or the endpoint returns 503 for every
  request. This prevents a misconfigured deployment from silently accepting
  writes from anyone.
- If ``ROCKBLOCK_ALLOWED_IPS`` is set (comma-separated), the client IP (from
  X-Forwarded-For behind DO's proxy) must be in the allowlist.
- If ``ROCKBLOCK_JWT_PUBLIC_KEY`` is set **and** the post includes a ``JWT``
  field, that token is verified as RS256. Ground Control's form-encoded
  ``HTTP_POST`` delivery does not send ``JWT`` (JSON delivery does), so a
  missing token is allowed; a present but invalid token is rejected.

Ground Control's RockBLOCK webhook system does NOT let you attach a custom
``Authorization`` header, so the shared secret is accepted in either of two
places:

1. Preferred: ``?secret=<WEBHOOK_SHARED_SECRET>`` on the delivery URL.
2. ``Authorization: Bearer <WEBHOOK_SHARED_SECRET>`` header (useful when
   fronting the webhook with a proxy).
"""

import hmac
import os
from typing import Optional

from fastapi import HTTPException, Request
from loguru import logger


def _client_ip(request: Request) -> str:
    """Return the caller's IP, taking X-Forwarded-For into account."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


def _extract_bearer(authorization_header: Optional[str]) -> str:
    if not authorization_header:
        return ""
    parts = authorization_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return ""
    return parts[1].strip()


def verify_rockblock_webhook(
    *,
    request: Request,
    authorization_header: Optional[str],
    jwt_token: Optional[str],
) -> None:
    """Raise ``HTTPException`` unless the request is a legitimate RockBLOCK post."""
    shared_secret = os.getenv("WEBHOOK_SHARED_SECRET")
    if not shared_secret:
        logger.error(
            "WEBHOOK_SHARED_SECRET not configured; refusing webhook traffic"
        )
        raise HTTPException(status_code=503, detail="webhook not configured")

    client_ip = _client_ip(request)

    # Accept the secret from either the Authorization header or a ?secret=
    # query parameter (Ground Control's portal only supports the latter).
    provided = _extract_bearer(authorization_header) or (
        request.query_params.get("secret") or ""
    )
    if not hmac.compare_digest(provided, shared_secret):
        logger.warning(
            f"Rejected webhook: bad or missing shared secret (ip={client_ip})"
        )
        raise HTTPException(status_code=401, detail="unauthorized")

    allowed_ips_raw = os.getenv("ROCKBLOCK_ALLOWED_IPS", "").strip()
    if allowed_ips_raw:
        allowed = {ip.strip() for ip in allowed_ips_raw.split(",") if ip.strip()}
        if client_ip not in allowed:
            logger.warning(
                f"Rejected webhook: source ip {client_ip} not in allowlist"
            )
            raise HTTPException(status_code=403, detail="forbidden source")

    jwt_public_key = os.getenv("ROCKBLOCK_JWT_PUBLIC_KEY", "").strip()
    if jwt_public_key and jwt_token:
        try:
            import jwt as pyjwt  # imported lazily so tests without JWT still work
        except ImportError as e:  # pragma: no cover -- deployment misconfig
            logger.error(f"ROCKBLOCK_JWT_PUBLIC_KEY set but PyJWT missing: {e}")
            raise HTTPException(status_code=503, detail="jwt not available")
        try:
            pyjwt.decode(jwt_token, jwt_public_key, algorithms=["RS256"])
        except Exception as e:
            logger.warning(f"Rejected webhook: JWT verification failed: {e}")
            raise HTTPException(status_code=401, detail="invalid JWT")
