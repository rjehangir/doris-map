"""HTML + plain-text email templates for DORIS subscriptions.

Pure functions that take ORM objects and return ``(subject, html, text)`` tuples.
Templates are intentionally inline (no Jinja) so they are dependency-free and
trivial to read.
"""

from __future__ import annotations

import html as html_lib
import os
from datetime import datetime, timezone
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


def static_map_url(
    lat: float,
    lon: float,
    zoom: int = 12,
    *,
    width: int = 600,
    height: int = 300,
) -> Optional[str]:
    """Return a static map image URL for the given coords, or None if no
    provider is configured.

    Providers are tried in order of preference:

    - Mapbox (``MAPBOX_TOKEN``) — 50k free static requests/month.
    - MapTiler (``MAPTILER_KEY``) — 100k free requests/month.

    Callers must handle ``None`` by omitting the ``<img>``.
    """
    mapbox = os.getenv("MAPBOX_TOKEN")
    if mapbox:
        style = os.getenv("MAPBOX_STYLE", "mapbox/outdoors-v12")
        pin = f"pin-s+ff3b30({lon:.6f},{lat:.6f})"
        return (
            f"https://api.mapbox.com/styles/v1/{style}/static/"
            f"{pin}/{lon:.6f},{lat:.6f},{zoom}/"
            f"{width}x{height}@2x?access_token={mapbox}"
        )

    maptiler = os.getenv("MAPTILER_KEY")
    if maptiler:
        style = os.getenv("MAPTILER_STYLE", "outdoor-v2")
        return (
            f"https://api.maptiler.com/maps/{style}/static/"
            f"{lon:.6f},{lat:.6f},{zoom}/{width}x{height}@2x.png"
            f"?key={maptiler}&markers={lon:.6f},{lat:.6f}"
        )

    return None


def deep_link(base_url: str, imei: str, message_id: Optional[int] = None) -> str:
    if message_id is not None:
        return f"{base_url}/ui#device={imei}&msg={message_id}"
    return f"{base_url}/ui#device={imei}"


def manage_url(base_url: str, manage_token: str) -> str:
    return f"{base_url}/api/manage?token={manage_token}"


# ── Shared HTML chrome ──

# Light-theme palette. Solid colors only (no rgba) so Outlook Desktop and
# other older clients render reliably. All font-sizes are declared in px
# elsewhere for the same reason.
_PALETTE = {
    "body_bg":      "#EEF2F7",
    "card_bg":      "#FFFFFF",
    "card_border":  "#E1E7EF",
    "row_bg":       "#F5F8FB",     # digest cards + subtle stripes
    "row_border":   "#E1E7EF",
    "header_bg":    "#0E2446",     # DORIS navy — kept as accent bar
    "header_text":  "#FFFFFF",
    "header_accent":"#96EEF2",
    "text":         "#1A2340",
    "muted":        "#64748B",
    "accent":       "#187D8B",     # button primary
    "accent_hover": "#116373",
    "accent_soft":  "#E0F7FA",     # pill/secondary bg
    "link":         "#187D8B",
    "warn_bg":      "#FEF3C7",
    "warn_text":    "#92400E",
    "warn_border":  "#F59E0B",
    "danger_bg":    "#FEE2E2",
    "danger_text":  "#991B1B",
    "danger_border":"#DC2626",
}


def _wrapper(inner_html: str, *, preheader: str = "") -> str:
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<meta name="supported-color-schemes" content="light">
</head>
<body style="margin:0; padding:0; background:{_PALETTE['body_bg']}; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif; color:{_PALETTE['text']};">
<span style="display:none!important;visibility:hidden;opacity:0;height:0;width:0;overflow:hidden">{html_lib.escape(preheader)}</span>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:{_PALETTE['body_bg']};">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellspacing="0" cellpadding="0" style="max-width:600px; background:{_PALETTE['card_bg']}; border-radius:12px; overflow:hidden; border:1px solid {_PALETTE['card_border']};">
<tr><td style="background:{_PALETTE['header_bg']}; padding:14px 24px; color:{_PALETTE['header_text']};">
  <div style="font-size:13px; letter-spacing:2px; color:{_PALETTE['header_accent']}; text-transform:uppercase; font-weight:600;">DORIS Tracker</div>
