"""Tests for the email-subscription flow.

Covers:
- POST /api/subscribe creates a subscriber + verify token and sends a verify email
- GET /api/verify activates the subscriber and redirects with manage token
- GET/PUT /api/subscriptions reads and replaces a subscriber's subscriptions
- GET/POST /api/unsubscribe set the unsubscribed timestamp
- dispatch_realtime respects per-subscription throttle
"""

from datetime import datetime, timezone

import pytest

import crud
import email_service
import notifications
from models import DorisMessage, EmailToken, Subscriber, Subscription


KNOWN_IMEI = "301434061119510"


# ── Helpers ──


@pytest.fixture(autouse=True)
def stub_email_sender(monkeypatch):
    """Replace the Resend HTTP call with an in-memory record."""
    sent: list[dict] = []

    def fake_post(to, subject, html, text, *, unsubscribe_url=None):
        sent.append({
            "to": to,
            "subject": subject,
            "html": html,
            "text": text,
            "unsubscribe_url": unsubscribe_url,
        })
        return True

    monkeypatch.setattr(email_service, "_post", fake_post)
    monkeypatch.setattr(
        notifications,
        "_lookup_place_label",
        lambda db, lat, lon: "near Kaneohe Bay",
    )
    return sent


def _post_subscribe(client, email="alice@example.com", imei="all", **kwargs):
    body = {
        "email": email,
        "imei": imei,
        "wants_realtime": True,
        "wants_digest": False,
    }
    body.update(kwargs)
    return client.post("/api/subscribe", json=body)


# ── Subscribe / verify ──


class TestSubscribeFlow:
    def test_subscribe_creates_subscriber_and_token(self, client, db_session, stub_email_sender):
        resp = _post_subscribe(client)
        assert resp.status_code == 200
        assert resp.json()["status"] == "verify_email_sent"

        sub = db_session.query(Subscriber).filter_by(email="alice@example.com").first()
        assert sub is not None
        assert sub.verified_at is None
        assert sub.manage_token

        token = db_session.query(EmailToken).filter_by(subscriber_id=sub.id, purpose="verify").first()
        assert token is not None
        assert any(
            "Confirm" in (e.get("subject") or "") and e["to"] == "alice@example.com"
            for e in stub_email_sender
        )

    def test_subscribe_normalizes_email_case(self, client, db_session):
        _post_subscribe(client, email="ALICE@Example.COM")
        sub = db_session.query(Subscriber).filter_by(email="alice@example.com").first()
        assert sub is not None

    def test_subscribe_rejects_invalid_email(self, client):
        resp = _post_subscribe(client, email="not-an-email")
        assert resp.status_code == 400

    def test_subscribe_specific_imei(self, client, db_session):
        resp = _post_subscribe(client, imei=KNOWN_IMEI)
        assert resp.status_code == 200
        sub = db_session.query(Subscriber).filter_by(email="alice@example.com").first()
        rows = db_session.query(Subscription).filter_by(subscriber_id=sub.id).all()
        assert len(rows) == 1
        assert rows[0].device_imei == KNOWN_IMEI

    def test_subscribe_unknown_imei_rejected(self, client):
        resp = _post_subscribe(client, imei="000000000000000")
        assert resp.status_code == 404

    def test_subscribe_resends_verify_when_unverified(self, client, db_session, stub_email_sender):
        _post_subscribe(client)
        sent_first = len(stub_email_sender)
        _post_subscribe(client, wants_digest=True)
        assert len(stub_email_sender) > sent_first

    def test_subscribe_already_verified_no_verify_email(self, client, db_session, stub_email_sender):
        _post_subscribe(client)
        sub = db_session.query(Subscriber).filter_by(email="alice@example.com").first()
        sub.verified_at = datetime.now(timezone.utc)
        db_session.commit()
        stub_email_sender.clear()

        resp = _post_subscribe(client, wants_digest=True)
        assert resp.status_code == 200
        assert resp.json()["status"] == "subscribed"
        assert all("Confirm" not in (e.get("subject") or "") for e in stub_email_sender)


