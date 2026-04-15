from datetime import datetime, timezone, timedelta
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

import models
import schemas


def parse_doris_payload(hex_data: str) -> dict:
    """Parse hex-encoded plain-text DORIS payload into a dict of typed values.

    Expected format after decoding:
        LAT:21.432552,LON:-157.789331,ALT:20.5,SAT:4,V:14.93,LEAK:0,MAXD:1.1m
    """
    text = bytes.fromhex(hex_data).decode("ascii")
    logger.debug(f"Decoded payload: {text}")

    fields = {}
    for pair in text.split(","):
        key, value = pair.split(":", 1)
        fields[key.strip()] = value.strip()

    return {
        "latitude": float(fields["LAT"]),
        "longitude": float(fields["LON"]),
        "altitude": float(fields["ALT"]),
        "satellite_count": int(fields["SAT"]),
        "battery_voltage": float(fields["V"]),
        "leak_detected": fields["LEAK"] == "1",
        "max_depth": float(fields["MAXD"].rstrip("m")),
    }


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
        altitude=parsed["altitude"],
        satellite_count=parsed["satellite_count"],
        battery_voltage=parsed["battery_voltage"],
        leak_detected=parsed["leak_detected"],
        max_depth=parsed["max_depth"],
        raw_data=hex_data,
    )
    db.add(db_message)
    db.commit()
    db.refresh(db_message)
    logger.info(f"Stored message id={db_message.id} from IMEI {imei}")
    return db_message


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


# ── Dive Starts ──


def create_dive_start(
    db: Session, data: schemas.DiveStartCreate
) -> models.DiveStart:
    obj = models.DiveStart(
        device_imei=data.device_imei,
        latitude=data.latitude,
        longitude=data.longitude,
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
