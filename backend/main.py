#! /usr/bin/env python3

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

import uvicorn
from fastapi import BackgroundTasks, Depends, FastAPI, Form, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.responses import Response

import email_preview
import email_service
import models
import notifications
from crud import (
    ALL_UNITS_IMEI,
    create_dive_start,
    create_doris_message,
    create_verify_token,
    delete_dive_start,
    drop_covered_device_subscriptions,
    get_all_devices,
    get_device_by_imei,
    get_device_messages,
    get_dive_starts,
    get_latest_message_per_device,
    get_location_labels_batch,
    get_messages_paginated,
    get_or_create_subscriber,
    get_recent_messages,
    get_subscriber_by_email,
    get_subscriber_by_manage_token,
    get_unsubscribe_token,
    get_verify_token,
    is_all_units_imei,
    mark_unsubscribed,
    replace_subscriptions,
    set_user_location_label,
    update_dive_start,
    upsert_subscription,
)
from database import SessionLocal, engine
from schemas import (
    DiveStartCreate,
    DiveStartResponse,
    DiveStartUpdate,
    DorisMessageResponse,
    GeocodeBatchRequest,
    GeocodeOverrideRequest,
    LocationLabelResponse,
    ManageLinkRequest,
    RockblockWebhookIn,
    SubscribeRequest,
    SubscriptionItem,
    SubscriptionsResponse,
    SubscriptionsUpdate,
)
from security import verify_rockblock_webhook


class PrettyJSONResponse(Response):
    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        return json.dumps(
            content,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            separators=(", ", ": "),
            default=str,
        ).encode(self.charset)


models.Base.metadata.create_all(bind=engine)


def migrate_devices_json():
    """One-time migration: import devices.json into the devices table."""
    path = os.path.join(os.path.dirname(__file__), "devices.json")
    if not os.path.exists(path):
        return
    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Could not read {path}: {e}")
        return

    db = SessionLocal()
    try:
        added = 0
        for imei, info in data.items():
            if not get_device_by_imei(db, imei):
                db.add(models.Device(imei=imei, name=info.get("name", imei)))
                added += 1
        if added:
            db.commit()
            logger.info(f"Migrated {added} device(s) from devices.json to database")
        else:
            logger.info("All devices from devices.json already in database, skipping")
    finally:
        db.close()


migrate_devices_json()


def migrate_dive_start_columns():
    """Add name and notes columns to dive_starts if missing."""
    from sqlalchemy import inspect as sa_inspect, text
    insp = sa_inspect(engine)
    if "dive_starts" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("dive_starts")}
    with engine.begin() as conn:
        if "name" not in cols:
            conn.execute(text("ALTER TABLE dive_starts ADD COLUMN name VARCHAR"))
            logger.info("Added 'name' column to dive_starts")
        if "notes" not in cols:
            conn.execute(text("ALTER TABLE dive_starts ADD COLUMN notes VARCHAR"))
            logger.info("Added 'notes' column to dive_starts")


migrate_dive_start_columns()


def migrate_subscription_tables():
    """Ensure subscription tables exist on existing deployments."""
    from sqlalchemy import inspect as sa_inspect

    insp = sa_inspect(engine)
    existing = set(insp.get_table_names())
    needed = {"subscribers", "subscriptions", "email_tokens"}
    missing = needed - existing
    if missing:
        # create_all is idempotent and only creates tables that don't exist
        models.Base.metadata.create_all(bind=engine, tables=[
            t for t in models.Base.metadata.sorted_tables if t.name in missing
        ])
        logger.info(f"Created subscription tables: {sorted(missing)}")


migrate_subscription_tables()


def migrate_doris_message_p1_columns():
    """Add P/1 telemetry columns and drop altitude/sat/leak on existing DBs."""
    from sqlalchemy import inspect as sa_inspect, text

    insp = sa_inspect(engine)
    if "doris_messages" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("doris_messages")}
    adds = [
        ("message_type", "VARCHAR"),
        ("message_version", "VARCHAR"),
        ("velocity_dm_s", "INTEGER"),
        ("course_deg", "INTEGER"),
        ("status_flags", "INTEGER"),
    ]
    drops = ("altitude", "satellite_count", "leak_detected")
    with engine.begin() as conn:
        for name, typ in adds:
            if name not in cols:
                conn.execute(text(f"ALTER TABLE doris_messages ADD COLUMN {name} {typ}"))
                logger.info(f"Added '{name}' column to doris_messages")
        for name in drops:
            if name in cols:
                conn.execute(text(f"ALTER TABLE doris_messages DROP COLUMN {name}"))
                logger.info(f"Dropped '{name}' column from doris_messages")


