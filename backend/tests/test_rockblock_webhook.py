"""Tests for the POST /rockblock-webhook endpoint.

Verifies that a Rockblock-style form POST is accepted, parsed, stored,
and that the response and database row contain the expected values.
"""

import pytest

from crud import parse_doris_payload
from models import DorisMessage

EXAMPLE_PAYLOAD = (
    "LAT:21.432841,LON:-157.789464,ALT:12.7,SAT:6,V:14.71,LEAK:0,MAXD:28.6m"
)
EXAMPLE_HEX = EXAMPLE_PAYLOAD.encode("ascii").hex()

KNOWN_IMEI = "301434061119510"
UNKNOWN_IMEI = "999999999999999"


def _build_form_data(*, imei: str = KNOWN_IMEI, hex_data: str = EXAMPLE_HEX) -> dict:
    return {
        "imei": imei,
        "serial": "12345",
        "momsn": "42",
        "transmit_time": "26-04-14 12:00:00",
        "iridium_latitude": "21.43",
        "iridium_longitude": "-157.79",
        "iridium_cep": "3",
        "data": hex_data,
    }


# -- Unit test: payload parsing ------------------------------------------


class TestParseDorisPayload:
    def test_parses_example_payload(self):
        result = parse_doris_payload(EXAMPLE_HEX)

        assert result["latitude"] == pytest.approx(21.432841)
        assert result["longitude"] == pytest.approx(-157.789464)
        assert result["altitude"] == pytest.approx(12.7)
        assert result["satellite_count"] == 6
        assert result["battery_voltage"] == pytest.approx(14.71)
        assert result["leak_detected"] is False
        assert result["max_depth"] == pytest.approx(28.6)

    def test_leak_detected_when_flag_is_one(self):
        payload = "LAT:0,LON:0,ALT:0,SAT:0,V:0,LEAK:1,MAXD:0m"
        hex_data = payload.encode("ascii").hex()
        result = parse_doris_payload(hex_data)
        assert result["leak_detected"] is True

    def test_rejects_invalid_hex(self):
        with pytest.raises(ValueError):
            parse_doris_payload("ZZZZ")

    def test_rejects_missing_fields(self):
        incomplete = "LAT:1,LON:2".encode("ascii").hex()
        with pytest.raises(KeyError):
            parse_doris_payload(incomplete)


# -- Integration test: full webhook POST ----------------------------------


class TestRockblockWebhook:
    def test_successful_post_returns_ok(self, client):
        resp = client.post("/rockblock-webhook", data=_build_form_data())

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "id" in body

    def test_message_stored_in_database(self, client, db_session):
        resp = client.post("/rockblock-webhook", data=_build_form_data())
        msg_id = resp.json()["id"]

        row = db_session.get(DorisMessage, msg_id)
        assert row is not None
        assert row.device_imei == KNOWN_IMEI
        assert row.momsn == 42
        assert row.latitude == pytest.approx(21.432841)
        assert row.longitude == pytest.approx(-157.789464)
        assert row.altitude == pytest.approx(12.7)
        assert row.satellite_count == 6
        assert row.battery_voltage == pytest.approx(14.71)
        assert row.leak_detected is False
        assert row.max_depth == pytest.approx(28.6)
        assert row.raw_data == EXAMPLE_HEX

    def test_unknown_imei_still_succeeds(self, client):
        form = _build_form_data(imei=UNKNOWN_IMEI)
        resp = client.post("/rockblock-webhook", data=form)

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_corrupt_hex_returns_error(self, client):
        form = _build_form_data(hex_data="DEADBEEF_NOT_VALID")
        resp = client.post("/rockblock-webhook", data=form)

        body = resp.json()
        assert body["status"] == "error"
        assert "detail" in body

    def test_missing_required_form_field_returns_422(self, client):
        form = _build_form_data()
        del form["imei"]
        resp = client.post("/rockblock-webhook", data=form)

        assert resp.status_code == 422

    def test_messages_visible_via_api(self, client):
        client.post("/rockblock-webhook", data=_build_form_data())

        resp = client.get(f"/api/devices/{KNOWN_IMEI}/messages")
        assert resp.status_code == 200
        messages = resp.json()
        assert len(messages) == 1
        assert messages[0]["latitude"] == pytest.approx(21.432841)
