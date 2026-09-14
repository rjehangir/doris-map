from datetime import datetime, timezone
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field, field_serializer, field_validator


class DorisMessageBase(BaseModel):
    device_imei: str
    momsn: Optional[int] = None
    transmit_time: Optional[str] = None
    iridium_latitude: Optional[float] = None
    iridium_longitude: Optional[float] = None
    iridium_cep: Optional[int] = None
    latitude: float
    longitude: float
    message_type: Optional[str] = None
    message_version: Optional[str] = None
    velocity_dm_s: Optional[int] = None
    course_deg: Optional[int] = None
    battery_voltage: Optional[float] = None
    max_depth: Optional[float] = None
    status_flags: Optional[int] = None
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


class RockblockWebhookIn(BaseModel):
    """Strictly-validated shape of an inbound RockBLOCK webhook post.

    Every constraint here is deliberately tight: RockBLOCK IMEIs are always
    15 digits, ``momsn`` is a uint16, timestamps use ``yy-MM-dd HH:mm:ss`` in
    UTC, and the SBD payload is at most 340 bytes (680 hex chars). Anything
    outside those bounds is not a real RockBLOCK message and must be rejected
    before it reaches the database.
    """

    imei: str = Field(..., pattern=r"^\d{15}$")
    serial: str = Field(..., pattern=r"^\d{1,10}$")
    momsn: int = Field(..., ge=0, le=65535)
    transmit_time: datetime
    iridium_latitude: float = Field(..., ge=-90.0, le=90.0)
    iridium_longitude: float = Field(..., ge=-180.0, le=180.0)
    iridium_cep: int = Field(..., ge=0, le=100000)
    data: str = Field(..., pattern=r"^[0-9a-fA-F]+$", max_length=680)

    @field_validator("transmit_time", mode="before")
    @classmethod
    def _parse_rockblock_timestamp(cls, v):
        if isinstance(v, datetime):
            return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        return datetime.strptime(str(v), "%y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )


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
