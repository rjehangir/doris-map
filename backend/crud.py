import secrets
from datetime import datetime, timezone, timedelta
from typing import Iterable, Optional

from loguru import logger
from sqlalchemy.orm import Session

import geocode
import models
import schemas


def _parse_p1_fields(fields: list[str]) -> dict:
    """Parse ASCII fields for message type P version 1.

    Expected fields (already split, type/version included):
        P, 1, lat, lon, velocity_dm_s, course_deg, depth_m, battery_v
    """
    if len(fields) != 8:
        raise ValueError(f"P/1 payload expected 8 ASCII fields, got {len(fields)}")
    _, _, lat_s, lon_s, vel_s, course_s, depth_s, batt_s = fields
    latitude = float(lat_s)
    longitude = float(lon_s)
    if not -90.0 <= latitude <= 90.0:
        raise ValueError(f"latitude out of range: {latitude}")
    if not -180.0 <= longitude <= 180.0:
        raise ValueError(f"longitude out of range: {longitude}")
    return {
        "latitude": latitude,
        "longitude": longitude,
        "velocity_dm_s": int(vel_s),
        "course_deg": int(course_s),
        "max_depth": float(int(depth_s)),
        "battery_voltage": float(batt_s),
    }


_PAYLOAD_PARSERS = {
    ("P", "1"): _parse_p1_fields,
}


def parse_doris_payload(hex_data: str) -> dict:
    """Parse a hex-encoded DORIS SBD payload into typed values.

    P/1 layout after ``bytes.fromhex``:
        P,1,+021.43255,-157.78933,12,045,0028,14.7,<hi><lo>

    Eight ASCII CSV fields, a trailing comma, then exactly two raw flag
    bytes (big-endian uint16). Lat/lon padding and explicit signs are
    preferred but not required. Unknown type/version pairs are rejected.
    """
    raw = bytes.fromhex(hex_data)
    if len(raw) < 3 or raw[-3] != 0x2C:
        raise ValueError("payload must end with comma plus two flag bytes")

    status_flags = int.from_bytes(raw[-2:], "big")
    text = raw[:-3].decode("ascii")
    logger.debug(f"Decoded payload: {text}")

    fields = [part.strip() for part in text.split(",")]
    if len(fields) < 2:
        raise ValueError("payload missing type/version")

    msg_type, msg_version = fields[0], fields[1]
    parser = _PAYLOAD_PARSERS.get((msg_type, msg_version))
    if parser is None:
        raise ValueError(f"unsupported message type/version: {msg_type}/{msg_version}")

    parsed = parser(fields)
    parsed["message_type"] = msg_type
    parsed["message_version"] = msg_version
    parsed["status_flags"] = status_flags
    return parsed


def create_doris_message(
    db: Session,
    imei: str,
    momsn: int,
    transmit_time: str,
    iridium_latitude: float,
    iridium_longitude: float,
    iridium_cep: int,
    hex_data: str,
) -> models.DorisMessage:
    """Parse a RockBLOCK webhook payload and persist it as a DorisMessage."""
    parsed = parse_doris_payload(hex_data)

    db_message = models.DorisMessage(
        device_imei=imei,
        momsn=momsn,
        transmit_time=transmit_time,
        iridium_latitude=iridium_latitude,
        iridium_longitude=iridium_longitude,
        iridium_cep=iridium_cep,
        latitude=parsed["latitude"],
        longitude=parsed["longitude"],
        message_type=parsed["message_type"],
        message_version=parsed["message_version"],
        velocity_dm_s=parsed["velocity_dm_s"],
        course_deg=parsed["course_deg"],
        battery_voltage=parsed["battery_voltage"],
        max_depth=parsed["max_depth"],
        status_flags=parsed["status_flags"],
        raw_data=hex_data,
    )
    db.add(db_message)
    db.commit()
    db.refresh(db_message)
    logger.info(f"Stored message id={db_message.id} from IMEI {imei}")
    return db_message


def get_all_devices(db: Session):
    return db.query(models.Device).order_by(models.Device.name).all()


def get_device_by_imei(db: Session, imei: str):
    return db.query(models.Device).filter(models.Device.imei == imei).first()


def create_device(db: Session, imei: str, name: str) -> models.Device:
    device = models.Device(imei=imei, name=name)
    db.add(device)
    db.commit()
    db.refresh(device)
    return device


def auto_register_device(db: Session, imei: str) -> models.Device:
    """Create a new device with an auto-incremented name like 'New DORIS 1'."""
    count = db.query(models.Device).count()
    name = f"New DORIS {count + 1}"
    device = create_device(db, imei, name)
    logger.info(f"Auto-registered device '{name}' for IMEI {imei}")
    return device


