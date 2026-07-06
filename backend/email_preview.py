"""Dev-only email preview endpoint.

Renders any of the four notification templates in a browser so you can
iterate on markup without sending real email. Uses fake subscriber data
and fake tokens; if a real IMEI (and optionally message id) is supplied
it will pull that message from the DB, otherwise it falls back to fully
synthetic sample data.

Enable by setting ``ENABLE_EMAIL_PREVIEW=1`` (or leave the default: on
whenever ``APP_BASE_URL`` points at localhost).
"""

from __future__ import annotations

import html as html_lib
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy.orm import Session

import crud
import email_templates
import models

router = APIRouter()

VARIANTS = ("verify", "realtime", "realtime_leak", "digest", "unsubscribed")


def _is_enabled() -> bool:
    flag = os.getenv("ENABLE_EMAIL_PREVIEW")
    if flag is not None:
        return flag.lower() in ("1", "true", "yes", "on")
    return "localhost" in os.getenv("APP_BASE_URL", "http://localhost:8000")


def _base_url() -> str:
    return os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")


def _fake_subscriber() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        email="preview@example.com",
        manage_token="FAKE-MANAGE-TOKEN-abc123",
        verified_at=datetime.now(timezone.utc),
        unsubscribed_at=None,
        subscriptions=[],
        tokens=[],
    )


def _fake_device() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        imei="300234010753370",
        name="DORIS 3",
    )


def _fake_message(
    *,
    leak: bool = False,
    msg_id: int = 4242,
    imei: str = "300234010753370",
    lat: float = 21.432841,
    lon: float = -157.789464,
    battery: Optional[float] = None,
    depth: Optional[float] = None,
    momsn: int = 987,
    transmit_time: str = "26-07-05 22:14:03",
    created_at: Optional[datetime] = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=msg_id,
        device_imei=imei,
        momsn=momsn,
        transmit_time=transmit_time,
        iridium_latitude=lat + 0.001,
        iridium_longitude=lon - 0.001,
        iridium_cep=3,
        latitude=lat,
        longitude=lon,
        altitude=12.7,
        satellite_count=6,
        battery_voltage=battery if battery is not None else (11.85 if leak else 13.42),
        leak_detected=leak,
        max_depth=depth if depth is not None else (34.1 if leak else 28.6),
        raw_data="00",
        created_at=created_at or datetime.now(timezone.utc),
    )


def _fake_subscription() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        subscriber_id=1,
        device_imei="300234010753370",
        wants_realtime=True,
        wants_digest=True,
        digest_frequency="daily",
        digest_hour_utc=13,
        realtime_throttle_minutes=60,
        last_realtime_sent_at=None,
        last_digest_sent_at=None,
    )


def _fake_rollups() -> list[dict]:
    now = datetime.now(timezone.utc)
    dev1 = _fake_device()
    dev2 = SimpleNamespace(id=2, imei="300234010753371", name="DORIS 4")
    dev3 = SimpleNamespace(id=3, imei="300234010753372", name="DORIS 5")
    return [
        {
            "device": dev1,
            "count": 14,
            "first_msg": _fake_message(
                imei=dev1.imei, msg_id=4229,
                lat=21.4200, lon=-157.7912,
                created_at=now - timedelta(hours=23),
            ),
            "last_msg": _fake_message(
                imei=dev1.imei, msg_id=4242,
                lat=21.4331, lon=-157.7898,
                created_at=now - timedelta(hours=2, minutes=14),
            ),
            "leak_events": 0,
            "min_battery": 13.10,
            "max_battery": 13.68,
            "max_depth": 28.6,
            "place_label": "near Kaneohe Bay",
        },
        {
            "device": dev2,
            "count": 3,
            "first_msg": _fake_message(
                imei=dev2.imei, msg_id=1101,
                lat=21.3320, lon=-157.6900,
                created_at=now - timedelta(hours=19),
            ),
            "last_msg": _fake_message(
                imei=dev2.imei, msg_id=1104,
                lat=21.3355, lon=-157.6912,
                created_at=now - timedelta(hours=6, minutes=42),
            ),
            "leak_events": 0,
            "min_battery": 14.12,
            "max_battery": 14.24,
            "max_depth": 5.1,
            "place_label": "near Waimanalo Bay",
        },
        {
            "device": dev3,
            "count": 27,
            "first_msg": _fake_message(
                imei=dev3.imei, msg_id=880, leak=True,
                lat=21.4085, lon=-158.1650,
                created_at=now - timedelta(hours=22),
            ),
            "last_msg": _fake_message(
                imei=dev3.imei, msg_id=906, leak=True,
                lat=21.4102, lon=-158.1655,
                battery=11.20, depth=34.1,
                created_at=now - timedelta(minutes=18),
            ),
            "leak_events": 4,
            "min_battery": 10.90,
            "max_battery": 12.30,
            "max_depth": 34.1,
            "place_label": "near Ma\u02bbili Point",
        },
    ]


