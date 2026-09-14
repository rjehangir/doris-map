"""Subscription dispatch: realtime per-message and periodic digest.

``dispatch_realtime`` is called from the RockBLOCK webhook via FastAPI
``BackgroundTasks`` after a message is persisted. ``dispatch_digest`` is
invoked by an APScheduler interval job and walks subscriptions that are due
for their daily/weekly summary.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from loguru import logger
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

import crud
import email_service
import models
from database import SessionLocal


# ── Token helpers ──


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def get_or_create_unsubscribe_token(db: Session, subscriber: models.Subscriber) -> str:
    """Return a long-lived unsubscribe token, creating one if needed."""
    row = (
        db.query(models.EmailToken)
        .filter(
            models.EmailToken.subscriber_id == subscriber.id,
            models.EmailToken.purpose == "unsubscribe",
        )
        .first()
    )
    if row:
        return row.token
    row = models.EmailToken(
        subscriber_id=subscriber.id,
        token=_new_token(),
        purpose="unsubscribe",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row.token


# ── Realtime dispatch ──


def _matching_subscriptions(
    db: Session, imei: str
) -> list[models.Subscription]:
    """Subscriptions targeting this device (or "all units") that want realtime."""
    return (
        db.query(models.Subscription)
        .join(models.Subscriber, models.Subscription.subscriber_id == models.Subscriber.id)
        .filter(
            models.Subscription.wants_realtime.is_(True),
            models.Subscriber.verified_at.isnot(None),
            models.Subscriber.unsubscribed_at.is_(None),
            or_(
                models.Subscription.device_imei == imei,
                models.Subscription.device_imei.is_(None),
            ),
        )
        .all()
    )


def _throttled(sub: models.Subscription, now: datetime) -> bool:
    if not sub.realtime_throttle_minutes or sub.realtime_throttle_minutes <= 0:
        return False
    if sub.last_realtime_sent_at is None:
        return False
    last = sub.last_realtime_sent_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return now - last < timedelta(minutes=sub.realtime_throttle_minutes)


def _lookup_place_label(db: Session, lat: Optional[float], lon: Optional[float]) -> str:
    if lat is None or lon is None:
        return ""
    try:
        row = crud.get_or_create_location_label(db, lat, lon)
        return row.label or ""
    except Exception as e:  # pragma: no cover -- network failures handled in geocode
        logger.warning("place label lookup failed for ({}, {}): {}", lat, lon, e)
        return ""


def dispatch_realtime_for_message_id(message_id: int) -> None:
    """Background-task entry point: load message + dispatch in a fresh session."""
    db = SessionLocal()
    try:
        message = (
            db.query(models.DorisMessage)
            .filter(models.DorisMessage.id == message_id)
            .first()
        )
        if not message:
            logger.warning("dispatch_realtime: message id={} not found", message_id)
            return
        dispatch_realtime(db, message)
    except Exception as e:  # pragma: no cover - defensive
        logger.exception("dispatch_realtime failed for message_id={}: {}", message_id, e)
    finally:
        db.close()


def dispatch_realtime(db: Session, message: models.DorisMessage) -> int:
    """Send a realtime email to all matching, non-throttled subscribers.

    Returns the number of emails sent.
    """
    device = crud.get_device_by_imei(db, message.device_imei)
    if not device:
        logger.warning("dispatch_realtime: unknown device IMEI {}", message.device_imei)
        return 0

    subs = _matching_subscriptions(db, message.device_imei)
    if not subs:
        return 0

    place_label = _lookup_place_label(db, message.latitude, message.longitude)
    now = datetime.now(timezone.utc)
    sent = 0
    for sub in subs:
        if _throttled(sub, now):
            logger.debug(
                "throttled realtime for sub={} (last={}, throttle={}m)",
                sub.id,
                sub.last_realtime_sent_at,
                sub.realtime_throttle_minutes,
            )
            continue
        subscriber = sub.subscriber
        unsub_token = get_or_create_unsubscribe_token(db, subscriber)
        ok = email_service.send_realtime(
            subscriber=subscriber,
            sub=sub,
            device=device,
            message=message,
            place_label=place_label or None,
            unsubscribe_token=unsub_token,
        )
        if ok:
            sub.last_realtime_sent_at = now
            sent += 1
    if sent:
        db.commit()
    return sent


# ── Digest dispatch ──


def _digest_due(sub: models.Subscription, now: datetime) -> bool:
    if not sub.wants_digest:
        return False
    span = timedelta(days=1) if sub.digest_frequency == "daily" else timedelta(days=7)
    last = sub.last_digest_sent_at
    if last is None:
        # Never sent: due if the current UTC hour matches the configured hour.
        return now.hour == (sub.digest_hour_utc or 0)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return now - last >= span


def _devices_for_subscriber(
    db: Session, subscriber: models.Subscriber
) -> list[models.Device]:
    digest_subs = [s for s in subscriber.subscriptions if s.wants_digest]
    if not digest_subs:
        return []
    if any(s.device_imei is None for s in digest_subs):
        return crud.get_all_devices(db)
    imeis = {s.device_imei for s in digest_subs if s.device_imei}
    if not imeis:
        return []
    return (
        db.query(models.Device).filter(models.Device.imei.in_(imeis)).all()
    )


def _build_rollup(
    db: Session, device: models.Device, since: datetime
) -> Optional[dict]:
    msgs = (
        db.query(models.DorisMessage)
        .filter(
            models.DorisMessage.device_imei == device.imei,
            models.DorisMessage.created_at >= since,
        )
        .order_by(models.DorisMessage.created_at.asc())
        .all()
    )
    if not msgs:
        return None
    batteries = [m.battery_voltage for m in msgs if m.battery_voltage is not None]
    depths = [m.max_depth for m in msgs if m.max_depth is not None]
    last = msgs[-1]
    place_label = _lookup_place_label(db, last.latitude, last.longitude)
    return {
        "device": device,
        "count": len(msgs),
        "first_msg": msgs[0],
        "last_msg": last,
        "min_battery": min(batteries) if batteries else None,
        "max_battery": max(batteries) if batteries else None,
        "max_depth": max(depths) if depths else None,
        "place_label": place_label,
    }


def dispatch_digest(now: Optional[datetime] = None) -> int:
    """Scheduler entry point: send digest emails to all due subscribers.

    Returns total emails sent.
    """
    db = SessionLocal()
    sent = 0
    try:
        now = now or datetime.now(timezone.utc)
        subscribers = (
            db.query(models.Subscriber)
            .filter(
                models.Subscriber.verified_at.isnot(None),
                models.Subscriber.unsubscribed_at.is_(None),
            )
            .all()
        )
        for subscriber in subscribers:
            due_subs = [s for s in subscriber.subscriptions if _digest_due(s, now)]
            if not due_subs:
                continue
            # Use the longest span window across all due subscriptions.
            spans = [
                timedelta(days=1) if s.digest_frequency == "daily" else timedelta(days=7)
                for s in due_subs
            ]
            since = now - max(spans)

            devices = _devices_for_subscriber(db, subscriber)
            rollups = [_build_rollup(db, d, since) for d in devices]
            rollups = [r for r in rollups if r]
            if not rollups:
                # Still mark as sent so we don't re-evaluate every minute.
                for s in due_subs:
                    s.last_digest_sent_at = now
                db.commit()
                continue

            frequency = "weekly" if any(s.digest_frequency == "weekly" for s in due_subs) else "daily"
            unsub_token = get_or_create_unsubscribe_token(db, subscriber)
            ok = email_service.send_digest(
                subscriber=subscriber,
                rollups=rollups,
                frequency=frequency,
                unsubscribe_token=unsub_token,
            )
            if ok:
                for s in due_subs:
                    s.last_digest_sent_at = now
                db.commit()
                sent += 1
    except Exception as e:  # pragma: no cover -- defensive
        logger.exception("dispatch_digest crashed: {}", e)
    finally:
        db.close()
    return sent
