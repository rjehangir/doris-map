"""HTML + plain-text email templates for DORIS subscriptions.

Pure functions that take ORM objects and return ``(subject, html, text)`` tuples.
Templates are intentionally inline (no Jinja) so they are dependency-free and
trivial to read.
"""

from __future__ import annotations

import html as html_lib
from typing import Iterable, Optional
from urllib.parse import quote

import models


# ── Geo formatting helpers ──


def _decimal_to_dms(value: float, positive: str, negative: str) -> str:
    if value is None:
        return ""
    hemi = positive if value >= 0 else negative
    abs_val = abs(value)
    deg = int(abs_val)
    minutes_full = (abs_val - deg) * 60
    minutes = int(minutes_full)
    seconds = (minutes_full - minutes) * 60
    return f"{deg}° {minutes}' {seconds:.2f}\" {hemi}"


def format_dms(lat: float, lon: float) -> str:
    return f"{_decimal_to_dms(lat, 'N', 'S')}, {_decimal_to_dms(lon, 'E', 'W')}"


def format_decimal(lat: float, lon: float) -> str:
    return f"{lat:.6f}, {lon:.6f}"


def google_maps_url(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={lat:.6f},{lon:.6f}"


def apple_maps_url(lat: float, lon: float, label: Optional[str] = None) -> str:
    base = f"https://maps.apple.com/?ll={lat:.6f},{lon:.6f}&q="
    return base + quote(label or f"{lat:.4f},{lon:.4f}")


def geo_uri(lat: float, lon: float) -> str:
    return f"geo:{lat:.6f},{lon:.6f}"


def static_map_url(lat: float, lon: float, zoom: int = 11) -> str:
    # staticmap.openstreetmap.de — free, no key required.
    return (
        "https://staticmap.openstreetmap.de/staticmap.php"
        f"?center={lat:.6f},{lon:.6f}"
        f"&zoom={zoom}&size=600x300&maptype=mapnik"
        f"&markers={lat:.6f},{lon:.6f},red-pushpin"
    )


def deep_link(base_url: str, imei: str, message_id: Optional[int] = None) -> str:
    if message_id is not None:
        return f"{base_url}/ui#device={imei}&msg={message_id}"
    return f"{base_url}/ui#device={imei}"


def manage_url(base_url: str, manage_token: str) -> str:
    return f"{base_url}/api/manage?token={manage_token}"


# ── Shared HTML chrome ──

_PALETTE = {
    "bg": "#0E2446",
    "panel": "#13315C",
    "accent": "#41B9C3",
    "accent_light": "#96EEF2",
    "text": "#FFFFFF",
    "muted": "#A4B7CC",
    "warn": "#FFB347",
    "danger": "#DD2C1D",
}


def _wrapper(inner_html: str, *, preheader: str = "") -> str:
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body style="margin:0; padding:0; background:{_PALETTE['bg']}; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; color:{_PALETTE['text']};">
<span style="display:none!important;visibility:hidden;opacity:0;height:0;width:0;overflow:hidden">{html_lib.escape(preheader)}</span>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:{_PALETTE['bg']};">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellspacing="0" cellpadding="0" style="max-width:600px; background:{_PALETTE['panel']}; border-radius:12px; overflow:hidden;">
<tr><td style="padding:20px 24px 8px;">
  <div style="font-size:0.78rem; letter-spacing:1px; color:{_PALETTE['accent']}; text-transform:uppercase; font-weight:600;">DORIS Tracker</div>
</td></tr>
{inner_html}
</table>
<div style="font-size:0.7rem; color:{_PALETTE['muted']}; padding:14px 8px 0; max-width:600px;">
You're receiving this because you subscribed to notifications from the DORIS Tracker.
</div>
</td></tr>
</table>
</body></html>"""


def _button(href: str, label: str, *, bg: Optional[str] = None) -> str:
    bg = bg or _PALETTE["accent"]
    return (
        f'<a href="{html_lib.escape(href)}" '
        f'style="display:inline-block; padding:10px 16px; margin:4px 4px 4px 0; '
        f'background:{bg}; color:#0E2446; text-decoration:none; font-weight:600; '
        f'border-radius:8px; font-size:0.9rem;">{html_lib.escape(label)}</a>'
    )


def _footer_links(manage_link: str, unsubscribe_link: Optional[str]) -> str:
    parts = [
        f'<a href="{html_lib.escape(manage_link)}" style="color:{_PALETTE["accent_light"]};">Manage subscriptions</a>'
    ]
    if unsubscribe_link:
        parts.append(
            f'<a href="{html_lib.escape(unsubscribe_link)}" style="color:{_PALETTE["accent_light"]};">Unsubscribe</a>'
        )
    return (
        f'<tr><td style="padding:8px 24px 24px; font-size:0.78rem; color:{_PALETTE["muted"]}; '
        f'border-top:1px solid rgba(255,255,255,0.08);">'
        + " &nbsp;·&nbsp; ".join(parts)
        + "</td></tr>"
    )


# ── Verification email ──


def build_verification_email(
    *, subscriber: models.Subscriber, verify_token: str, base_url: str
) -> tuple[str, str, str]:
    verify_url = f"{base_url.rstrip('/')}/api/verify?token={verify_token}"
    subject = "Confirm your DORIS Tracker subscription"
    inner = f"""
<tr><td style="padding:8px 24px 4px;">
  <h2 style="margin:0 0 8px; font-size:1.25rem; color:{_PALETTE['text']};">Confirm your subscription</h2>
  <p style="margin:0 0 12px; color:{_PALETTE['muted']}; font-size:0.95rem; line-height:1.5;">
    Click the button below to confirm <strong style="color:{_PALETTE['accent_light']};">{html_lib.escape(subscriber.email)}</strong>
    and start receiving DORIS notifications. If you didn't request this, you can ignore this email.
  </p>
  <div style="margin:16px 0 8px;">{_button(verify_url, "Confirm subscription")}</div>
  <p style="margin:12px 0 0; color:{_PALETTE['muted']}; font-size:0.78rem;">
    Or paste this link into your browser:<br>
    <span style="color:{_PALETTE['accent_light']}; word-break:break-all;">{html_lib.escape(verify_url)}</span>
  </p>
</td></tr>
"""
    text = (
        "Confirm your DORIS Tracker subscription\n\n"
        f"Confirm {subscriber.email} by visiting:\n{verify_url}\n\n"
        "If you didn't request this, just ignore this email."
    )
    return subject, _wrapper(inner, preheader="Confirm your DORIS Tracker subscription"), text


# ── Realtime per-message email ──


def _telemetry_rows(message: models.DorisMessage) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if message.transmit_time:
        rows.append(("Transmit time (UTC)", str(message.transmit_time)))
    if message.battery_voltage is not None:
        rows.append(("Battery", f"{message.battery_voltage:.2f} V"))
    if message.leak_detected is not None:
        rows.append(("Leak detected", "YES" if message.leak_detected else "no"))
    if message.max_depth is not None:
        rows.append(("Max depth", f"{message.max_depth:.2f} m"))
    if message.altitude is not None:
        rows.append(("Altitude", f"{message.altitude:.1f} m"))
    if message.satellite_count is not None:
        rows.append(("GPS satellites", str(message.satellite_count)))
    if message.momsn is not None:
        rows.append(("MOMSN", str(message.momsn)))
    return rows


def _telemetry_html(message: models.DorisMessage) -> str:
    rows = _telemetry_rows(message)
    if not rows:
        return ""
    cells = "".join(
        f'<tr><td style="padding:4px 12px 4px 0; color:{_PALETTE["muted"]}; font-size:0.85rem;">{html_lib.escape(k)}</td>'
        f'<td style="padding:4px 0; color:{_PALETTE["text"]}; font-size:0.9rem;">{html_lib.escape(v)}</td></tr>'
        for k, v in rows
    )
    return f'<table role="presentation" cellspacing="0" cellpadding="0">{cells}</table>'


def _telemetry_text(message: models.DorisMessage) -> str:
    return "\n".join(f"  {k}: {v}" for k, v in _telemetry_rows(message))


def build_realtime_email(
    *,
    subscriber: models.Subscriber,
    sub: models.Subscription,
    device: models.Device,
    message: models.DorisMessage,
    place_label: Optional[str],
    base_url: str,
    unsubscribe_url: Optional[str],
) -> tuple[str, str, str]:
    lat = message.latitude
    lon = message.longitude
    leak = bool(message.leak_detected)
    place = place_label or ""

    subject_bits = [device.name]
    if leak:
        subject_bits.append("LEAK DETECTED")
    if place:
        subject_bits.append(place)
    subject = " — ".join(subject_bits) if leak else f"{device.name} update" + (
        f" — {place}" if place else ""
    )

    deep = deep_link(base_url, device.imei, message.id)
    gmap = google_maps_url(lat, lon)
    amap = apple_maps_url(lat, lon, label=device.name)
    geo = geo_uri(lat, lon)
    smap = static_map_url(lat, lon)
    manage_link = manage_url(base_url, subscriber.manage_token)

    leak_banner = (
        f'<div style="margin:8px 0 12px; padding:10px 14px; background:rgba(221,44,29,0.18); '
        f'border:1px solid {_PALETTE["danger"]}; border-radius:8px; color:#FFD7D2; font-weight:600;">'
        f'Leak detected on {html_lib.escape(device.name)}.</div>'
        if leak
        else ""
    )

    inner = f"""
<tr><td style="padding:4px 24px 0;">
  <h2 style="margin:0 0 4px; font-size:1.3rem;">{html_lib.escape(device.name)}</h2>
  <div style="color:{_PALETTE['muted']}; font-size:0.85rem; margin-bottom:8px;">
    IMEI {html_lib.escape(device.imei)}{' &nbsp;·&nbsp; ' + html_lib.escape(place) if place else ''}
  </div>
  {leak_banner}
  <a href="{html_lib.escape(deep)}" style="display:block; text-decoration:none;">
    <img src="{html_lib.escape(smap)}" alt="Map" width="552" style="width:100%; max-width:552px; border-radius:8px; display:block; margin:8px 0;">
  </a>
  <div style="margin:6px 0 12px;">
    {_button(deep, "Open on tracker map")}
    {_button(gmap, "Google Maps", bg=_PALETTE['accent_light'])}
    {_button(amap, "Apple Maps", bg=_PALETTE['accent_light'])}
  </div>
  <div style="margin:8px 0 4px; color:{_PALETTE['muted']}; font-size:0.78rem;">
    On Android, this link opens your default map app:
    <a href="{html_lib.escape(geo)}" style="color:{_PALETTE['accent_light']};">{html_lib.escape(geo)}</a>
  </div>
  <table role="presentation" cellspacing="0" cellpadding="0" style="margin-top:12px; width:100%;">
    <tr>
      <td style="padding:6px 8px 6px 0; vertical-align:top; width:55%;">
        <div style="color:{_PALETTE['muted']}; font-size:0.78rem; text-transform:uppercase; letter-spacing:1px;">Position</div>
        <div style="font-size:0.95rem; color:{_PALETTE['text']};">{html_lib.escape(format_decimal(lat, lon))}</div>
        <div style="font-size:0.85rem; color:{_PALETTE['accent_light']};">{html_lib.escape(format_dms(lat, lon))}</div>
      </td>
      <td style="vertical-align:top;">
        <div style="color:{_PALETTE['muted']}; font-size:0.78rem; text-transform:uppercase; letter-spacing:1px;">Telemetry</div>
        {_telemetry_html(message)}
      </td>
    </tr>
  </table>
</td></tr>
{_footer_links(manage_link, unsubscribe_url)}
"""

    text_lines = [
        f"{device.name} — {place or 'position update'}",
        f"IMEI: {device.imei}",
        "",
        f"Position: {format_decimal(lat, lon)}",
        f"          {format_dms(lat, lon)}",
        "",
        "Telemetry:",
        _telemetry_text(message),
        "",
        f"Open on tracker map: {deep}",
        f"Google Maps:         {gmap}",
        f"Apple Maps:          {amap}",
        f"Geo URI:             {geo}",
        "",
        f"Manage subscriptions: {manage_link}",
    ]
    if unsubscribe_url:
        text_lines.append(f"Unsubscribe:          {unsubscribe_url}")
    text = "\n".join(text_lines)

    preheader = (
        f"{device.name} {('leak detected ' if leak else '')}at {format_decimal(lat, lon)}"
    )
    return subject, _wrapper(inner, preheader=preheader), text


# ── Digest email ──


def build_digest_email(
    *,
    subscriber: models.Subscriber,
    rollups: Iterable[dict],
    frequency: str,
    base_url: str,
    unsubscribe_url: Optional[str],
) -> tuple[str, str, str]:
    """Each rollup dict: {device, count, first_msg, last_msg, leak_events,
    min_battery, max_battery, max_depth, place_label}."""
    rollup_list = list(rollups)
    span = "Daily" if frequency == "daily" else "Weekly"
    subject = f"{span} DORIS digest — {len(rollup_list)} unit(s)"
    manage_link = manage_url(base_url, subscriber.manage_token)

    cards_html = []
    text_blocks: list[str] = []
    for r in rollup_list:
        device: models.Device = r["device"]
        last: models.DorisMessage = r["last_msg"]
        first: models.DorisMessage = r["first_msg"]
        deep = deep_link(base_url, device.imei, last.id)
        place = r.get("place_label") or ""
        leak_badge = (
            f'<span style="display:inline-block; padding:2px 8px; background:{_PALETTE["danger"]}; '
            f'color:white; border-radius:6px; font-size:0.72rem; margin-left:6px;">LEAK</span>'
            if r.get("leak_events")
            else ""
        )
        battery_line = ""
        if r.get("min_battery") is not None and r.get("max_battery") is not None:
            battery_line = (
                f'<div style="color:{_PALETTE["muted"]}; font-size:0.82rem;">'
                f'Battery: {r["min_battery"]:.2f}–{r["max_battery"]:.2f} V</div>'
            )
        depth_line = ""
        if r.get("max_depth") is not None:
            depth_line = (
                f'<div style="color:{_PALETTE["muted"]}; font-size:0.82rem;">'
                f'Max depth: {r["max_depth"]:.2f} m</div>'
            )

        cards_html.append(
            f"""
<tr><td style="padding:10px 24px;">
  <div style="background:rgba(255,255,255,0.04); border-radius:10px; padding:12px 14px;">
    <div style="font-weight:600; font-size:1rem;">{html_lib.escape(device.name)}{leak_badge}</div>
    <div style="color:{_PALETTE['muted']}; font-size:0.78rem; margin-bottom:6px;">
      {r['count']} message(s){' · ' + html_lib.escape(place) if place else ''}
    </div>
    <div style="font-size:0.88rem;">Last position: {html_lib.escape(format_decimal(last.latitude, last.longitude))}</div>
    {battery_line}
    {depth_line}
    <div style="margin-top:8px;">{_button(deep, "Open on tracker map")}</div>
  </div>
</td></tr>"""
        )

        block = [
            f"{device.name}{' [LEAK]' if r.get('leak_events') else ''}",
            f"  {r['count']} message(s){' · ' + place if place else ''}",
            f"  Last position: {format_decimal(last.latitude, last.longitude)}",
        ]
        if r.get("min_battery") is not None and r.get("max_battery") is not None:
            block.append(f"  Battery: {r['min_battery']:.2f}–{r['max_battery']:.2f} V")
        if r.get("max_depth") is not None:
            block.append(f"  Max depth: {r['max_depth']:.2f} m")
        block.append(f"  Open: {deep}")
        text_blocks.append("\n".join(block))

    inner = f"""
<tr><td style="padding:4px 24px 0;">
  <h2 style="margin:0 0 4px; font-size:1.3rem;">{span} DORIS digest</h2>
  <div style="color:{_PALETTE['muted']}; font-size:0.85rem;">{len(rollup_list)} unit(s) reported in</div>
</td></tr>
{''.join(cards_html)}
{_footer_links(manage_link, unsubscribe_url)}
"""

    text_lines = [
        f"{span} DORIS digest",
        f"{len(rollup_list)} unit(s) reported in",
        "",
        "\n\n".join(text_blocks),
        "",
        f"Manage subscriptions: {manage_link}",
    ]
    if unsubscribe_url:
        text_lines.append(f"Unsubscribe:          {unsubscribe_url}")
    return subject, _wrapper(inner, preheader=f"{span} DORIS digest"), "\n".join(text_lines)


# ── Unsubscribed confirmation ──


def build_unsubscribed_email(
    *, subscriber: models.Subscriber, base_url: str
) -> tuple[str, str, str]:
    subject = "You've been unsubscribed from DORIS Tracker"
    inner = f"""
<tr><td style="padding:8px 24px 24px;">
  <h2 style="margin:0 0 8px; font-size:1.2rem;">You're unsubscribed</h2>
  <p style="margin:0; color:{_PALETTE['muted']}; font-size:0.95rem; line-height:1.5;">
    {html_lib.escape(subscriber.email)} will no longer receive notifications.
    Changed your mind? You can sign up again at any time at
    <a href="{html_lib.escape(base_url)}/ui" style="color:{_PALETTE['accent_light']};">{html_lib.escape(base_url)}/ui</a>.
  </p>
</td></tr>
"""
    text = (
        "You're unsubscribed from DORIS Tracker.\n\n"
        f"{subscriber.email} will no longer receive notifications.\n"
        f"Re-subscribe any time at {base_url}/ui"
    )
    return subject, _wrapper(inner, preheader="You're unsubscribed from DORIS Tracker"), text
