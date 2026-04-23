"""Reverse geocoding using OpenStreetMap Nominatim.

Rate-limited to comply with the Nominatim usage policy:
https://operations.osmfoundation.org/policies/nominatim/
"""

import threading
import time
from typing import Optional, Tuple

import requests
from loguru import logger


NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "DORIS-Tracker/1.0 (https://github.com/rjehangir/doris-map)"
REQUEST_TIMEOUT = 8
MIN_INTERVAL_S = 1.0  # max 1 req/sec per Nominatim policy

_PLACE_PRIORITY = (
    "village",
    "town",
    "city",
    "hamlet",
    "suburb",
    "neighbourhood",
    "island",
    "archipelago",
    "county",
    "state_district",
    "state",
    "country",
)

_lock = threading.Lock()
_last_call_ts = 0.0


def grid(lat: float, lon: float) -> Tuple[float, float]:
    """Round coordinates to a ~1.1km grid cell key (2 decimal degrees)."""
    return round(float(lat), 2), round(float(lon), 2)


def _wait_for_slot() -> None:
    """Block until at least MIN_INTERVAL_S has elapsed since the last call."""
    global _last_call_ts
    now = time.monotonic()
    delta = now - _last_call_ts
    if delta < MIN_INTERVAL_S:
        time.sleep(MIN_INTERVAL_S - delta)
    _last_call_ts = time.monotonic()


def _format_label(payload: dict, lat: float, lon: float) -> str:
    """Pick the best human-readable label from a Nominatim response."""
    addr = payload.get("address") or {}
    for key in _PLACE_PRIORITY:
        val = addr.get(key)
        if val:
            return f"near {val}"
    name = payload.get("name")
    if name:
        return f"near {name}"
    display = payload.get("display_name")
    if display:
        return f"near {display.split(',')[0].strip()}"
    return f"{lat:.2f}, {lon:.2f}"


def nominatim_reverse(lat: float, lon: float) -> str:
    """Return a fuzzy place label like 'near Kaneohe Bay' for the given coords.

    Falls back to a coordinate string if Nominatim returns nothing useful or
    the request fails. Always rate-limited to 1 req/sec.
    """
    with _lock:
        _wait_for_slot()
        try:
            resp = requests.get(
                NOMINATIM_URL,
                params={
                    "format": "jsonv2",
                    "lat": f"{lat:.4f}",
                    "lon": f"{lon:.4f}",
                    "zoom": 10,
                    "addressdetails": 1,
                },
                headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as e:
            logger.warning(f"Nominatim lookup failed for ({lat}, {lon}): {e}")
            return f"{lat:.2f}, {lon:.2f}"

    return _format_label(data, lat, lon)
