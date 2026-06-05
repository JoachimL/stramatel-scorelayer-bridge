#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 [--user USER]"
  echo
  echo "  --user USER   System user to run the service (default: \$USER)"
  exit 1
}

SERVICE_USER="${USER}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user) SERVICE_USER="${2:?--user requires a value}"; shift 2 ;;
    -h|--help) usage ;;
    *) echo "Unknown argument: $1"; usage ;;
  esac
done

APP_DIR="/opt/stramatel-scorelayer-bridge"
SERVICE_NAME="stramatel-scorelayer"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_FILE="/etc/stramatel-scorelayer.env"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Installing Stramatel Scorelayer bridge as user '${SERVICE_USER}'..."

sudo apt update
sudo apt install -y python3 python3-venv python3-pip git

sudo mkdir -p "$APP_DIR"
sudo chown -R "${SERVICE_USER}:${SERVICE_USER}" "$APP_DIR"

cd "$APP_DIR"

if [ ! -d ".git" ]; then
  echo "Copy or clone the repo into $APP_DIR before running this script."
  exit 1
fi

python3 -m venv venv
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

if [ ! -f "$ENV_FILE" ]; then
  sudo tee "$ENV_FILE" > /dev/null <<EOF
SCORELAYER_API_URL=https://www.scorelayer.live/api/scoreboards/dad66360-2f76-4136-adb4-370740696365/push
SCORELAYER_API_TOKEN=replace_me
STRAMATEL_PORT=/dev/ttyUSB0
STRAMATEL_BAUDRATE=19200
EOF

  echo "Created $ENV_FILE"
  echo "Edit it and set SCORELAYER_API_TOKEN before starting the service."
fi

sed \
  -e "s|__USER__|${SERVICE_USER}|g" \
  -e "s|/opt/stramatel-scorelayer-bridge|${APP_DIR}|g" \
  -e "s|/etc/stramatel-scorelayer.env|${ENV_FILE}|g" \
  "${SCRIPT_DIR}/stramatel-scorelayer.service" \
  | sudo tee "$SERVICE_FILE" > /dev/null

sudo usermod -aG dialout "$SERVICE_USER"

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

echo
echo "Installed."
echo
echo "Next steps:"
echo "1. Edit $ENV_FILE and set the real token:"
echo "   sudo nano $ENV_FILE"
echo
echo "2. Reboot or log out/in so dialout group applies:"
echo "   sudo reboot"
echo
echo "3. Start service:"
echo "   sudo systemctl start $SERVICE_NAME"
echo
echo "4. Watch logs:"
echo "   journalctl -u $SERVICE_NAME -f"
