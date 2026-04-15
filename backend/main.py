#! /usr/bin/env python3

import json
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
from config import get_device_name, get_devices, load_devices
from crud import (
    create_dive_start,
    create_doris_message,
    get_device_messages,
    get_dive_starts,
    get_latest_message_per_device,
    get_recent_messages,
)
from database import SessionLocal, engine
from schemas import DiveStartCreate, DiveStartResponse, DorisMessageResponse


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
load_devices()

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
    device_name = get_device_name(imei)
    if device_name == imei:
        logger.warning(f"Received message from unknown IMEI: {imei}")
    else:
        logger.info(f"Received message from {device_name} (IMEI {imei})")

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
    devices = get_devices()
    result = []
    for imei, info in devices.items():
        latest = get_latest_message_per_device(db, imei)
        result.append({
            "imei": imei,
            "name": info.get("name", imei),
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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