class TestVerify:
    def test_verify_activates_subscriber(self, client, db_session):
        _post_subscribe(client)
        sub = db_session.query(Subscriber).filter_by(email="alice@example.com").first()
        token = db_session.query(EmailToken).filter_by(subscriber_id=sub.id, purpose="verify").first()

        resp = client.get(f"/api/verify?token={token.token}", follow_redirects=False)
        assert resp.status_code == 302
        assert "subscribed=ok" in resp.headers["location"]
        assert f"token={sub.manage_token}" in resp.headers["location"]

        db_session.refresh(sub)
        db_session.refresh(token)
        assert sub.verified_at is not None
        assert token.used_at is not None

    def test_verify_with_bad_token(self, client):
        resp = client.get("/api/verify?token=bogus", follow_redirects=False)
        assert resp.status_code == 200
        assert "Invalid" in resp.text


class TestSubscriptionsCrud:
    def test_list_subscriptions_via_manage_token(self, client, db_session):
        _post_subscribe(client, imei=KNOWN_IMEI)
        sub = db_session.query(Subscriber).filter_by(email="alice@example.com").first()
        resp = client.get(f"/api/subscriptions?token={sub.manage_token}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "alice@example.com"
        assert len(data["subscriptions"]) == 1
        assert data["subscriptions"][0]["device_imei"] == KNOWN_IMEI

    def test_put_subscriptions_replaces_set(self, client, db_session):
        _post_subscribe(client, imei=KNOWN_IMEI)
        sub = db_session.query(Subscriber).filter_by(email="alice@example.com").first()
        resp = client.put(
            f"/api/subscriptions?token={sub.manage_token}",
            json={"subscriptions": [
                {"device_imei": None, "wants_realtime": True, "wants_digest": True,
                 "digest_frequency": "weekly", "digest_hour_utc": 9, "realtime_throttle_minutes": 5},
            ]},
        )
        assert resp.status_code == 200
        rows = db_session.query(Subscription).filter_by(subscriber_id=sub.id).all()
        assert len(rows) == 1
        assert rows[0].device_imei is None
        assert rows[0].digest_frequency == "weekly"
        assert rows[0].realtime_throttle_minutes == 5

    def test_put_subscriptions_rejects_unknown_imei(self, client, db_session):
        _post_subscribe(client)
        sub = db_session.query(Subscriber).filter_by(email="alice@example.com").first()
        resp = client.put(
            f"/api/subscriptions?token={sub.manage_token}",
            json={"subscriptions": [
                {"device_imei": "000000000000000", "wants_realtime": True, "wants_digest": False,
                 "digest_frequency": "daily", "digest_hour_utc": 13, "realtime_throttle_minutes": 0},
            ]},
        )
        assert resp.status_code == 400

    def test_subscriptions_bad_token(self, client):
        resp = client.get("/api/subscriptions?token=bogus")
        assert resp.status_code == 404


class TestUnsubscribe:
    def test_get_marks_unsubscribed(self, client, db_session):
        _post_subscribe(client)
        sub = db_session.query(Subscriber).filter_by(email="alice@example.com").first()
        unsub_token = notifications.get_or_create_unsubscribe_token(db_session, sub)

        resp = client.get(f"/api/unsubscribe?token={unsub_token}")
        assert resp.status_code == 200
        assert "unsubscribed" in resp.text.lower()
        db_session.refresh(sub)
        assert sub.unsubscribed_at is not None

    def test_post_oneclick_unsubscribe(self, client, db_session):
        """RFC 8058: List-Unsubscribe-Post one-click endpoint."""
        _post_subscribe(client)
        sub = db_session.query(Subscriber).filter_by(email="alice@example.com").first()
        unsub_token = notifications.get_or_create_unsubscribe_token(db_session, sub)

        resp = client.post(f"/api/unsubscribe?token={unsub_token}")
        assert resp.status_code == 200
        db_session.refresh(sub)
        assert sub.unsubscribed_at is not None

    def test_manage_token_also_unsubscribes(self, client, db_session):
        _post_subscribe(client)
        sub = db_session.query(Subscriber).filter_by(email="alice@example.com").first()
        resp = client.post(f"/api/unsubscribe?token={sub.manage_token}")
        assert resp.status_code == 200
        db_session.refresh(sub)
        assert sub.unsubscribed_at is not None

    def test_bad_token(self, client):
        resp = client.get("/api/unsubscribe?token=bogus")
        assert resp.status_code == 200
        assert "Invalid" in resp.text


# ── Realtime dispatch + throttle ──


def _make_message(db_session, imei=KNOWN_IMEI, lat=21.43, lon=-157.79) -> DorisMessage:
    msg = DorisMessage(
        device_imei=imei,
        momsn=1,
        transmit_time="26-04-14 12:00:00",
        iridium_latitude=lat,
        iridium_longitude=lon,
        iridium_cep=3,
        latitude=lat,
        longitude=lon,
        message_type="P",
        message_version="1",
        velocity_dm_s=12,
        course_deg=45,
        battery_voltage=14.5,
        max_depth=5.0,
        status_flags=0,
        raw_data="00",
    )
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)
    return msg