def _load_real(db: Session, imei: str, msg_id: Optional[int]):
    device = crud.get_device_by_imei(db, imei)
    if not device:
        raise HTTPException(status_code=404, detail=f"no device {imei}")
    if msg_id is not None:
        msg = (
            db.query(models.DorisMessage)
            .filter(
                models.DorisMessage.id == msg_id,
                models.DorisMessage.device_imei == imei,
            )
            .first()
        )
    else:
        msg = crud.get_latest_message_per_device(db, imei)
    if not msg:
        raise HTTPException(status_code=404, detail="no message for that device")
    return device, msg


def _render(variant: str, subject: str, html: str, text: str, query_string: str) -> str:
    nav_links = []
    for v in VARIANTS:
        cls = "active" if v == variant else ""
        nav_links.append(
            f'<a class="chip {cls}" href="/email-preview?type={v}">{v}</a>'
        )
    for fmt in ("html", "text"):
        cls = "active" if f"format={fmt}" in query_string or (
            fmt == "html" and "format=" not in query_string
        ) else ""
        base_qs = "&".join(
            p for p in query_string.split("&") if p and not p.startswith("format=")
        )
        href = "/email-preview?" + (base_qs + "&" if base_qs else "") + f"format={fmt}"
        nav_links.append(f'<a class="chip {cls}" href="{href}">as {fmt}</a>')

    body_frame = (
        f"<iframe srcdoc=\"{html_lib.escape(html, quote=True)}\" "
        f'style="width:100%; height: calc(100vh - 130px); border: none; background:#fff;"></iframe>'
        if "format=text" not in query_string
        else f'<pre style="margin:0; padding:20px; white-space:pre-wrap; '
        f'font-family: ui-monospace, monospace; font-size: 13px; '
        f'background:#0E2446; color:#96EEF2; min-height: calc(100vh - 130px);">'
        f"{html_lib.escape(text)}</pre>"
    )

    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Email preview: {html_lib.escape(variant)}</title>
