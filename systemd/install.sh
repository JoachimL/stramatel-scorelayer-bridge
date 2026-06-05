#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/stramatel-scorelayer-bridge"
SERVICE_NAME="stramatel-scorelayer"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
ENV_FILE="/etc/stramatel-scorelayer.env"

echo "Installing Stramatel Scorelayer bridge..."

sudo apt update
sudo apt install -y python3 python3-venv python3-pip git

sudo mkdir -p "$APP_DIR"
sudo chown -R "$USER:$USER" "$APP_DIR"

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

sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Stramatel to Scorelayer bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/stramatel_scorelayer_bridge.py --port \${STRAMATEL_PORT} --baudrate \${STRAMATEL_BAUDRATE}
Restart=always
RestartSec=3
User=$USER
Group=$USER
SupplementaryGroups=dialout
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

sudo usermod -aG dialout "$USER"

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