</td></tr>
{inner_html}
</table>
<div style="font-size:11px; color:{_PALETTE['muted']}; padding:14px 8px 0; max-width:600px; text-align:center;">
You're receiving this because you subscribed to notifications from the DORIS Tracker.
</div>
</td></tr>
</table>
</body></html>"""


def _button(
    href: str,
    label: str,
    *,
    bg: Optional[str] = None,
    color: Optional[str] = None,
    border: Optional[str] = None,
) -> str:
    """Solid pill-shape button. Defaults to primary (teal); pass overrides for
    secondary/tertiary variants."""
    bg = bg or _PALETTE["accent"]
    color = color or "#FFFFFF"
    border_css = f"border:1px solid {border};" if border else "border:0;"
    return (
        f'<a href="{html_lib.escape(href)}" '
        f'style="display:inline-block; padding:10px 16px; margin:4px 4px 4px 0; '
        f'background:{bg}; color:{color}; text-decoration:none; font-weight:600; '
        f'border-radius:8px; font-size:14px; {border_css}">{html_lib.escape(label)}</a>'
    )


def _secondary_button(href: str, label: str) -> str:
    return _button(
        href,
        label,
        bg=_PALETTE["accent_soft"],
        color=_PALETTE["accent"],
        border=_PALETTE["accent_soft"],
    )


def _footer_links(manage_link: str, unsubscribe_link: Optional[str]) -> str:
    manage_html = (
        f'<a href="{html_lib.escape(manage_link)}" '
        f'style="color:{_PALETTE["link"]}; text-decoration:none; font-weight:600;">'
        f'Manage subscriptions</a>'
    )
    unsub_html = (
        f'<a href="{html_lib.escape(unsubscribe_link)}" '
        f'style="display:inline-block; padding:6px 12px; border:1px solid {_PALETTE["card_border"]}; '
        f'border-radius:6px; color:{_PALETTE["muted"]}; text-decoration:none; font-weight:500; '
        f'background:{_PALETTE["card_bg"]};">Unsubscribe</a>'
        if unsubscribe_link
        else ""
    )
    # White spacer row above the gray footer strip so the "Manage subscriptions"
    # area isn't visually crushed against the content above.
    return (
        f'<tr><td style="height:28px; line-height:28px; font-size:0; background:{_PALETTE["card_bg"]};">&nbsp;</td></tr>'
        f'<tr><td style="padding:18px 24px 22px; font-size:13px; color:{_PALETTE["muted"]}; '
        f'border-top:1px solid {_PALETTE["card_border"]}; background:{_PALETTE["row_bg"]};">'
        f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>'
        f'<td style="vertical-align:middle;">{manage_html}</td>'
        f'<td align="right" style="vertical-align:middle;">{unsub_html}</td>'
        f'</tr></table></td></tr>'
    )


# ── Time formatting ──


def _parse_rockblock_time(s: str) -> Optional[datetime]:
    """Parse the RockBLOCK ``YY-MM-DD HH:MM:SS`` format (UTC assumed)."""
    if not s:
        return None
    s = s.strip()
    for fmt in ("%y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def format_transmit_time(s: str) -> str:
    """Return a friendly ``Jul 5, 2026 22:14 UTC`` string, falling back to raw."""
    dt = _parse_rockblock_time(s)
    if dt is None:
        return s
    return dt.strftime("%b %-d, %Y %H:%M UTC")


def _time_ago(dt: Optional[datetime], now: Optional[datetime] = None) -> str:
    if dt is None:
        return ""
    now = now or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "just now"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    days = seconds // 86400
    return f"{days}d ago"


# ── Verification email ──


def build_verification_email(
    *, subscriber: models.Subscriber, verify_token: str, base_url: str
) -> tuple[str, str, str]:
    verify_url = f"{base_url.rstrip('/')}/api/verify?token={verify_token}"
    subject = "Confirm your DORIS Tracker subscription"
    inner = f"""
<tr><td style="padding:24px;">
  <h2 style="margin:0 0 10px; font-size:20px; color:{_PALETTE['text']};">Confirm your subscription</h2>
  <p style="margin:0 0 14px; color:{_PALETTE['text']}; font-size:15px; line-height:1.55;">
    Click the button below to confirm <strong>{html_lib.escape(subscriber.email)}</strong>
    and start receiving DORIS notifications.
  </p>
  <div style="margin:18px 0 12px;">{_button(verify_url, "Confirm subscription")}</div>
  <p style="margin:14px 0 0; color:{_PALETTE['muted']}; font-size:13px;">
    Or paste this link into your browser:<br>
    <span style="color:{_PALETTE['link']}; word-break:break-all;">{html_lib.escape(verify_url)}</span>
  </p>
  <div style="margin-top:20px; padding:12px 14px; background:{_PALETTE['row_bg']}; border:1px solid {_PALETTE['row_border']}; border-radius:8px; color:{_PALETTE['muted']}; font-size:13px; line-height:1.5;">
    <strong style="color:{_PALETTE['text']};">Didn't sign up?</strong> Someone may have typed
    your address by mistake — you can safely ignore this email and no account will be created.
    <br><br>
    <strong style="color:{_PALETTE['text']};">Heads up:</strong> this link expires in 7 days.
  </div>
