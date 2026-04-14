from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class DorisMessageBase(BaseModel):
    device_imei: str
    momsn: int
    transmit_time: str
    iridium_latitude: float
    iridium_longitude: float
    iridium_cep: int
    latitude: float
    longitude: float
    altitude: float
    satellite_count: int
    battery_voltage: float
    leak_detected: bool
    max_depth: float
    raw_data: str


class DorisMessageResponse(DorisMessageBase):
    id: int
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class DeviceInfo(BaseModel):
    imei: str
    name: str


class DeviceStatus(BaseModel):
    imei: str
    name: str
    latest_message: Optional[DorisMessageResponse] = None