migrate_doris_message_p1_columns()


def migrate_all_units_subscription_token():
    """Rewrite legacy NULL/empty 'all units' rows to device_imei='all'.

    SQL NULL is a poor sentinel: PostgreSQL unique constraints treat NULLs as
    distinct, and dispatch matching against a real IMEI never equals NULL
    unless the query explicitly uses IS NULL. A stored token is unambiguous.
    """
    from sqlalchemy import inspect as sa_inspect

    insp = sa_inspect(engine)
    if "subscriptions" not in insp.get_table_names():
        return
    db = SessionLocal()
    try:
        from collections import defaultdict

        converted = 0
        deleted = 0
        by_subscriber: dict[int, list] = defaultdict(list)
        for row in db.query(models.Subscription).all():
            if is_all_units_imei(row.device_imei):
                by_subscriber[row.subscriber_id].append(row)
        for group in by_subscriber.values():
            keep = next(
                (r for r in group if r.device_imei == ALL_UNITS_IMEI),
                None,
            )
            if keep is None:
                keep = group[0]
                keep.device_imei = ALL_UNITS_IMEI
                converted += 1
            for row in group:
                if row is keep:
                    continue
                db.delete(row)
                deleted += 1
        if converted or deleted:
            db.commit()
            logger.info(
                "Normalized all-units subscriptions: "
                f"{converted} updated, {deleted} duplicate(s) removed"
            )
    except Exception as e:
        db.rollback()
        logger.warning(f"Could not normalize all-units subscriptions: {e}")
    finally:
        db.close()


migrate_all_units_subscription_token()


def prune_device_subscriptions_covered_by_all():
    """Drop per-device rows for anyone who already has an all-units subscription."""
    from sqlalchemy import inspect as sa_inspect

    insp = sa_inspect(engine)
    if "subscriptions" not in insp.get_table_names():
        return
    db = SessionLocal()
    try:
        deleted = 0
        for subscriber in db.query(models.Subscriber).all():
            deleted += drop_covered_device_subscriptions(db, subscriber)
        if deleted:
            db.commit()
            logger.info(
                f"Removed {deleted} per-device subscription(s) covered by all-units"
            )
    except Exception as e:
        db.rollback()
        logger.warning(f"Could not prune covered device subscriptions: {e}")
    finally:
        db.close()


prune_device_subscriptions_covered_by_all()

app = FastAPI(
    title="DORIS Tracker API",
    description="Multi-device camera tracking system via Iridium / RockBLOCK.",
    default_response_class=PrettyJSONResponse,
)

app.mount("/ui", StaticFiles(directory="../frontend", html=True), name="static")
app.include_router(email_preview.router)


# ── Digest scheduler ──

_scheduler = None


@app.on_event("startup")
def _start_digest_scheduler():  # pragma: no cover -- scheduler wiring
    global _scheduler
    if os.getenv("DISABLE_DIGEST_SCHEDULER", "").lower() in ("1", "true", "yes"):
        logger.info("Digest scheduler disabled via DISABLE_DIGEST_SCHEDULER")
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.warning("apscheduler not installed; skipping digest scheduler")
        return
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        notifications.dispatch_digest,
        "interval",
        minutes=15,
        id="dispatch_digest",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info("Digest scheduler started (every 15 minutes)")


@app.on_event("shutdown")
def _stop_digest_scheduler():  # pragma: no cover
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def serialize_message(orm_obj) -> dict | None:
    """Convert a DorisMessage ORM object to a JSON-safe dict via Pydantic."""
    if orm_obj is None:
        return None
    return DorisMessageResponse.model_validate(orm_obj).model_dump(mode="json")


def serialize_dive_start(orm_obj) -> dict | None:
    if orm_obj is None:
        return None
    return DiveStartResponse.model_validate(orm_obj).model_dump(mode="json")


@app.get("/")
async def root():
    return RedirectResponse(url="/ui")


