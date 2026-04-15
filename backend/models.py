from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime

from database import Base


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
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
