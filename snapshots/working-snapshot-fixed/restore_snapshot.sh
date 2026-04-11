#!/usr/bin/env bash
set -euo pipefail

SNAP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[1/7] Stopping bridge..."
systemctl stop bt-call-bridge.service 2>/dev/null || true

echo "[2/7] Restoring app files..."
if [[ -d "$SNAP_DIR/opt/bt-call-bridge" ]]; then
  rm -rf /opt/bt-call-bridge
  cp -a "$SNAP_DIR/opt/bt-call-bridge" /opt/
fi

echo "[3/7] Restoring systemd unit..."
if [[ -f "$SNAP_DIR/etc/systemd/system/bt-call-bridge.service" ]]; then
  cp -a "$SNAP_DIR/etc/systemd/system/bt-call-bridge.service" /etc/systemd/system/
fi

echo "[4/7] Restoring Bluetooth state..."
if [[ -d "$SNAP_DIR/var/lib/bluetooth" ]]; then
  rm -rf /var/lib/bluetooth
  cp -a "$SNAP_DIR/var/lib/bluetooth" /var/lib/
fi

if [[ -d "$SNAP_DIR/var/lib/bluealsa" ]]; then
  rm -rf /var/lib/bluealsa
  cp -a "$SNAP_DIR/var/lib/bluealsa" /var/lib/
fi

echo "[5/7] Reloading systemd..."
systemctl daemon-reload

echo "[6/7] Re-enabling bridge..."
systemctl enable bt-call-bridge.service

echo "[7/7] Restore complete."
echo "Recommended next steps:"
echo "  systemctl restart bt-call-bridge.service"
echo "  systemctl status bt-call-bridge.service --no-pager"
echo "If Bluetooth call audio conflicts return, stop user audio stack:"
echo "  systemctl --user stop pipewire pipewire-pulse wireplumber"