@app.post("/rockblock-webhook")
async def rockblock_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    imei: str = Form(...),
    serial: str = Form(...),
    momsn: int = Form(...),
    transmit_time: str = Form(...),
    iridium_latitude: float = Form(...),
    iridium_longitude: float = Form(...),
    iridium_cep: int = Form(...),
    data: str = Form(...),
    JWT: Optional[str] = Form(None),
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db),
):
    """Receive a RockBLOCK webhook (form-encoded) from Rock Seven.

    Authentication is enforced via ``verify_rockblock_webhook``: at minimum
    the request must present ``Authorization: Bearer <WEBHOOK_SHARED_SECRET>``.
    Optional IP allowlist and JWT verification are applied if configured.
    """
    verify_rockblock_webhook(
        request=request,
        authorization_header=authorization,
        jwt_token=JWT,
    )

    try:
        payload = RockblockWebhookIn(
            imei=imei,
            serial=serial,
            momsn=momsn,
            transmit_time=transmit_time,
            iridium_latitude=iridium_latitude,
            iridium_longitude=iridium_longitude,
            iridium_cep=iridium_cep,
            data=data,
        )
    except (ValidationError, ValueError) as e:
        logger.warning(f"Rejected webhook: input validation failed: {e}")
        raise HTTPException(status_code=422, detail="invalid webhook payload")

    device = get_device_by_imei(db, payload.imei)
    if not device:
        client_ip = request.headers.get("x-forwarded-for") or (
            request.client.host if request.client else "?"
        )
        logger.warning(
            f"Rejected webhook: unregistered IMEI {payload.imei} (ip={client_ip})"
        )
        raise HTTPException(status_code=403, detail="unknown device")

    logger.info(f"Received message from {device.name} (IMEI {payload.imei})")

    try:
        message = create_doris_message(
            db=db,
            imei=payload.imei,
            momsn=payload.momsn,
            transmit_time=payload.transmit_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            iridium_latitude=payload.iridium_latitude,
            iridium_longitude=payload.iridium_longitude,
            iridium_cep=payload.iridium_cep,
            hex_data=payload.data,
        )
    except (ValueError, KeyError) as e:
        logger.error(
            f"Malformed DORIS payload from IMEI {payload.imei}: {e}"
        )
        raise HTTPException(status_code=400, detail="malformed payload")

    background_tasks.add_task(
        notifications.dispatch_realtime_for_message_id, message.id
    )
    return {"status": "ok", "id": message.id}


@app.get("/api/devices")
async def list_devices(db: Session = Depends(get_db)):
    """List all configured devices with their latest reported position."""
    devices = get_all_devices(db)
    result = []
    for device in devices:
        latest = get_latest_message_per_device(db, device.imei)
        result.append({
            "imei": device.imei,
            "name": device.name,
            "latest_message": serialize_message(latest),
        })
    return result


