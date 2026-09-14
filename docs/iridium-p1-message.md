# DORIS Iridium SBD message format (P/1)

This is the payload the vehicle should send through RockBLOCK / Iridium Short Burst Data. Canonical P/1 is what to encode; the cloud will still plot a message if it can find a valid lat/lon in a messy buffer.

**Target size:** 50 bytes (RockBLOCK SBD MO). Canonical P/1 is **45 bytes**.

---

## Framing

The payload is **not** a normal CSV file. It is:

1. Eight ASCII fields separated by commas (`,`, `0x2C`)
2. A **trailing comma**
3. **Exactly two raw binary bytes** (status flags — not ASCII, not hex digits)

```
<type>,<version>,<lat>,<lon>,<velocity>,<course>,<depth>,<battery>,<flag_hi><flag_lo>
```

Do **not** UTF-8 encode. Do **not** null-terminate. Do **not** append a newline. Do **not** hex-encode before handing the buffer to the modem — RockBLOCK already hex-encodes the bytes on the webhook.

Because the last two bytes are arbitrary binary, a comma byte (`0x2C`) is legal *inside the flags*. Split only the ASCII prefix; never `split(",")` the whole buffer.

---

## Type and version

| Field | Size | Value for this message |
|---|---|---|
| Message type | 1 ASCII byte | `P` (position / telemetry) |
| Message version | 1 ASCII byte | `1` |

Type is a single character `0–9` or `A–Z`. Version is a single character so the same type can evolve without changing the letter. The cloud currently accepts **only** type `P` version `1`. Other combinations are rejected.

When you later add a new message, pick a new type letter (or bump version to `2` if the fields of `P` change). Keep this same framing: ASCII body, trailing comma, two flag bytes — unless a future spec says otherwise.

---

## Fields

Encode the ASCII body with **no spaces**. Zero-pad numeric fields to the widths below. Always include an explicit `+` or `-` on lat and lon.

| # | Field | Canonical width | Encode as | Notes |
|---|---|---|---|---|
| 1 | Type | 1 | `P` | |
| 2 | Version | 1 | `1` | |
| 3 | GPS latitude | 10 | `+DDD.DDDDD` | Degrees. Always a sign. Three digits before the decimal (pad with zeros). Five after. Range −90…+90. |
| 4 | GPS longitude | 10 | `±DDD.DDDDD` | Same pattern. Range −180…+180. |
| 5 | GPS velocity | 2 | `VV` | Integer **decimeters per second** (0.1 m/s). `12` = 1.2 m/s. Range 00–99. |
| 6 | GPS course | 3 | `CCC` | Integer degrees, 000–359, true north. |
| 7 | Depth achieved | 4 | `DDDD` | Integer meters this dive (max depth). Range 0000–9999. |
| 8 | Battery voltage | 4 | `BB.B` | Volts, one decimal place. `14.7` = 14.7 V. |
| 9 | Status flags | 2 bytes | raw | Reserved. Send `0x00 0x00`. See below. |

### Status flags

Two bytes, **big-endian** uint16: first byte is the high 8 bits.

```
flags = (flag_hi << 8) | flag_lo
```

No bits are assigned yet. Send `00 00` until a later spec defines them. Do not send ASCII `"00"` unless the byte values really are `0x30 0x30`.

---

## Canonical example

ASCII prefix (43 bytes) plus flags (2 bytes) = **45 bytes**:

```
P,1,+021.43255,-157.78933,12,045,0028,14.7,<0x00><0x00>
```

| Field | Encoded | Meaning |
|---|---|---|
| Type / version | `P` `1` | Position message v1 |
| Latitude | `+021.43255` | 21.43255° N |
| Longitude | `-157.78933` | 157.78933° W |
| Velocity | `12` | 1.2 m/s |
| Course | `045` | 45° |
| Depth | `0028` | 28 m |
| Battery | `14.7` | 14.7 V |
| Flags | `00 00` | none set |

Hex of the full 45-byte buffer (what RockBLOCK puts in webhook `data`):

```
502c312c2b3032312e34333235352c2d3135372e37383933332c31322c3034352c303032382c31342e372c0000
```

---

## Encoder sketch

Widths assume C `printf` / equivalent. `lat` and `lon` are decimal degrees; `velocity_dm_s` is already in dm/s.

```c
uint8_t buf[50];
int n = snprintf((char *)buf, sizeof(buf),
    "P,1,%+010.5f,%+010.5f,%02d,%03d,%04d,%04.1f,",
    lat, lon, velocity_dm_s, course_deg, depth_m, battery_v);
/* snprintf wrote the trailing comma; append two flag bytes. */
buf[n++] = 0x00;
buf[n++] = 0x00;
/* send buf[0 .. n-1], n == 45 for in-range values */
```

`%+010.5f` produces a sign, zero-padded width 10, five decimal places (`+021.43255`, `-157.78933`). Confirm your libc actually zero-pads that way before shipping.

---

## Cloud acceptance

Encode the canonical layout above. The tracker is deliberately looser than this spec so a **valid lat/lon still plots** when the rest of the buffer is messy:

- Zero-padding and extra/missing fractional digits on lat/lon are accepted (`-21.43`).
- Velocity, course, depth, battery, and flags are best-effort. Garbage, empty fields, or omitted fields become unset; the position is still stored.
- Missing flag bytes, a trailing NUL, or a newline do not drop the message.
- Type/version other than `P`/`1` is still accepted if fields 3–4 are a valid lat/lon.
- A named `LAT:…,LON:…` body is salvaged if the P/1 slots are not usable.

The message is rejected only when hex-decoding fails or **no valid latitude/longitude** can be found. Do not use that as an excuse to send a short or ad-hoc payload — keep P/1 at 45 bytes.

---

## Size budget

| Piece | Bytes |
|---|---|
| `P,1,` | 4 |
| lat + comma | 11 |
| lon + comma | 11 |
| velocity + comma | 3 |
| course + comma | 4 |
| depth + comma | 5 |
| battery + comma | 5 |
| flags | 2 |
| **Total** | **45** |
| Iridium SBD MO budget | 50 |
| Spare | 5 |

Keep P/1 at 45. If you need more fields, add a new type or version rather than stretching this one past 50.
