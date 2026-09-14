"""Tests for the POST /rockblock-webhook endpoint.

Verifies both the happy path and the security controls: authentication,
input validation, and rejection of unregistered devices.
"""

import pytest

from crud import parse_doris_payload
from models import DorisMessage
from tests.conftest import WEBHOOK_AUTH_HEADERS

KNOWN_IMEI = "301434061119510"
UNKNOWN_IMEI = "999999999999999"


def encode_p1(
    *,
    msg_type: str = "P",
    version: str = "1",
    lat: str = "+021.43255",
    lon: str = "-157.78933",
    velocity: str = "12",
    course: str = "045",
    depth: str = "0028",
    battery: str = "14.7",
    flags: bytes = b"\x00\x00",
) -> str:
    prefix = (
        f"{msg_type},{version},{lat},{lon},{velocity},{course},{depth},{battery},"
    ).encode("ascii")
    return (prefix + flags).hex()


EXAMPLE_HEX = encode_p1()


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
    def test_parses_canonical_p1_payload(self):
        result = parse_doris_payload(EXAMPLE_HEX)

        assert result["message_type"] == "P"
        assert result["message_version"] == "1"
        assert result["latitude"] == pytest.approx(21.43255)
        assert result["longitude"] == pytest.approx(-157.78933)
        assert result["velocity_dm_s"] == 12
        assert result["course_deg"] == 45
        assert result["max_depth"] == pytest.approx(28.0)
        assert result["battery_voltage"] == pytest.approx(14.7)
        assert result["status_flags"] == 0

    def test_accepts_unpadded_lat_lon(self):
        hex_data = encode_p1(lat="-21.43", lon="+157.8", velocity="5", course="45", depth="28")
        result = parse_doris_payload(hex_data)
        assert result["latitude"] == pytest.approx(-21.43)
        assert result["longitude"] == pytest.approx(157.8)
        assert result["velocity_dm_s"] == 5
        assert result["course_deg"] == 45
        assert result["max_depth"] == pytest.approx(28.0)

    def test_parses_non_ascii_flag_bytes(self):
        result = parse_doris_payload(encode_p1(flags=b"\xff\x01"))
        assert result["status_flags"] == 0xFF01

    def test_parses_flag_bytes_containing_comma(self):
        result = parse_doris_payload(encode_p1(flags=b"\x2c\x00"))
        assert result["status_flags"] == 0x2C00

    def test_rejects_invalid_hex(self):
        with pytest.raises(ValueError):
            parse_doris_payload("ZZZZ")

    def test_accepts_old_key_value_format_for_position(self):
        old = "LAT:21.432841,LON:-157.789464,ALT:12.7,SAT:6,V:14.71,LEAK:0,MAXD:28.6m"
        result = parse_doris_payload(old.encode("ascii").hex())
        assert result["latitude"] == pytest.approx(21.432841)
        assert result["longitude"] == pytest.approx(-157.789464)
        assert result["battery_voltage"] == pytest.approx(14.71)
        assert result["max_depth"] == pytest.approx(28.0)
        assert result["message_type"] is None
        assert result["message_version"] is None

    def test_rejects_truncated_payload_without_longitude(self):
        truncated = "P,1,+021.43255".encode("ascii").hex()
        with pytest.raises(ValueError, match="latitude/longitude"):
            parse_doris_payload(truncated)

    def test_accepts_position_without_flag_bytes(self):
        missing_flags = "P,1,+021.43255,-157.78933,12,045,0028,14.7".encode("ascii").hex()
        result = parse_doris_payload(missing_flags)
        assert result["latitude"] == pytest.approx(21.43255)
        assert result["longitude"] == pytest.approx(-157.78933)
        assert result["velocity_dm_s"] == 12
        assert result["status_flags"] is None

    def test_accepts_position_only(self):
        hex_data = "P,1,+021.43255,-157.78933".encode("ascii").hex()
        result = parse_doris_payload(hex_data)
        assert result["latitude"] == pytest.approx(21.43255)
        assert result["longitude"] == pytest.approx(-157.78933)
        assert result["velocity_dm_s"] is None
        assert result["course_deg"] is None
        assert result["max_depth"] is None
        assert result["battery_voltage"] is None
        assert result["status_flags"] is None

    def test_accepts_unknown_message_type_if_position_present(self):
        result = parse_doris_payload(encode_p1(msg_type="X"))
        assert result["message_type"] == "X"
        assert result["latitude"] == pytest.approx(21.43255)
        assert result["longitude"] == pytest.approx(-157.78933)

    def test_accepts_unknown_version_if_position_present(self):
        result = parse_doris_payload(encode_p1(version="2"))
        assert result["message_version"] == "2"
        assert result["latitude"] == pytest.approx(21.43255)

    def test_keeps_position_when_optional_fields_are_malformed(self):
        hex_data = encode_p1(velocity="xx", course="", depth="??", battery="n/a")
        result = parse_doris_payload(hex_data)
        assert result["latitude"] == pytest.approx(21.43255)
        assert result["longitude"] == pytest.approx(-157.78933)
        assert result["velocity_dm_s"] is None
        assert result["course_deg"] is None
        assert result["max_depth"] is None
        assert result["battery_voltage"] is None

    def test_accepts_trailing_nul_after_flags(self):
        raw = bytes.fromhex(encode_p1(flags=b"\xff\x01")) + b"\x00"
        result = parse_doris_payload(raw.hex())
        assert result["latitude"] == pytest.approx(21.43255)
        assert result["status_flags"] == 0xFF01

    def test_rejects_latitude_out_of_range_without_named_coords(self):
        with pytest.raises(ValueError, match="latitude/longitude"):
            parse_doris_payload(encode_p1(lat="+091.00000"))