</td></tr>
"""
    text = (
        "Confirm your DORIS Tracker subscription\n\n"
        f"Confirm {subscriber.email} by visiting:\n{verify_url}\n\n"
        "This link expires in 7 days.\n\n"
        "If you didn't sign up, just ignore this email — no account will be created."
    )
    return subject, _wrapper(inner, preheader="Confirm your DORIS Tracker subscription"), text


# ── Realtime per-message email ──


LOW_BATTERY_THRESHOLD_V = 12.0


def _telemetry_rows(message: models.DorisMessage) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    if message.transmit_time:
        rows.append(("Reported", format_transmit_time(str(message.transmit_time))))
    if message.battery_voltage is not None:
        rows.append(("Battery", f"{message.battery_voltage:.1f} V"))
    if message.max_depth is not None:
        rows.append(("Max depth", f"{message.max_depth:.0f} m"))
    if message.velocity_dm_s is not None:
        rows.append(("GPS speed", f"{message.velocity_dm_s / 10:.1f} m/s"))
    if message.course_deg is not None:
        rows.append(("GPS course", f"{message.course_deg}°"))
    if message.momsn is not None:
        rows.append(("MOMSN", str(message.momsn)))
    return rows


def _telemetry_html(message: models.DorisMessage) -> str:
    rows = _telemetry_rows(message)
    if not rows:
        return ""
    cells = "".join(
        f'<tr>'
        f'<td style="padding:5px 14px 5px 0; color:{_PALETTE["muted"]}; font-size:13px; vertical-align:top; white-space:nowrap;">{html_lib.escape(k)}</td>'
        f'<td style="padding:5px 0; color:{_PALETTE["text"]}; font-size:14px; vertical-align:top;">{html_lib.escape(v)}</td>'
        f'</tr>'
        for k, v in rows
    )
    return f'<table role="presentation" cellspacing="0" cellpadding="0" width="100%">{cells}</table>'


def _telemetry_text(message: models.DorisMessage) -> str:
    return "\n".join(f"  {k}: {v}" for k, v in _telemetry_rows(message))


def _banner(bg: str, border: str, text_color: str, body_html: str) -> str:
    return (
        f'<div style="margin:12px 0; padding:12px 14px; background:{bg}; '
        f'border:1px solid {border}; border-radius:8px; color:{text_color}; '
        f'font-weight:600; font-size:14px; line-height:1.4;">{body_html}</div>'
    )


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
    place = place_label or ""  # "near Kaneohe Bay" or ""
    battery_v = message.battery_voltage
    low_battery = battery_v is not None and battery_v < LOW_BATTERY_THRESHOLD_V

    subject = f"{device.name} {place}" if place else f"{device.name} position update"

    deep = deep_link(base_url, device.imei, message.id)
    gmap = google_maps_url(lat, lon)
    amap = apple_maps_url(lat, lon, label=device.name)
    geo = geo_uri(lat, lon)
    smap = static_map_url(lat, lon)
    manage_link = manage_url(base_url, subscriber.manage_token)

    banners: list[str] = []
    if low_battery:
        banners.append(
            _banner(
                _PALETTE["warn_bg"],
                _PALETTE["warn_border"],
                _PALETTE["warn_text"],
                f"Low battery: {battery_v:.1f}V (threshold {LOW_BATTERY_THRESHOLD_V:.1f}V).",
            )
        )

    map_html = (
        f'<a href="{html_lib.escape(deep)}" style="display:block; text-decoration:none;">'
        f'<img src="{html_lib.escape(smap)}" alt="Map showing {html_lib.escape(device.name)} position" '
        f'width="552" height="276" '
        f'style="width:100%; max-width:552px; height:auto; border-radius:8px; display:block; '
        f'margin:8px 0; border:1px solid {_PALETTE["card_border"]};">'
        f'</a>'
        if smap
        else ""
    )

    place_line = (
        f'<div style="color:{_PALETTE["muted"]}; font-size:14px; margin:2px 0 0;">'
        f'{html_lib.escape(place)}</div>'
        if place
        else ""
    )

    inner = f"""
