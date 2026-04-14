import json
import os

from loguru import logger

DEVICES_FILE = os.getenv("DEVICES_FILE", os.path.join(os.path.dirname(__file__), "devices.json"))

_devices: dict[str, dict] = {}


def load_devices() -> dict[str, dict]:
    global _devices
    try:
        with open(DEVICES_FILE) as f:
            _devices = json.load(f)
        logger.info(f"Loaded {len(_devices)} devices from {DEVICES_FILE}")
    except FileNotFoundError:
        logger.warning(f"Device config not found at {DEVICES_FILE}, starting with empty device list")
        _devices = {}
    return _devices


def get_devices() -> dict[str, dict]:
    return _devices


def get_device_name(imei: str) -> str:
    device = _devices.get(imei)
    if device:
        return device.get("name", imei)
    return imei
