from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, UniqueConstraint

from database import Base


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    imei = Column(String, unique=True, index=True, nullable=False)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DorisMessage(Base):
    __tablename__ = "doris_messages"

    id = Column(Integer, primary_key=True, index=True)
    device_imei = Column(String, index=True)
    momsn = Column(Integer)
    transmit_time = Column(String)
    iridium_latitude = Column(Float)
    iridium_longitude = Column(Float)
    iridium_cep = Column(Integer)
    latitude = Column(Float)
    longitude = Column(Float)
    altitude = Column(Float)
    satellite_count = Column(Integer)
    battery_voltage = Column(Float)
    leak_detected = Column(Boolean)
    max_depth = Column(Float)
    raw_data = Column(String)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DiveStart(Base):
    __tablename__ = "dive_starts"

    id = Column(Integer, primary_key=True, index=True)
    device_imei = Column(String, index=True)
    latitude = Column(Float)
    longitude = Column(Float)
    name = Column(String, nullable=True)
    notes = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class LocationLabel(Base):
    __tablename__ = "location_labels"

    id = Column(Integer, primary_key=True, index=True)
    lat_grid = Column(Float, index=True, nullable=False)
    lon_grid = Column(Float, index=True, nullable=False)
    label = Column(String, nullable=False)
    source = Column(String, default="auto", nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("lat_grid", "lon_grid", name="uq_location_grid"),
    )
