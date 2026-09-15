from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Boolean,
    DateTime,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

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
    message_type = Column(String)
    message_version = Column(String)
    velocity_dm_s = Column(Integer)
    course_deg = Column(Integer)
    battery_voltage = Column(Float)
    max_depth = Column(Float)
    status_flags = Column(Integer)
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


class Subscriber(Base):
    __tablename__ = "subscribers"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    manage_token = Column(String, unique=True, index=True, nullable=False)
    verified_at = Column(DateTime, nullable=True)
    unsubscribed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    subscriptions = relationship(
        "Subscription", back_populates="subscriber", cascade="all, delete-orphan"
    )
    tokens = relationship(
        "EmailToken", back_populates="subscriber", cascade="all, delete-orphan"
    )


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    subscriber_id = Column(
        Integer, ForeignKey("subscribers.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Token "all" means every current and future unit (legacy NULL still accepted)
    device_imei = Column(String, index=True, nullable=True)
    wants_realtime = Column(Boolean, default=True, nullable=False)
    wants_digest = Column(Boolean, default=False, nullable=False)
    digest_frequency = Column(String, default="daily", nullable=False)  # 'daily' | 'weekly'
    digest_hour_utc = Column(Integer, default=13, nullable=False)
    realtime_throttle_minutes = Column(Integer, default=0, nullable=False)
    last_realtime_sent_at = Column(DateTime, nullable=True)
    last_digest_sent_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    subscriber = relationship("Subscriber", back_populates="subscriptions")

    __table_args__ = (
        UniqueConstraint("subscriber_id", "device_imei", name="uq_subscription_per_device"),
    )


class EmailToken(Base):
    __tablename__ = "email_tokens"

    id = Column(Integer, primary_key=True, index=True)
    subscriber_id = Column(
        Integer, ForeignKey("subscribers.id", ondelete="CASCADE"), index=True, nullable=False
    )
    token = Column(String, unique=True, index=True, nullable=False)
    purpose = Column(String, nullable=False)  # 'verify' | 'unsubscribe'
    expires_at = Column(DateTime, nullable=True)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    subscriber = relationship("Subscriber", back_populates="tokens")
