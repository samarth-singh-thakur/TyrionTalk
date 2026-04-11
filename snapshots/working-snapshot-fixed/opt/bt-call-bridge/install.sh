#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="${TARGET_DIR:-/opt/bt-call-bridge}"
SERVICE_NAME="bt-call-bridge.service"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo ./install.sh" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y \
  bluez \
  bluez-alsa-utils \
  libasound2-plugin-bluez \
  alsa-utils \
  python3

mkdir -p "$TARGET_DIR"
find "$TARGET_DIR" -mindepth 1 -maxdepth 1 ! -name 'bridge.conf' -exec rm -rf {} +
cp -a "$ROOT_DIR/." "$TARGET_DIR/"

if [[ ! -f "$TARGET_DIR/bridge.conf" ]]; then
  cp "$TARGET_DIR/bridge.conf.example" "$TARGET_DIR/bridge.conf"
fi

install -m 0644 "$TARGET_DIR/systemd/$SERVICE_NAME" "/etc/systemd/system/$SERVICE_NAME"
systemctl daemon-reload

echo
echo "Installed to $TARGET_DIR"
echo "Next:"
echo "  1) Edit $TARGET_DIR/bridge.conf"
echo "  2) Run $TARGET_DIR/pair_phone.sh $TARGET_DIR/bridge.conf"
echo "  3) Start manually: sudo $TARGET_DIR/start.sh $TARGET_DIR/bridge.conf"
echo "     or via systemd: sudo systemctl enable --now $SERVICE_NAME"
