#! /usr/bin/env python3

import json
import os
from datetime import datetime
from typing import Any, List, Optional

import uvicorn
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from sqlalchemy.orm import Session
from starlette.responses import Response as StarletteResponse

import models
from crud import (
    auto_register_device,
    create_dive_start,
    create_doris_message,
    delete_dive_start,
    get_all_devices,
    get_device_by_imei,
    get_device_messages,
    get_device_name,
    get_dive_starts,
    get_latest_message_per_device,
    get_location_labels_batch,
    get_messages_paginated,
    get_recent_messages,
    set_user_location_label,
    update_dive_start,
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
)


class PrettyJSONResponse(StarletteResponse):
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

app = FastAPI(
    title="DORIS Tracker API",
    description="Multi-device camera tracking system via Iridium / RockBLOCK.",
    default_response_class=PrettyJSONResponse,
)

app.mount("/ui", StaticFiles(directory="../frontend", html=True), name="static")


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
    imei: str = Form(...),
    serial: str = Form(...),
    momsn: int = Form(...),
    transmit_time: str = Form(...),
    iridium_latitude: float = Form(...),
    iridium_longitude: float = Form(...),
    iridium_cep: int = Form(...),
    data: str = Form(...),
    db: Session = Depends(get_db),
):
    """Receive a RockBLOCK webhook (form-encoded) from Rock Seven."""
    device = get_device_by_imei(db, imei)
    if not device:
        device = auto_register_device(db, imei)
    logger.info(f"Received message from {device.name} (IMEI {imei})")

    try:
        message = create_doris_message(
            db=db,
            imei=imei,
            momsn=momsn,
            transmit_time=transmit_time,
            iridium_latitude=iridium_latitude,
            iridium_longitude=iridium_longitude,
            iridium_cep=iridium_cep,
            hex_data=data,
        )
        return {"status": "ok", "id": message.id}
    except Exception as e:
        logger.error(f"Failed to process message from IMEI {imei}: {e}")
        return {"status": "error", "detail": str(e)}


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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