def get_latest_message_per_device(db: Session, imei: str):
    return (
        db.query(models.DorisMessage)
        .filter(models.DorisMessage.device_imei == imei)
        .order_by(models.DorisMessage.id.desc())
        .first()
    )


def get_device_messages(
    db: Session,
    imei: str,
    skip: int = 0,
    limit: int = 10000,
    since: Optional[datetime] = None,
):
    q = db.query(models.DorisMessage).filter(
        models.DorisMessage.device_imei == imei
    )
    if since is not None:
        q = q.filter(models.DorisMessage.created_at >= since)
    return q.order_by(models.DorisMessage.id.asc()).offset(skip).limit(limit).all()


def get_recent_messages(db: Session, hours: int = 24):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    return (
        db.query(models.DorisMessage)
        .filter(models.DorisMessage.created_at >= cutoff)
        .order_by(models.DorisMessage.id.desc())
        .all()
    )


def get_messages_paginated(
    db: Session,
    imei: Optional[str] = None,
    page: int = 1,
    page_size: int = 100,
):
    q = db.query(models.DorisMessage)
    if imei:
        q = q.filter(models.DorisMessage.device_imei == imei)
    total = q.count()
    rows = (
        q.order_by(models.DorisMessage.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return rows, total


# ── Dive Starts ──


def create_dive_start(
    db: Session, data: schemas.DiveStartCreate
) -> models.DiveStart:
    obj = models.DiveStart(
        device_imei=data.device_imei,
        latitude=data.latitude,
        longitude=data.longitude,
        name=data.name,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    logger.info(
        f"Dive start id={obj.id} logged for IMEI {data.device_imei} "
        f"at ({data.latitude}, {data.longitude})"
    )
    return obj


def get_dive_starts(db: Session, imei: str):
    return (
        db.query(models.DiveStart)
        .filter(models.DiveStart.device_imei == imei)
        .order_by(models.DiveStart.id.desc())
        .all()
    )


def update_dive_start(
    db: Session, dive_start_id: int, data: schemas.DiveStartUpdate
) -> models.DiveStart | None:
    obj = db.query(models.DiveStart).filter(models.DiveStart.id == dive_start_id).first()
    if obj is None:
        return None
    if data.name is not None:
        obj.name = data.name
    if data.notes is not None:
        obj.notes = data.notes
    db.commit()
    db.refresh(obj)
    logger.info(f"Updated dive start id={dive_start_id}")
    return obj


def delete_dive_start(db: Session, dive_start_id: int) -> bool:
    obj = db.query(models.DiveStart).filter(models.DiveStart.id == dive_start_id).first()
    if obj is None:
        return False
    db.delete(obj)
    db.commit()
    logger.info(f"Deleted dive start id={dive_start_id}")
    return True


# ── Location Labels ──


def _find_location_label(db: Session, g_lat: float, g_lon: float) -> Optional[models.LocationLabel]:
    return (
        db.query(models.LocationLabel)
        .filter(
            models.LocationLabel.lat_grid == g_lat,
            models.LocationLabel.lon_grid == g_lon,
        )
        .first()
    )


def get_or_create_location_label(
    db: Session, lat: float, lon: float
) -> models.LocationLabel:
    """Return cached label for the grid cell or fetch from Nominatim and store."""
    g_lat, g_lon = geocode.grid(lat, lon)
    row = _find_location_label(db, g_lat, g_lon)
    if row is not None:
        return row

    label = geocode.nominatim_reverse(g_lat, g_lon)
    row = models.LocationLabel(
        lat_grid=g_lat,
        lon_grid=g_lon,
        label=label,
        source="auto",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    logger.info(f"Cached location label for ({g_lat}, {g_lon}): {label}")
    return row


def get_location_labels_batch(
    db: Session, coords: Iterable[tuple]
) -> list[models.LocationLabel]:
    """Resolve labels for many coordinates, deduped by grid cell.

    Cached cells are returned immediately; misses trigger rate-limited Nominatim calls.
    """
    seen: dict[tuple, models.LocationLabel] = {}
    for lat, lon in coords:
        if lat is None or lon is None:
            continue
        key = geocode.grid(lat, lon)
        if key in seen:
            continue
        seen[key] = get_or_create_location_label(db, lat, lon)
    return list(seen.values())


def set_user_location_label(
    db: Session, lat: float, lon: float, label: str
) -> models.LocationLabel:
    g_lat, g_lon = geocode.grid(lat, lon)
    row = _find_location_label(db, g_lat, g_lon)
    now = datetime.now(timezone.utc)
    if row is not None:
        row.label = label
        row.source = "user"
        row.updated_at = now
    else:
        row = models.LocationLabel(
            lat_grid=g_lat,
            lon_grid=g_lon,
            label=label,
            source="user",
            updated_at=now,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    logger.info(f"User override location label for ({g_lat}, {g_lon}): {label}")
    return row


# ── Subscriptions ──


def _new_token() -> str:
    return secrets.token_urlsafe(32)


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def get_subscriber_by_email(db: Session, email: str) -> Optional[models.Subscriber]:
    return (
        db.query(models.Subscriber)
        .filter(models.Subscriber.email == _normalize_email(email))
        .first()
    )


def get_subscriber_by_manage_token(
    db: Session, token: str
) -> Optional[models.Subscriber]:
    return (
        db.query(models.Subscriber)
        .filter(models.Subscriber.manage_token == token)
        .first()
    )


def get_or_create_subscriber(db: Session, email: str) -> models.Subscriber:
    sub = get_subscriber_by_email(db, email)
    if sub:
        return sub
    sub = models.Subscriber(
        email=_normalize_email(email),
        manage_token=_new_token(),
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)
    logger.info(f"Created subscriber id={sub.id} email={sub.email}")
    return sub


def create_verify_token(
    db: Session, subscriber: models.Subscriber, *, expires_days: int = 7
) -> models.EmailToken:
    token = models.EmailToken(
        subscriber_id=subscriber.id,
        token=_new_token(),
        purpose="verify",
        expires_at=datetime.now(timezone.utc) + timedelta(days=expires_days),
    )
    db.add(token)
    db.commit()
    db.refresh(token)
    return token


def get_verify_token(db: Session, token: str) -> Optional[models.EmailToken]:
    return (
        db.query(models.EmailToken)
        .filter(
            models.EmailToken.token == token,
            models.EmailToken.purpose == "verify",
        )
        .first()
    )


def get_unsubscribe_token(db: Session, token: str) -> Optional[models.EmailToken]:
    return (
        db.query(models.EmailToken)
        .filter(
            models.EmailToken.token == token,
            models.EmailToken.purpose == "unsubscribe",
        )
        .first()
    )


def upsert_subscription(
    db: Session,
    subscriber: models.Subscriber,
    *,
    device_imei: Optional[str],
    wants_realtime: bool,
    wants_digest: bool,
    digest_frequency: str,
    digest_hour_utc: int,
    realtime_throttle_minutes: int,
) -> models.Subscription:
    """Insert or update the subscriber's row for this device (or "all")."""
    row = (
        db.query(models.Subscription)
        .filter(
            models.Subscription.subscriber_id == subscriber.id,
            models.Subscription.device_imei.is_(None)
            if device_imei is None
            else models.Subscription.device_imei == device_imei,
        )
        .first()
    )
    if row is None:
        row = models.Subscription(
            subscriber_id=subscriber.id,
            device_imei=device_imei,
        )
        db.add(row)
    row.wants_realtime = wants_realtime
    row.wants_digest = wants_digest
    row.digest_frequency = digest_frequency if digest_frequency in ("daily", "weekly") else "daily"
    row.digest_hour_utc = max(0, min(23, digest_hour_utc))
    row.realtime_throttle_minutes = max(0, realtime_throttle_minutes)
    db.commit()
    db.refresh(row)
    return row


def replace_subscriptions(
    db: Session,
    subscriber: models.Subscriber,
    items: Iterable[schemas.SubscriptionItem],
) -> list[models.Subscription]:
    """Replace the subscriber's whole subscription set with ``items``."""
    db.query(models.Subscription).filter(
        models.Subscription.subscriber_id == subscriber.id
    ).delete()
    result: list[models.Subscription] = []
    seen_keys: set[Optional[str]] = set()
    for item in items:
        key = item.device_imei
        if key in seen_keys:
            continue
        seen_keys.add(key)
        row = models.Subscription(
            subscriber_id=subscriber.id,
            device_imei=key,
            wants_realtime=item.wants_realtime,
            wants_digest=item.wants_digest,
            digest_frequency=item.digest_frequency
            if item.digest_frequency in ("daily", "weekly")
            else "daily",
            digest_hour_utc=max(0, min(23, item.digest_hour_utc)),
            realtime_throttle_minutes=max(0, item.realtime_throttle_minutes),
        )
        db.add(row)
        result.append(row)
    db.commit()
    return result


def mark_unsubscribed(db: Session, subscriber: models.Subscriber) -> None:
    subscriber.unsubscribed_at = datetime.now(timezone.utc)
    db.commit()
    logger.info(f"Subscriber id={subscriber.id} ({subscriber.email}) unsubscribed")