<style>
  html, body {{ margin: 0; padding: 0; background: #13315C; color: #fff; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
  header {{ padding: 12px 16px; background: #0E2446; border-bottom: 1px solid rgba(65,185,195,0.25); }}
  header h1 {{ font-size: 0.9rem; margin: 0 0 8px; color: #96EEF2; font-weight: 500; }}
  header .subject {{ font-size: 0.82rem; color: #FFF8A7; margin-bottom: 8px; word-break: break-word; }}
  .chips {{ display: flex; flex-wrap: wrap; gap: 6px; }}
  .chip {{ display: inline-block; padding: 4px 10px; border-radius: 999px;
           background: rgba(65,185,195,0.15); color: #96EEF2; text-decoration: none;
           font-size: 0.78rem; border: 1px solid rgba(65,185,195,0.25); }}
  .chip:hover {{ background: rgba(65,185,195,0.28); }}
  .chip.active {{ background: #41B9C3; color: #0E2446; border-color: transparent; }}
</style></head>
<body>
<header>
  <h1>Email preview &middot; <code>{html_lib.escape(variant)}</code></h1>
  <div class="subject"><strong>Subject:</strong> {html_lib.escape(subject)}</div>
  <div class="chips">{''.join(nav_links)}</div>
</header>
{body_frame}
</body></html>"""


def _render_html(variant: str, subject: str, html: str, text: str, qs: str):
    return HTMLResponse(_render(variant, subject, html, text, qs))


@router.get("/email-preview", response_class=HTMLResponse)
async def email_preview(
    type: str = Query("realtime", pattern="^(verify|realtime|realtime_leak|digest|unsubscribed)$"),
    imei: Optional[str] = None,
    msg_id: Optional[int] = None,
    format: str = Query("html", pattern="^(html|text)$"),
):
    if not _is_enabled():
        raise HTTPException(
            status_code=404,
            detail="Email preview disabled. Set ENABLE_EMAIL_PREVIEW=1 to enable.",
        )

    # Late-import to avoid circular import at module load
    from database import SessionLocal

    real_db = SessionLocal()
    try:
        subscriber = _fake_subscriber()
        base = _base_url()

        qs_parts = [f"type={type}", f"format={format}"]
        if imei:
            qs_parts.append(f"imei={imei}")
        if msg_id is not None:
            qs_parts.append(f"msg_id={msg_id}")
        qs = "&".join(qs_parts)

        if type == "verify":
            subject, html, text = email_templates.build_verification_email(
                subscriber=subscriber,
                verify_token="FAKE-VERIFY-TOKEN-xyz789",
                base_url=base,
            )

        elif type in ("realtime", "realtime_leak"):
            leak = type == "realtime_leak"
            if imei:
                device, message = _load_real(real_db, imei, msg_id)
                # Override leak flag if the URL asked for the leak variant
                if leak:
                    message.leak_detected = True
            else:
                device = _fake_device()
                message = _fake_message(leak=leak)
            place = "near Kaneohe Bay" if not imei else None
            subject, html, text = email_templates.build_realtime_email(
                subscriber=subscriber,
                sub=_fake_subscription(),
                device=device,
                message=message,
                place_label=place,
                base_url=base,
                unsubscribe_url=f"{base}/api/unsubscribe?token=FAKE-UNSUB-TOKEN",
            )

        elif type == "digest":
            subject, html, text = email_templates.build_digest_email(
                subscriber=subscriber,
                rollups=_fake_rollups(),
                frequency="daily",
                base_url=base,
                unsubscribe_url=f"{base}/api/unsubscribe?token=FAKE-UNSUB-TOKEN",
            )

        else:  # unsubscribed
            subject, html, text = email_templates.build_unsubscribed_email(
                subscriber=subscriber, base_url=base
            )

        if format == "text":
            return HTMLResponse(_render(type, subject, html, text, qs))
        return HTMLResponse(_render(type, subject, html, text, qs))
    finally:
        real_db.close()


@router.get("/email-preview/raw", response_class=HTMLResponse)
async def email_preview_raw(
    type: str = Query("realtime", pattern="^(verify|realtime|realtime_leak|digest|unsubscribed)$"),
    imei: Optional[str] = None,
    msg_id: Optional[int] = None,
    format: str = Query("html", pattern="^(html|text)$"),
):
    """Return only the rendered email body (no preview chrome).

    Handy for copying the HTML source or testing rendering in an isolated tab.
    """
    if not _is_enabled():
        raise HTTPException(status_code=404, detail="Email preview disabled.")

    from database import SessionLocal

    real_db = SessionLocal()
    try:
        subscriber = _fake_subscriber()
        base = _base_url()

        if type == "verify":
            subject, html, text = email_templates.build_verification_email(
                subscriber=subscriber,
                verify_token="FAKE-VERIFY-TOKEN-xyz789",
                base_url=base,
            )
        elif type in ("realtime", "realtime_leak"):
            leak = type == "realtime_leak"
            if imei:
                device, message = _load_real(real_db, imei, msg_id)
                if leak:
                    message.leak_detected = True
            else:
                device = _fake_device()
                message = _fake_message(leak=leak)
            subject, html, text = email_templates.build_realtime_email(
                subscriber=subscriber,
                sub=_fake_subscription(),
                device=device,
                message=message,
                place_label="near Kaneohe Bay" if not imei else None,
                base_url=base,
                unsubscribe_url=f"{base}/api/unsubscribe?token=FAKE-UNSUB-TOKEN",
            )
        elif type == "digest":
            subject, html, text = email_templates.build_digest_email(
                subscriber=subscriber,
                rollups=_fake_rollups(),
                frequency="daily",
                base_url=base,
                unsubscribe_url=f"{base}/api/unsubscribe?token=FAKE-UNSUB-TOKEN",
            )
        else:
            subject, html, text = email_templates.build_unsubscribed_email(
                subscriber=subscriber, base_url=base
            )

        if format == "text":
            return PlainTextResponse(text)
        return HTMLResponse(html)
    finally:
        real_db.close()