<tr><td style="padding:22px 24px 4px;">
  <h2 style="margin:0; font-size:22px; color:{_PALETTE['text']};">{html_lib.escape(device.name)}</h2>
  {place_line}
  <div style="color:{_PALETTE['muted']}; font-size:12px; margin:4px 0 0; letter-spacing:0.5px;">
    IMEI {html_lib.escape(device.imei)}
  </div>
  {''.join(banners)}
  {map_html}
  <div style="margin:10px 0 14px;">
    {_button(deep, "Open on tracker map")}
    {_secondary_button(gmap, "Google Maps")}
    {_secondary_button(amap, "Apple Maps")}
  </div>
  <div style="margin:6px 0 4px; color:{_PALETTE['muted']}; font-size:12px;">
    On Android, this opens your default map app:
    <a href="{html_lib.escape(geo)}" style="color:{_PALETTE['link']}; word-break:break-all;">{html_lib.escape(geo)}</a>
  </div>

  <!-- Position section (full-width, mobile-friendly) -->
  <div style="margin-top:20px; padding:14px 16px; background:{_PALETTE['row_bg']}; border:1px solid {_PALETTE['row_border']}; border-radius:10px;">
    <div style="color:{_PALETTE['muted']}; font-size:11px; text-transform:uppercase; letter-spacing:1.2px; margin-bottom:6px;">Position</div>
    <div style="font-size:16px; color:{_PALETTE['text']}; font-weight:600;">{html_lib.escape(format_decimal(lat, lon))}</div>
    <div style="font-size:13px; color:{_PALETTE['muted']}; margin-top:2px;">{html_lib.escape(format_dms(lat, lon))}</div>
  </div>

  <!-- Telemetry section -->
  <div style="margin-top:12px; padding:14px 16px; background:{_PALETTE['row_bg']}; border:1px solid {_PALETTE['row_border']}; border-radius:10px;">
    <div style="color:{_PALETTE['muted']}; font-size:11px; text-transform:uppercase; letter-spacing:1.2px; margin-bottom:6px;">Telemetry</div>
    {_telemetry_html(message)}
  </div>
</td></tr>
{_footer_links(manage_link, unsubscribe_url)}
"""

    text_lines = [
        f"{device.name}{' — ' + place if place else ''}",
        f"IMEI: {device.imei}",
    ]
    if low_battery:
        text_lines.append(f"[Low battery: {battery_v:.1f}V]")
    text_lines += [
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

    # Preheader: informative first-line summary for Gmail's inbox preview
    preheader_bits: list[str] = []
    preheader_bits.append(f"{device.name} checked in")
    if place:
        preheader_bits.append(place)
    if battery_v is not None:
        preheader_bits.append(f"battery {battery_v:.1f}V")
    preheader = " · ".join(preheader_bits)

    return subject, _wrapper(inner, preheader=preheader), text


# ── Digest email ──


def build_digest_email(
    *,
    subscriber: models.Subscriber,
    rollups: Iterable[dict],
    frequency: str,
    base_url: str,
    unsubscribe_url: Optional[str],
    now: Optional[datetime] = None,
) -> tuple[str, str, str]:
    """Each rollup dict: {device, count, first_msg, last_msg,
    min_battery, max_battery, max_depth, place_label}."""
    rollup_list = list(rollups)
    now = now or datetime.now(timezone.utc)
    span = "Daily" if frequency == "daily" else "Weekly"
    unit_count = len(rollup_list)
    subject = f"{span} DORIS digest — {unit_count} unit(s)"
    manage_link = manage_url(base_url, subscriber.manage_token)

    dateline = now.strftime("For %A, %b %-d, %Y (UTC)")

    cards_html = []
    text_blocks: list[str] = []
    for r in rollup_list:
        device: models.Device = r["device"]
        last: models.DorisMessage = r["last_msg"]
        deep = deep_link(base_url, device.imei, last.id)
        place = r.get("place_label") or ""

        last_seen_dt = getattr(last, "created_at", None) or _parse_rockblock_time(
            getattr(last, "transmit_time", "") or ""
        )
        last_seen = _time_ago(last_seen_dt, now) if last_seen_dt else ""

        detail_lines: list[str] = []
        if r.get("min_battery") is not None and r.get("max_battery") is not None:
            detail_lines.append(f'Battery: {r["min_battery"]:.1f}–{r["max_battery"]:.1f} V')
        if r.get("max_depth") is not None:
            detail_lines.append(f'Max depth: {r["max_depth"]:.0f} m')
        if getattr(last, "velocity_dm_s", None) is not None:
            detail_lines.append(f"GPS speed: {last.velocity_dm_s / 10:.1f} m/s")
        if getattr(last, "course_deg", None) is not None:
            detail_lines.append(f"GPS course: {last.course_deg}°")
        detail_html = "".join(
            f'<div style="color:{_PALETTE["muted"]}; font-size:13px; margin-top:2px;">{html_lib.escape(d)}</div>'
            for d in detail_lines
        )

        meta_bits: list[str] = [f'{r["count"]} message(s)']
        if last_seen:
            meta_bits.append(f'last update {last_seen}')
        if place:
            meta_bits.append(place)
        meta_line = " · ".join(html_lib.escape(m) for m in meta_bits)

        cards_html.append(
            f"""