def _make_subscriber(
    db_session, *, verified=True, imei=KNOWN_IMEI, throttle=0, wants_realtime=True
):
    sub = crud.get_or_create_subscriber(db_session, "alice@example.com")
    if verified:
        sub.verified_at = datetime.now(timezone.utc)
        db_session.commit()
    crud.upsert_subscription(
        db_session,
        sub,
        device_imei=imei,
        wants_realtime=wants_realtime,
        wants_digest=False,
        digest_frequency="daily",
        digest_hour_utc=13,
        realtime_throttle_minutes=throttle,
    )
    return sub


class TestRealtimeDispatch:
    def test_sends_to_matching_subscriber(self, db_session, stub_email_sender):
        _make_subscriber(db_session, imei=KNOWN_IMEI)
        msg = _make_message(db_session)

        sent = notifications.dispatch_realtime(db_session, msg)
        assert sent == 1
        assert stub_email_sender[-1]["to"] == "alice@example.com"
        assert stub_email_sender[-1]["unsubscribe_url"]
        assert "/api/unsubscribe" in stub_email_sender[-1]["unsubscribe_url"]

    def test_all_units_subscriber_matches(self, db_session, stub_email_sender):
        _make_subscriber(db_session, imei=None)
        msg = _make_message(db_session)

        sent = notifications.dispatch_realtime(db_session, msg)
        assert sent == 1

    def test_unverified_subscriber_skipped(self, db_session, stub_email_sender):
        _make_subscriber(db_session, verified=False)
        msg = _make_message(db_session)

        sent = notifications.dispatch_realtime(db_session, msg)
        assert sent == 0

    def test_unsubscribed_skipped(self, db_session, stub_email_sender):
        sub = _make_subscriber(db_session)
        sub.unsubscribed_at = datetime.now(timezone.utc)
        db_session.commit()

        msg = _make_message(db_session)
        sent = notifications.dispatch_realtime(db_session, msg)
        assert sent == 0

    def test_throttle_skips_second_within_window(self, db_session, stub_email_sender):
        _make_subscriber(db_session, throttle=5)
        first = _make_message(db_session)
        assert notifications.dispatch_realtime(db_session, first) == 1
        second = _make_message(db_session)
        assert notifications.dispatch_realtime(db_session, second) == 0

    def test_no_throttle_sends_all(self, db_session, stub_email_sender):
        _make_subscriber(db_session, throttle=0)
        first = _make_message(db_session)
        second = _make_message(db_session)
        assert notifications.dispatch_realtime(db_session, first) == 1
        assert notifications.dispatch_realtime(db_session, second) == 1

    def test_unknown_device_is_noop(self, db_session, stub_email_sender):
        msg = _make_message(db_session, imei="999999999999999")
        assert notifications.dispatch_realtime(db_session, msg) == 0
