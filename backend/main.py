#! /usr/bin/env python3

import json
from typing import Any, List

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
    create_doris_message,
    get_device_messages,
    get_latest_message_per_device,
    get_recent_messages,
)
from database import SessionLocal, engine
from schemas import DeviceStatus, DorisMessageResponse


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


@app.get("/")
async def root():
    return RedirectResponse(url="/ui")


@app.get("/api/health")
async def health(db: Session = Depends(get_db)):
    """Diagnostic: check DB connectivity and return env info."""
    import os

    db_url = os.getenv("DATABASE_URL", "(not set, using sqlite default)")
    masked = db_url
    if "@" in db_url:
        pre, post = db_url.split("@", 1)
        masked = pre.rsplit(":", 1)[0] + ":***@" + post

    try:
        result = db.execute(models.DorisMessage.__table__.select().limit(1))
        rows = result.fetchall()
        db_status = f"ok, sample rows: {len(rows)}"
    except Exception as e:
        db_status = f"error: {e}"

    return {"database_url": masked, "db_status": db_status}


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


@app.get("/api/devices", response_model=List[DeviceStatus])
async def list_devices(db: Session = Depends(get_db)) -> Any:
    """List all configured devices with their latest reported position."""
    devices = get_devices()
    result = []
    for imei, info in devices.items():
        latest = get_latest_message_per_device(db, imei)
        result.append(
            DeviceStatus(
                imei=imei,
                name=info.get("name", imei),
                latest_message=latest,
            )
        )
    return result


@app.get("/api/devices/{imei}/messages", response_model=List[DorisMessageResponse])
async def device_messages(
    imei: str, skip: int = 0, limit: int = 1000, db: Session = Depends(get_db)
) -> Any:
    """Get message history for a single device."""
    return get_device_messages(db, imei, skip, limit)


@app.get("/api/messages/recent", response_model=List[DorisMessageResponse])
async def recent_messages(hours: int = 24, db: Session = Depends(get_db)) -> Any:
    """Get all messages from all devices within a time window."""
    return get_recent_messages(db, hours)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
