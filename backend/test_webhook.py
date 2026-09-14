#!/usr/bin/env python3
"""Send fake DORIS messages to the local webhook for testing."""

import random
import time
from datetime import datetime

import requests

WEBHOOK_URL = "http://127.0.0.1:8000/rockblock-webhook"

TEST_DEVICES = [
    {"imei": "300234010753370", "name": "DORIS 3"},
    {"imei": "300234010753371", "name": "DORIS 4"},
]

# Honolulu area starting coordinates
BASE_LAT = 21.3069
BASE_LON = -157.8583


def make_payload(
    lat: float,
    lon: float,
    voltage: float,
    max_depth: int,
    velocity_dm_s: int,
    course_deg: int,
    flags: bytes = b"\x00\x00",
) -> str:
    lat_s = f"{lat:+010.5f}"
    lon_s = f"{lon:+010.5f}"
    text = (
        f"P,1,{lat_s},{lon_s},{velocity_dm_s:02d},{course_deg:03d},"
        f"{max_depth:04d},{voltage:04.1f},"
    )
    return (text.encode("ascii") + flags).hex()


def send_message(imei: str, lat: float, lon: float):
    payload_hex = make_payload(
        lat=lat,
        lon=lon,
        voltage=round(random.uniform(11.5, 15.0), 1),
        max_depth=random.randint(1, 30),
        velocity_dm_s=random.randint(0, 25),
        course_deg=random.randint(0, 359),
    )

    form_data = {
        "imei": imei,
        "serial": "12345",
        "momsn": str(random.randint(1, 99999)),
        "transmit_time": datetime.utcnow().strftime("%y-%m-%d %H:%M:%S"),
        "iridium_latitude": str(lat + random.uniform(-0.01, 0.01)),
        "iridium_longitude": str(lon + random.uniform(-0.01, 0.01)),
        "iridium_cep": str(random.randint(1, 10)),
        "data": payload_hex,
    }

    resp = requests.post(WEBHOOK_URL, data=form_data)
    print(f"  [{resp.status_code}] {resp.json()}")


def main():
    print(f"Sending test messages to {WEBHOOK_URL}")
    print("=" * 60)

    for device in TEST_DEVICES:
        lat = BASE_LAT + random.uniform(-0.05, 0.05)
        lon = BASE_LON + random.uniform(-0.05, 0.05)

        print(f"\n{device['name']} (IMEI {device['imei']}):")
        for i in range(5):
            lat += random.uniform(-0.005, 0.005)
            lon += random.uniform(-0.005, 0.005)
            print(f"  Message {i + 1}/5: ({lat:.6f}, {lon:.6f})")
            send_message(device["imei"], lat, lon)
            time.sleep(0.2)

    print("\nDone! Check http://localhost:8000/ui or http://localhost:8000/docs")


if __name__ == "__main__":
    main()
