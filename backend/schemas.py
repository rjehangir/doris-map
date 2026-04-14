from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class DorisMessageBase(BaseModel):
    device_imei: str
    momsn: Optional[int] = None
    transmit_time: Optional[str] = None
    iridium_latitude: Optional[float] = None
    iridium_longitude: Optional[float] = None
    iridium_cep: Optional[int] = None
    latitude: float
    longitude: float
    altitude: Optional[float] = None
    satellite_count: Optional[int] = None
    battery_voltage: Optional[float] = None
    leak_detected: Optional[bool] = None
    max_depth: Optional[float] = None
    raw_data: Optional[str] = None


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
