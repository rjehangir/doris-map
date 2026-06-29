"""Thin Resend HTTP client.

Reads ``RESEND_API_KEY`` and ``EMAIL_FROM`` from the environment. All ``send_*``
helpers swallow exceptions and log failures: notifications must never break the
RockBLOCK webhook ingestion path.
"""

from __future__ import annotations

import os
from typing import Iterable, Optional

import requests
from loguru import logger

import models
from email_templates import (
    build_digest_email,
    build_realtime_email,
    build_unsubscribed_email,
    build_verification_email,
)

RESEND_API_URL = "https://api.resend.com/emails"
REQUEST_TIMEOUT = 10


def _api_key() -> Optional[str]:
    return os.getenv("RESEND_API_KEY")


def _from_address() -> str:
    return os.getenv("EMAIL_FROM", "DORIS Tracker <notifications@example.com>")


def _app_base_url() -> str:
    return os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")


def _unsubscribe_mailto() -> Optional[str]:
    return os.getenv("UNSUBSCRIBE_MAILTO")  # optional; e.g. "unsubscribe@yourdomain"


def _post(
    to: str,
    subject: str,
    html: str,
    text: str,
    *,
    unsubscribe_url: Optional[str] = None,
) -> bool:
    """Send a single email via Resend. Returns True on apparent success."""
    api_key = _api_key()
    if not api_key:
        logger.warning(
            "RESEND_API_KEY not set; skipping email to {} (subject={!r})", to, subject
        )
        return False

    payload = {
        "from": _from_address(),
        "to": [to],
        "subject": subject,
        "html": html,
        "text": text,
    }

    headers_extra = {}
    if unsubscribe_url:
        mailto = _unsubscribe_mailto()
        if mailto:
            headers_extra["List-Unsubscribe"] = (
                f"<mailto:{mailto}>, <{unsubscribe_url}>"
            )
        else:
            headers_extra["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        headers_extra["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    if headers_extra:
        payload["headers"] = headers_extra

    try:
        resp = requests.post(
            RESEND_API_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code >= 400:
            logger.error(
                "Resend send failed status={} body={} to={} subject={!r}",
                resp.status_code,
                resp.text[:500],
                to,
                subject,
            )
            return False
        logger.info("Sent email to {} subject={!r}", to, subject)
        return True
    except requests.RequestException as e:
        logger.error("Resend request error to={} subject={!r}: {}", to, subject, e)
        return False


# ── Public helpers ──


def send_verification(subscriber: models.Subscriber, verify_token: str) -> bool:
    subject, html, text = build_verification_email(
        subscriber=subscriber,
        verify_token=verify_token,
        base_url=_app_base_url(),
    )
    return _post(subscriber.email, subject, html, text)


def send_realtime(
    subscriber: models.Subscriber,
    sub: models.Subscription,
    device: models.Device,
    message: models.DorisMessage,
    *,
    place_label: Optional[str] = None,
    unsubscribe_token: Optional[str] = None,
) -> bool:
    base = _app_base_url()
    unsub_url = (
        f"{base}/api/unsubscribe?token={unsubscribe_token}"
        if unsubscribe_token
        else None
    )
    subject, html, text = build_realtime_email(
        subscriber=subscriber,
        sub=sub,
        device=device,
        message=message,
        place_label=place_label,
        base_url=base,
        unsubscribe_url=unsub_url,
    )
    return _post(
        subscriber.email,
        subject,
        html,
        text,
        unsubscribe_url=unsub_url,
    )


def send_digest(
    subscriber: models.Subscriber,
    rollups: Iterable[dict],
    *,
    frequency: str,
    unsubscribe_token: Optional[str] = None,
) -> bool:
    base = _app_base_url()
    unsub_url = (
        f"{base}/api/unsubscribe?token={unsubscribe_token}"
        if unsubscribe_token
        else None
    )
    rollup_list = list(rollups)
    if not rollup_list:
        return False
    subject, html, text = build_digest_email(
        subscriber=subscriber,
        rollups=rollup_list,
        frequency=frequency,
        base_url=base,
        unsubscribe_url=unsub_url,
    )
    return _post(
        subscriber.email,
        subject,
        html,
        text,
        unsubscribe_url=unsub_url,
    )


def send_unsubscribed_confirmation(subscriber: models.Subscriber) -> bool:
    subject, html, text = build_unsubscribed_email(
        subscriber=subscriber, base_url=_app_base_url()
    )
    return _post(subscriber.email, subject, html, text)
