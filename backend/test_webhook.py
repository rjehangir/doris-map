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


def make_payload(lat: float, lon: float, alt: float, sat: int, voltage: float, leak: int, max_depth: float) -> str:
    text = f"LAT:{lat:.6f},LON:{lon:.6f},ALT:{alt:.1f},SAT:{sat},V:{voltage:.2f},LEAK:{leak},MAXD:{max_depth:.1f}m"
    return text.encode("ascii").hex()


def send_message(imei: str, lat: float, lon: float):
    payload_hex = make_payload(
        lat=lat,
        lon=lon,
        alt=round(random.uniform(0, 50), 1),
        sat=random.randint(3, 12),
        voltage=round(random.uniform(11.5, 15.0), 2),
        leak=random.choice([0, 0, 0, 0, 1]),  # 20% chance of leak
        max_depth=round(random.uniform(0.5, 30.0), 1),
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
