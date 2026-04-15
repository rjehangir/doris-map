from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_serializer


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

    @field_serializer("created_at")
    def serialize_created_at(self, v: Optional[datetime], _info) -> Optional[str]:
        if v is None:
            return None
        return v.strftime("%Y-%m-%dT%H:%M:%SZ")


class DeviceInfo(BaseModel):
    imei: str
    name: str


class DeviceStatus(BaseModel):
    imei: str
    name: str
    latest_message: Optional[DorisMessageResponse] = None


class DiveStartCreate(BaseModel):
    device_imei: str
    latitude: float
    longitude: float
    name: Optional[str] = None


class DiveStartUpdate(BaseModel):
    name: Optional[str] = None
    notes: Optional[str] = None


class DiveStartResponse(BaseModel):
    id: int
    device_imei: str
    latitude: float
    longitude: float
    name: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

    @field_serializer("created_at")
    def serialize_created_at(self, v: Optional[datetime], _info) -> Optional[str]:
        if v is None:
            return None
        return v.strftime("%Y-%m-%dT%H:%M:%SZ")