# -- Integration test: full webhook POST ----------------------------------


class TestRockblockWebhook:
    def test_successful_post_returns_ok(self, client):
        resp = client.post(
            "/rockblock-webhook",
            data=_build_form_data(),
            headers=WEBHOOK_AUTH_HEADERS,
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "id" in body

    def test_message_stored_in_database(self, client, db_session):
        resp = client.post(
            "/rockblock-webhook",
            data=_build_form_data(),
            headers=WEBHOOK_AUTH_HEADERS,
        )
        msg_id = resp.json()["id"]

        row = db_session.get(DorisMessage, msg_id)
        assert row is not None
        assert row.device_imei == KNOWN_IMEI
        assert row.momsn == 42
        assert row.message_type == "P"
        assert row.message_version == "1"
        assert row.latitude == pytest.approx(21.43255)
        assert row.longitude == pytest.approx(-157.78933)
        assert row.velocity_dm_s == 12
        assert row.course_deg == 45
        assert row.battery_voltage == pytest.approx(14.7)
        assert row.max_depth == pytest.approx(28.0)
        assert row.status_flags == 0
        assert row.raw_data == EXAMPLE_HEX
        # transmit_time is now stored in normalized ISO-8601 UTC form.
        assert row.transmit_time == "2026-04-14T12:00:00Z"

    def test_old_format_with_position_is_stored(self, client, db_session):
        old = "LAT:21.432841,LON:-157.789464,ALT:12.7,SAT:6,V:14.71,LEAK:0,MAXD:28.6m"
        resp = client.post(
            "/rockblock-webhook",
            data=_build_form_data(hex_data=old.encode("ascii").hex()),
            headers=WEBHOOK_AUTH_HEADERS,
        )
        assert resp.status_code == 200
        row = db_session.get(DorisMessage, resp.json()["id"])
        assert row.latitude == pytest.approx(21.432841)
        assert row.longitude == pytest.approx(-157.789464)
        assert row.battery_voltage == pytest.approx(14.71)
        assert row.max_depth == pytest.approx(28.0)

    def test_missing_auth_returns_401(self, client):
        resp = client.post("/rockblock-webhook", data=_build_form_data())
        assert resp.status_code == 401

    def test_wrong_shared_secret_returns_401(self, client):
        resp = client.post(
            "/rockblock-webhook",
            data=_build_form_data(),
            headers={"Authorization": "Bearer nope"},
        )
        assert resp.status_code == 401

    def test_secret_in_query_string_accepted(self, client):
        resp = client.post(
            "/rockblock-webhook?secret=test-webhook-secret",
            data=_build_form_data(),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_wrong_secret_in_query_string_returns_401(self, client):
        resp = client.post(
            "/rockblock-webhook?secret=nope",
            data=_build_form_data(),
        )
        assert resp.status_code == 401

    def test_unregistered_imei_returns_403(self, client, db_session):
        resp = client.post(
            "/rockblock-webhook",
            data=_build_form_data(imei=UNKNOWN_IMEI),
            headers=WEBHOOK_AUTH_HEADERS,
        )
        assert resp.status_code == 403
        # And crucially: nothing was written for that IMEI.
        assert (
            db_session.query(DorisMessage)
            .filter(DorisMessage.device_imei == UNKNOWN_IMEI)
            .count()
            == 0
        )

    @pytest.mark.parametrize(
        "field,value",
        [
            ("imei", "'; DROP TABLE--"),
            ("imei", "../../../tmp/pwned"),
            ("imei", "test123"),
            ("imei", "12345"),                    # too short
            ("imei", "1234567890123456"),         # too long
            ("transmit_time", "{{7*7}}"),
            ("transmit_time", "not-a-date"),
            ("iridium_latitude", "999"),          # out of range
            ("iridium_longitude", "-500"),        # out of range
            ("data", "DEADBEEF_NOT_VALID"),       # non-hex
        ],
    )
    def test_malicious_or_bad_input_rejected(self, client, db_session, field, value):
        form = _build_form_data()
        form[field] = value
        resp = client.post(
            "/rockblock-webhook",
            data=form,
            headers=WEBHOOK_AUTH_HEADERS,
        )
        assert resp.status_code == 422, (field, value, resp.text)
        assert db_session.query(DorisMessage).count() == 0

    def test_missing_required_form_field_returns_422(self, client):
        form = _build_form_data()
        del form["imei"]
        resp = client.post(
            "/rockblock-webhook",
            data=form,
            headers=WEBHOOK_AUTH_HEADERS,
        )
        assert resp.status_code == 422

    def test_messages_visible_via_api(self, client):
        client.post(
            "/rockblock-webhook",
            data=_build_form_data(),
            headers=WEBHOOK_AUTH_HEADERS,
        )

        resp = client.get(f"/api/devices/{KNOWN_IMEI}/messages")
        assert resp.status_code == 200
        messages = resp.json()
        assert len(messages) == 1
        assert messages[0]["latitude"] == pytest.approx(21.43255)
        assert messages[0]["velocity_dm_s"] == 12
        assert messages[0]["course_deg"] == 45
        assert messages[0]["status_flags"] == 0
        assert "leak_detected" not in messages[0]
        assert "altitude" not in messages[0]
        assert "satellite_count" not in messages[0]