@app.get("/api/devices/{imei}/messages")
async def device_messages(
    imei: str,
    skip: int = 0,
    limit: int = 10000,
    since: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Get message history for a single device, optionally filtered by time."""
    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            pass
    messages = get_device_messages(db, imei, skip, limit, since=since_dt)
    return [serialize_message(m) for m in messages]


@app.get("/api/messages/recent")
async def recent_messages(hours: int = 24, db: Session = Depends(get_db)):
    """Get all messages from all devices within a time window."""
    messages = get_recent_messages(db, hours)
    return [serialize_message(m) for m in messages]


@app.get("/api/messages")
async def list_messages(
    imei: Optional[str] = None,
    page: int = 1,
    page_size: int = 100,
    db: Session = Depends(get_db),
):
    """Paginated list of all messages, sorted by date desc, optionally filtered by IMEI."""
    page = max(page, 1)
    page_size = max(1, min(page_size, 500))
    rows, total = get_messages_paginated(db, imei=imei, page=page, page_size=page_size)
    return {
        "messages": [serialize_message(m) for m in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ── Dive Starts ──


@app.post("/api/dive-starts")
async def log_dive_start(body: DiveStartCreate, db: Session = Depends(get_db)):
    """Log a dive start location for a device."""
    obj = create_dive_start(db, body)
    return serialize_dive_start(obj)


@app.get("/api/dive-starts/{imei}")
async def list_dive_starts(imei: str, db: Session = Depends(get_db)):
    """Get all dive start markers for a device."""
    starts = get_dive_starts(db, imei)
    return [serialize_dive_start(s) for s in starts]


@app.patch("/api/dive-start/{dive_start_id}")
async def patch_dive_start(dive_start_id: int, body: DiveStartUpdate, db: Session = Depends(get_db)):
    """Update dive start name and/or notes."""
    obj = update_dive_start(db, dive_start_id, body)
    if not obj:
        return {"status": "not_found"}
    return serialize_dive_start(obj)


@app.delete("/api/dive-start/{dive_start_id}")
async def remove_dive_start(dive_start_id: int, db: Session = Depends(get_db)):
    """Delete a dive start marker by ID."""
    deleted = delete_dive_start(db, dive_start_id)
    if not deleted:
        return {"status": "not_found"}
    return {"status": "ok"}


# ── Geocoding ──


@app.post("/api/geocode/lookup")
async def geocode_lookup(body: GeocodeBatchRequest, db: Session = Depends(get_db)):
    """Reverse-geocode a batch of coordinates with grid-based caching."""
    rows = get_location_labels_batch(db, body.coords)
    return {
        "labels": [LocationLabelResponse.model_validate(r).model_dump() for r in rows],
    }


@app.put("/api/geocode")
async def geocode_override(body: GeocodeOverrideRequest, db: Session = Depends(get_db)):
    """Save a user-provided label for the grid cell containing the given coords."""
    label = (body.label or "").strip()
    if not label:
        return {"status": "invalid", "error": "label cannot be empty"}
    row = set_user_location_label(db, body.latitude, body.longitude, label)
    return LocationLabelResponse.model_validate(row).model_dump()


# ── Subscriptions ──


def _app_base_url() -> str:
    return os.getenv("APP_BASE_URL", "http://localhost:8000").rstrip("/")


def _simple_html(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"""<!DOCTYPE html>
<html><head><meta charset=\"utf-8\"><title>{title}</title>
<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
          background: #0E2446; color: #fff; min-height: 100vh; margin: 0;
          display: flex; align-items: center; justify-content: center; padding: 24px; }}
  .card {{ background: #13315C; max-width: 460px; padding: 28px 28px 24px; border-radius: 12px;
           box-shadow: 0 4px 24px rgba(0,0,0,0.3); text-align: center; }}
  h1 {{ margin: 0 0 12px; font-size: 1.4rem; }}
  p {{ color: #A4B7CC; line-height: 1.55; margin: 0 0 12px; }}
  a.btn {{ display: inline-block; padding: 10px 18px; background: #41B9C3;
           color: #0E2446; font-weight: 600; border-radius: 8px;
           text-decoration: none; margin-top: 10px; }}
</style></head>
<body><div class=\"card\"><h1>{title}</h1>{body}</div></body></html>"""
    )


def _is_valid_email(email: str) -> bool:
    email = (email or "").strip()
    return "@" in email and "." in email.split("@")[-1] and len(email) <= 254


@app.post("/api/subscribe")
async def subscribe(
    body: SubscribeRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Start a subscription flow. Always sends a verify email if unverified.

    Body fields: email, imei ("all" or device IMEI), and notification preferences.
    """
    if not _is_valid_email(body.email):
        raise HTTPException(status_code=400, detail="invalid email")

    device_imei: Optional[str]
    if is_all_units_imei(body.imei):
        device_imei = ALL_UNITS_IMEI
    else:
        device_imei = body.imei
        if not get_device_by_imei(db, device_imei):
            raise HTTPException(status_code=404, detail="unknown device")

    subscriber = get_or_create_subscriber(db, body.email)
    if subscriber.unsubscribed_at is not None:
        subscriber.unsubscribed_at = None
        db.commit()

    upsert_subscription(
        db,
        subscriber,
        device_imei=device_imei,
        wants_realtime=body.wants_realtime,
        wants_digest=body.wants_digest,
        digest_frequency=body.digest_frequency,
        digest_hour_utc=body.digest_hour_utc,
        realtime_throttle_minutes=body.realtime_throttle_minutes,
    )

    if subscriber.verified_at is None:
        token = create_verify_token(db, subscriber)
        background_tasks.add_task(
            email_service.send_verification, subscriber, token.token
        )
        return {"status": "verify_email_sent"}
    return {"status": "subscribed"}


@app.get("/api/verify")
async def verify_subscription(token: str = Query(...), db: Session = Depends(get_db)):
    row = get_verify_token(db, token)
    if not row:
        return _simple_html(
            "Invalid verification link",
            "<p>This link is invalid or already used.</p>",
        )
    now = datetime.now(timezone.utc)
    if row.expires_at and row.expires_at.replace(tzinfo=timezone.utc) < now:
        return _simple_html(
            "Verification link expired",
            "<p>Please request a fresh confirmation by subscribing again.</p>"
            f'<a class="btn" href="{_app_base_url()}/ui">Open tracker</a>',
        )
    if row.used_at is None:
        row.used_at = now
    subscriber = row.subscriber
    if subscriber.verified_at is None:
        subscriber.verified_at = now
    if subscriber.unsubscribed_at is not None:
        subscriber.unsubscribed_at = None
    db.commit()
    return RedirectResponse(
        url=f"{_app_base_url()}/ui#subscribed=ok&token={subscriber.manage_token}",
        status_code=302,
    )


@app.get("/api/manage")
async def manage_redirect(token: str = Query(...), db: Session = Depends(get_db)):
    sub = get_subscriber_by_manage_token(db, token)
    if not sub:
        return _simple_html(
            "Invalid management link",
            "<p>This link is invalid. You can re-subscribe from the tracker.</p>"
            f'<a class="btn" href="{_app_base_url()}/ui">Open tracker</a>',
        )
    return RedirectResponse(
        url=f"{_app_base_url()}/ui#manage=1&token={token}",
        status_code=302,
    )


@app.post("/api/manage-link")
async def request_manage_link(
    body: ManageLinkRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Email the manage link to an existing verified subscriber.

    Always returns the same generic response so callers can't enumerate which
    email addresses are subscribed.
    """
    if not _is_valid_email(body.email):
        raise HTTPException(status_code=400, detail="invalid email")

    subscriber = get_subscriber_by_email(db, body.email.strip())
    if (
        subscriber
        and subscriber.verified_at is not None
        and subscriber.unsubscribed_at is None
    ):
        background_tasks.add_task(email_service.send_manage_link, subscriber)

    return {"status": "ok"}


@app.get("/api/subscriptions")
async def list_subscriptions(token: str = Query(...), db: Session = Depends(get_db)):
    subscriber = get_subscriber_by_manage_token(db, token)
    if not subscriber:
        raise HTTPException(status_code=404, detail="invalid token")
    items = []
    for s in subscriber.subscriptions:
        item = SubscriptionItem.model_validate(s)
        if is_all_units_imei(item.device_imei):
            item = item.model_copy(update={"device_imei": ALL_UNITS_IMEI})
        items.append(item)
    return SubscriptionsResponse(
        email=subscriber.email,
        verified=subscriber.verified_at is not None,
        unsubscribed=subscriber.unsubscribed_at is not None,
        subscriptions=items,
    ).model_dump()


@app.put("/api/subscriptions")
async def update_subscriptions(
    body: SubscriptionsUpdate,
    token: str = Query(...),
    db: Session = Depends(get_db),
):
    subscriber = get_subscriber_by_manage_token(db, token)
    if not subscriber:
        raise HTTPException(status_code=404, detail="invalid token")
    # Validate that referenced IMEIs exist ("all" is always allowed).
    for item in body.subscriptions:
        if item.device_imei and not is_all_units_imei(item.device_imei):
            if not get_device_by_imei(db, item.device_imei):
                raise HTTPException(
                    status_code=400, detail=f"unknown device {item.device_imei}"
                )
    replace_subscriptions(db, subscriber, body.subscriptions)
    if subscriber.unsubscribed_at is not None and body.subscriptions:
        subscriber.unsubscribed_at = None
        db.commit()
    return {"status": "ok"}


def _do_unsubscribe(token: str, db: Session) -> HTMLResponse:
    row = get_unsubscribe_token(db, token)
    subscriber = row.subscriber if row else get_subscriber_by_manage_token(db, token)
    if not subscriber:
        return _simple_html(
            "Invalid unsubscribe link",
            "<p>This link is invalid or expired.</p>",
        )
    if subscriber.unsubscribed_at is None:
        mark_unsubscribed(db, subscriber)
    return _simple_html(
        "You're unsubscribed",
        f"<p>{subscriber.email} will no longer receive DORIS notifications.</p>"
        f"<p>Changed your mind?</p>"
        f'<a class="btn" href="{_app_base_url()}/ui">Open tracker</a>',
    )


@app.get("/api/unsubscribe")
async def unsubscribe_get(token: str = Query(...), db: Session = Depends(get_db)):
    return _do_unsubscribe(token, db)


@app.post("/api/unsubscribe")
async def unsubscribe_post(token: str = Query(...), db: Session = Depends(get_db)):
    """One-click unsubscribe target for RFC 8058 List-Unsubscribe-Post."""
    return _do_unsubscribe(token, db)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
