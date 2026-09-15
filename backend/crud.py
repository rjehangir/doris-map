import secrets
from datetime import datetime, timezone, timedelta
from typing import Iterable, Optional

from loguru import logger
from sqlalchemy.orm import Session

import geocode
import models
import schemas

# Stored in subscriptions.device_imei for "every current and future unit".
# Prefer this over SQL NULL so unique constraints and equality matches work
# the same on SQLite and PostgreSQL.
ALL_UNITS_IMEI = "all"
_ALL_UNITS_ALIASES = {"", "all", "__all__", "null", "none", "*"}


def is_all_units_imei(imei: Optional[str]) -> bool:
    if imei is None:
        return True
    return imei.strip().lower() in _ALL_UNITS_ALIASES


def normalize_subscription_imei(imei: Optional[str]) -> str:
    if is_all_units_imei(imei):
        return ALL_UNITS_IMEI
    return imei.strip()


def _parse_coord(value: Optional[str], lo: float, hi: float) -> Optional[float]:
    if value is None:
        return None
    try:
        parsed = float(value.strip())
    except (TypeError, ValueError):
        return None
    if lo <= parsed <= hi:
        return parsed
    return None


def _type_or_version_token(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    token = value.strip()
    if len(token) == 1 and token.isalnum():
        return token
    return None


def _optional_int(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    text = value.strip().rstrip("mMdD")
    try:
        return int(text, 10)
    except ValueError:
        try:
            return int(float(text))
        except ValueError:
            logger.debug("Ignoring malformed integer field: {!r}", value)
            return None


def _optional_float(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    text = value.strip().rstrip("vV")
    try:
        return float(text)
    except ValueError:
        logger.debug("Ignoring malformed float field: {!r}", value)
        return None


def _looks_like_ascii_number(raw: bytes) -> bool:
    try:
        text = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        return False
    if not text:
        return False
    try:
        float(text)
        return True
    except ValueError:
        return False


def _split_ascii_and_flags(raw: bytes) -> tuple[bytes, Optional[int]]:
    """Peel optional trailing flag bytes; leave the ASCII CSV prefix.

    Canonical P/1 ends with ``,<hi><lo>``. That comma is at ``raw[-3]``, so a
    ``0x2C`` *inside* the flags is still parsed correctly. A trailing NUL or
    CR/LF is stripped only when the canonical ending is not already present.
    """
    raw = raw.rstrip(b"\r\n")

    def try_peel(buf: bytes) -> Optional[tuple[bytes, Optional[int]]]:
        if len(buf) >= 3 and buf[-3] == 0x2C:
            tail = buf[-2:]
            if not _looks_like_ascii_number(tail):
                return buf[:-3], int.from_bytes(tail, "big")
        if buf.endswith(b","):
            return buf[:-1], None
        return None

    peeled = try_peel(raw)
    if peeled is None:
        stripped = raw.rstrip(b"\x00")
        if stripped != raw:
            peeled = try_peel(stripped)
            if peeled is None:
                return stripped, None
    if peeled is None:
        return raw, None
    return peeled


def _named_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for pair in text.split(","):
        if ":" not in pair:
            continue
        key, value = pair.split(":", 1)
        fields[key.strip().upper()] = value.strip()
    return fields


def _parse_named_lat_lon(text: str) -> Optional[tuple[float, float]]:
    named = _named_fields(text)
    lat = _parse_coord(named.get("LAT"), -90.0, 90.0)
    lon = _parse_coord(named.get("LON"), -180.0, 180.0)
    if lat is None or lon is None:
        return None
    return lat, lon


def parse_doris_payload(hex_data: str) -> dict:
    """Parse a hex-encoded DORIS SBD payload into typed values.

    Canonical P/1 layout after ``bytes.fromhex``:
        P,1,+021.43255,-157.78933,12,045,0028,14.7,<hi><lo>

    Acceptance is loose: a valid latitude and longitude are enough to keep
    the message. Type/version, optional telemetry, padding, flags, and extra
    trailing bytes are best-effort. Unknown type/version still uses the P/1
    field order when lat/lon are in those slots.
    """
    raw = bytes.fromhex(hex_data)
    if not raw:
        raise ValueError("empty payload")

    prefix, status_flags = _split_ascii_and_flags(raw)
    text = prefix.decode("ascii", errors="replace").strip().rstrip(",")
    logger.debug(f"Decoded payload: {text}")

    fields = [part.strip() for part in text.split(",")]
    result = {
        "message_type": _type_or_version_token(fields[0]) if fields else None,
        "message_version": _type_or_version_token(fields[1]) if len(fields) >= 2 else None,
        "latitude": None,
        "longitude": None,
        "velocity_dm_s": None,
        "course_deg": None,
        "max_depth": None,
        "battery_voltage": None,
        "status_flags": status_flags,
    }

    lat = _parse_coord(fields[2], -90.0, 90.0) if len(fields) >= 4 else None
    lon = _parse_coord(fields[3], -180.0, 180.0) if len(fields) >= 4 else None
    if lat is None or lon is None:
        named = _parse_named_lat_lon(text)
        if named:
            lat, lon = named

    if lat is None or lon is None:
        raise ValueError("payload missing a valid latitude/longitude")

    result["latitude"] = lat
    result["longitude"] = lon
    if len(fields) >= 5:
        result["velocity_dm_s"] = _optional_int(fields[4])
    if len(fields) >= 6:
        result["course_deg"] = _optional_int(fields[5])
    if len(fields) >= 7:
        depth = _optional_int(fields[6])
        result["max_depth"] = float(depth) if depth is not None else None
    if len(fields) >= 8:
        result["battery_voltage"] = _optional_float(fields[7])

    named = _named_fields(text)
    if result["battery_voltage"] is None and "V" in named:
        result["battery_voltage"] = _optional_float(named["V"])
    if result["max_depth"] is None and "MAXD" in named:
        depth = _optional_int(named["MAXD"])
        result["max_depth"] = float(depth) if depth is not None else None

    return result


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


def _all_units_subscription(
    db: Session, subscriber: models.Subscriber
) -> Optional[models.Subscription]:
    rows = (
        db.query(models.Subscription)
        .filter(models.Subscription.subscriber_id == subscriber.id)
        .all()
    )
    for row in rows:
        if is_all_units_imei(row.device_imei):
            return row
    return None


def drop_covered_device_subscriptions(
    db: Session, subscriber: models.Subscriber
) -> int:
    """If an all-units row exists, delete per-device (and duplicate all) rows.

    All-units already covers every device, so extra rows only cause duplicate
    emails. Returns the number of rows deleted (caller must commit).
    """
    db.flush()
    rows = (
        db.query(models.Subscription)
        .filter(models.Subscription.subscriber_id == subscriber.id)
        .all()
    )
    keep = next((r for r in rows if r.device_imei == ALL_UNITS_IMEI), None)
    if keep is None:
        keep = next((r for r in rows if is_all_units_imei(r.device_imei)), None)
    if keep is None:
        return 0
    if keep.device_imei != ALL_UNITS_IMEI:
        keep.device_imei = ALL_UNITS_IMEI
        db.flush()
    deleted = 0
    for row in rows:
        if row.id == keep.id:
            continue
        db.delete(row)
        deleted += 1
    return deleted


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
    """Insert or update the subscriber's row for this device (or "all").

    An all-units row subsumes per-device rows: subscribing to a specific
    unit while already on "all" is a no-op, and subscribing to "all"
    drops any per-device rows.
    """
    device_imei = normalize_subscription_imei(device_imei)
    existing_all = _all_units_subscription(db, subscriber)
    if device_imei != ALL_UNITS_IMEI and existing_all is not None:
        return existing_all
    row = (
        db.query(models.Subscription)
        .filter(
            models.Subscription.subscriber_id == subscriber.id,
            models.Subscription.device_imei == device_imei,
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
    if device_imei == ALL_UNITS_IMEI:
        drop_covered_device_subscriptions(db, subscriber)
    db.commit()
    db.refresh(row)
    return row


def replace_subscriptions(
    db: Session,
    subscriber: models.Subscriber,
    items: Iterable[schemas.SubscriptionItem],
) -> list[models.Subscription]:
    """Replace the subscriber's whole subscription set with ``items``.

    If the set includes all-units, per-device entries are discarded.
    """
    items = list(items)
    all_item = next((i for i in items if is_all_units_imei(i.device_imei)), None)
    if all_item is not None:
        items = [all_item]
    db.query(models.Subscription).filter(
        models.Subscription.subscriber_id == subscriber.id
    ).delete()
    result: list[models.Subscription] = []
    seen_keys: set[Optional[str]] = set()
    for item in items:
        key = normalize_subscription_imei(item.device_imei)
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
