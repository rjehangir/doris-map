from datetime import datetime
from typing import List, Optional, Tuple

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


class GeocodeBatchRequest(BaseModel):
    coords: List[Tuple[float, float]]


class GeocodeOverrideRequest(BaseModel):
    latitude: float
    longitude: float
    label: str


class LocationLabelResponse(BaseModel):
    lat_grid: float
    lon_grid: float
    label: str
    source: str

    model_config = {"from_attributes": True}


# ── Subscriptions ──


class SubscribeRequest(BaseModel):
    email: str
    # "all" or a specific IMEI; default to "all" if omitted.
    imei: Optional[str] = "all"
    wants_realtime: bool = True
    wants_digest: bool = False
    digest_frequency: str = "daily"  # 'daily' | 'weekly'
    digest_hour_utc: int = 13
    realtime_throttle_minutes: int = 0


class ManageLinkRequest(BaseModel):
    email: str


class SubscriptionItem(BaseModel):
    # null device_imei = "all units"
    device_imei: Optional[str] = None
    wants_realtime: bool = True
    wants_digest: bool = False
    digest_frequency: str = "daily"
    digest_hour_utc: int = 13
    realtime_throttle_minutes: int = 0

    model_config = {"from_attributes": True}


class SubscriptionsResponse(BaseModel):
    email: str
    verified: bool
    unsubscribed: bool
    subscriptions: List[SubscriptionItem]


class SubscriptionsUpdate(BaseModel):
    subscriptions: List[SubscriptionItem]
