# stramatel-scoreboard-reader

> **Experimental — use at your own risk.**
> This project has had limited real-hardware testing. The Stramatel frame protocol was reverse-engineered from observed serial output; several fields (including the running/stopped bit) are not yet understood. Expect rough edges.

A Python bridge that reads the RS-485 serial output of a Stramatel scoreboard, decodes its binary frame protocol, and PATCHes the live clock state to a [Scorelayer](https://www.scorelayer.live) HTTP API.

## Requirements

- Python 3.10+
- `pyserial` and `requests` (`pip install -r requirements.txt`)
- A Stramatel scoreboard with an RS-485 output and a USB-RS485 adapter (tested at 19200 baud, 8N1)

The bridge only ever reads — it never transmits on the bus — so it works with a simple RS-485-to-USB adapter wired to the scoreboard's A/B pair. `serial_loop()` explicitly forces `DTR`/`RTS` low on the port (`stramatel_scorelayer_bridge.py:493-494`) so the adapter doesn't attempt to drive the line, since some USB-RS485 adapters use RTS to toggle their driver's transmit-enable pin.

## Quick start (no hardware needed)

```bash
pip install -r requirements.txt
python stramatel_scorelayer_bridge.py --simulate --dry-run --verbose
```

This runs a simulated clock counting up from 19:40 and prints the PATCH payload to stdout instead of sending it.

## Real hardware

```bash
python stramatel_scorelayer_bridge.py \
  --port /dev/ttyUSB0 \
  --api-url  https://www.scorelayer.live/api/scoreboards/<id>/push \
  --api-token <token>
```

Or use environment variables instead of flags:

```bash
export SCORELAYER_API_URL=https://www.scorelayer.live/api/scoreboards/<id>/push
export SCORELAYER_API_TOKEN=<token>
python stramatel_scorelayer_bridge.py --port /dev/ttyUSB0
```

Copy `.env.example` as a starting point:

```bash
cp .env.example .env
# edit .env, then:
set -a && source .env && set +a
python stramatel_scorelayer_bridge.py --port "$STRAMATEL_PORT"
```

Use `/dev/serial/by-id/...` for the port where possible — it survives USB re-plug.

## Frame protocol

The scoreboard continuously broadcasts fixed-length binary frames on the RS-485 bus at 19200 baud, 8N1. Each frame is exactly **54 bytes**:

| Offset | Content |
|--------|---------|
| 0 | `START` = `0xF8` |
| 1 | Display code byte (e.g. `0x35` = `'5'`; meaning of the value beyond a raw display-mode/CP437 character is unconfirmed) |
| 2–3 | Unidentified (observed as spaces) |
| 4–7 | Clock digits, ASCII/CP437 (see **Clock decoding** below) |
| 8 | Unidentified (observed as a separator space) |
| 9…52 | Home/away score fields and other unidentified display data (see **Score decoding** below) |
| 53 | `END` = `0x0D` |

`FrameReader` (`stramatel_scorelayer_bridge.py:91`) buffers incoming bytes and yields one 54-byte candidate at a time once it sees `START` at the front and `END` at offset 53. On a bad `END` byte it doesn't just drop one byte — it seeks forward to the *next* `0xF8` in the buffer, because `0xF8` also shows up as ordinary interior frame data and single-byte dropping would thrash trying to resync.

The scoreboard uses **Code Page 437** for its display characters (`decode_cp437()`), which matters because byte `0xF8` — the frame's own `START` marker — decodes to `°` (degree sign) in CP437 when it appears as display content elsewhere in a frame.

### Clock decoding

`parse_clock()` (`stramatel_scorelayer_bridge.py:149`) reads bytes 4–7 and classifies them into one of three modes:

| Mode | Pattern | Example | Decoded |
|------|---------|---------|---------|
| `MMSS` | 4 ASCII digits | `"1941"` | `19:41` |
| `MMSS` (sub-10-minute) | space + digit + 2 digits | `" 839"` | `08:39` |
| `SS_TENTHS` | 2 digits + tenths digit + trailing space | `"560 "` | `00:56.0` |
| `MMSS_INVALID_SECONDS` / `UNKNOWN` | anything else (e.g. seconds > 59, all spaces) | — | unparseable; `current_time_ms` is `None` |

The scoreboard switches from `MMSS` to `SS_TENTHS` once the clock drops under one minute, trading the tens-of-minutes digit for a tenths-of-a-second digit. When the mode is unparseable, the bridge deliberately skips the API push rather than sending a bad `currentTimeMs` (see **Key design decisions** below).

### Score decoding

Scores are not yet parsed into dedicated `ScoreboardState` fields — they are only available via the `tokens` list, which is produced by CP437-decoding the whole frame and splitting on whitespace. For a typical frame:

- `tokens[0]` — the `START` byte and display-code byte decoded together (no separating space), e.g. `"°5"`
- `tokens[1]` — the clock, e.g. `"141"` (raw, undecoded)
- `tokens[2]` — **home score**, plain decimal ASCII, e.g. `"3"`, `"13"`
- `tokens[3]` — **away score**, a two-character hex byte using what looks like a **thermometer/unary encoding**: the number of set bits in the byte equals the score, not its numeric value

Observed away-score values from real logs:

| Token | Byte | Binary | Set bits | Score |
|-------|------|--------|----------|-------|
| `0E` | `0x0E` | `00001110` | 3 | 3 |
| `1E` | `0x1E` | `00011110` | 4 | 4 |
| `3E` | `0x3E` | `00111110` | 5 | 5 |

The pattern so far is a run of `N` consecutive `1` bits (i.e. `2^(N+1) - 2`), consistent with a bar/segment-style away-score indicator rather than a 7-segment digit. This is a hypothesis based on a handful of real-world observations (see `test_bridge.py`'s score tests) and is **unconfirmed at higher scores** — it needs more field data before being trusted or wired into `ScoreboardState`/the API payload. Why home and away scores would use two different encodings on the same board is itself unexplained.

## Key design decisions

- **Push on change + heartbeat**: a state is only sent to the API when it differs from the previous one, or when `--heartbeat-seconds` (default 15s) has elapsed since the last push.
- **`current_time_ms` gating**: if the clock mode is unparseable, the PATCH is skipped entirely rather than sending a bad value.
- **`running` is hardcoded `true`**: the Stramatel running/stopped byte has not yet been identified.
- **Heartbeat timer only advances on successful push**: if `push()` returns `False` (e.g. clock unreadable, API down), `last_push_at` is not updated, so the bridge keeps retrying every heartbeat interval instead of going silent.
- **Auto-resync on misalignment**: see `FrameReader` above.

## Deployment on Raspberry Pi / Debian

```bash
# 1. Clone or copy the repo into /opt/stramatel-scorelayer-bridge
# 2. Run the install script (creates venv, systemd service, env file):
bash systemd/install.sh

# 3. Set the real API token:
sudo nano /etc/stramatel-scorelayer.env

# 4. Reboot so the dialout group applies, then start:
sudo systemctl start stramatel-scorelayer
journalctl -u stramatel-scorelayer -f
```

## Tests

```bash
pip install -r requirements.txt
pytest test_bridge.py
```

`test_bridge.py` doubles as living documentation of the protocol: it encodes the real clock and score examples pulled from field logs, including the thermometer-encoding hypothesis above.

## Known limitations

- **`running` is hardcoded `true`** — the Stramatel byte that signals clock running/stopped has not been identified. The API always receives `"running": true`, so downstream displays will show the clock as running even when it is stopped.
- **Frame protocol is reverse-engineered** — only the clock digits (bytes 4–7) and the display-code byte (byte 1) are reliably decoded. Bytes 2–3, 8, and most of 9–52 (beyond token-level score extraction) are unidentified.
- **SS_TENTHS mode is inferred** — the sub-minute display format (`56.0 s`) is based on one observed pattern; other Stramatel models or firmware versions may differ.
- **Scores are not structurally parsed** — home/away scores are only reachable via the raw `tokens` list, not dedicated `ScoreboardState` fields, and are not included in the API payload. The away-score thermometer/unary encoding is a hypothesis from limited real-world data, unconfirmed at higher scores.
