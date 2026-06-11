# stramatel-scoreboard-reader

> **Experimental — use at your own risk.**
> This project has had limited real-hardware testing. The Stramatel frame protocol was reverse-engineered from observed serial output; several fields (including the running/stopped bit) are not yet understood. Expect rough edges.

A Python bridge that reads the RS-232 serial output of a Stramatel scoreboard, decodes the binary frame protocol, and PATCHes the live clock state to a [Scorelayer](https://www.scorelayer.live) HTTP API.

## Requirements

- Python 3.10+
- `pyserial` and `requests` (`pip install -r requirements.txt`)
- A Stramatel scoreboard with an RS-232 output and a USB serial adapter (tested at 19200 baud, 8N1)

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

## Known limitations

- **`running` is hardcoded `true`** — the Stramatel byte that signals clock running/stopped has not been identified. The API always receives `"running": true`, so downstream displays will show the clock as running even when it is stopped.
- **Frame protocol is reverse-engineered** — only the clock digits (bytes 4–7) and display code (byte 1) are reliably decoded. The meaning of most other bytes is unknown.
- **SS_TENTHS mode is inferred** — the sub-minute display format (`56.0 s`) is based on one observed pattern; other Stramatel models or firmware versions may differ.