<tr><td style="padding:8px 24px;">
  <div style="background:{_PALETTE['row_bg']}; border:1px solid {_PALETTE['row_border']}; border-radius:10px; padding:14px 16px;">
    <div style="font-weight:600; font-size:16px; color:{_PALETTE['text']};">{html_lib.escape(device.name)}</div>
    <div style="color:{_PALETTE['muted']}; font-size:12px; margin:4px 0 8px;">{meta_line}</div>
    <div style="font-size:14px; color:{_PALETTE['text']};">Last position: {html_lib.escape(format_decimal(last.latitude, last.longitude))}</div>
    {detail_html}
    <div style="margin-top:10px;">{_button(deep, "Open on tracker map")}</div>
  </div>
</td></tr>"""
        )

        block = [
            device.name,
            f"  {r['count']} message(s)"
            + (f" · last update {last_seen}" if last_seen else "")
            + (f" · {place}" if place else ""),
            f"  Last position: {format_decimal(last.latitude, last.longitude)}",
        ]
        if r.get("min_battery") is not None and r.get("max_battery") is not None:
            block.append(f"  Battery: {r['min_battery']:.1f}–{r['max_battery']:.1f} V")
        if r.get("max_depth") is not None:
            block.append(f"  Max depth: {r['max_depth']:.0f} m")
        if getattr(last, "velocity_dm_s", None) is not None:
            block.append(f"  GPS speed: {last.velocity_dm_s / 10:.1f} m/s")
        if getattr(last, "course_deg", None) is not None:
            block.append(f"  GPS course: {last.course_deg}°")
        block.append(f"  Open: {deep}")
        text_blocks.append("\n".join(block))

    inner = f"""
<tr><td style="padding:22px 24px 4px;">
  <h2 style="margin:0; font-size:22px; color:{_PALETTE['text']};">{span} DORIS digest</h2>
  <div style="color:{_PALETTE['muted']}; font-size:13px; margin-top:4px;">{html_lib.escape(dateline)}</div>
  <div style="color:{_PALETTE['text']}; font-size:14px; margin-top:6px;">{unit_count} unit(s) reported in</div>
</td></tr>
{''.join(cards_html)}
{_footer_links(manage_link, unsubscribe_url)}
"""

    text_lines = [
        f"{span} DORIS digest",
        dateline,
        f"{unit_count} unit(s) reported in",
        "",
        "\n\n".join(text_blocks),
        "",
        f"Manage subscriptions: {manage_link}",
    ]
    if unsubscribe_url:
        text_lines.append(f"Unsubscribe:          {unsubscribe_url}")

    preheader = f"{unit_count} unit(s) reported in · {dateline}"

    return subject, _wrapper(inner, preheader=preheader), "\n".join(text_lines)


# ── Unsubscribed confirmation ──


def build_unsubscribed_email(
    *, subscriber: models.Subscriber, base_url: str
) -> tuple[str, str, str]:
    subject = "You've been unsubscribed from DORIS Tracker"
    inner = f"""
<tr><td style="padding:24px;">
  <h2 style="margin:0 0 10px; font-size:20px; color:{_PALETTE['text']};">You're unsubscribed</h2>
  <p style="margin:0 0 12px; color:{_PALETTE['text']}; font-size:15px; line-height:1.55;">
    <strong>{html_lib.escape(subscriber.email)}</strong> will no longer receive DORIS notifications.
  </p>
  <p style="margin:0; color:{_PALETTE['muted']}; font-size:14px; line-height:1.55;">
    Changed your mind? You can sign up again any time at
    <a href="{html_lib.escape(base_url)}/ui" style="color:{_PALETTE['link']}; font-weight:600;">the DORIS Tracker</a>.
  </p>
</td></tr>
"""
    text = (
        "You're unsubscribed from DORIS Tracker.\n\n"
        f"{subscriber.email} will no longer receive notifications.\n"
        f"Re-subscribe any time at {base_url}/ui"
    )
    return subject, _wrapper(inner, preheader="You're unsubscribed from DORIS Tracker"), text
