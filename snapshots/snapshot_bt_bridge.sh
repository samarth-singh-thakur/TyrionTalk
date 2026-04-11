#!/usr/bin/env bash
set -euo pipefail

TS="$(date +%F-%H%M%S)"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PHONE_MAC_DEFAULT="58:43:AB:D9:8C:6E"
USER_NAME="${SUDO_USER:-$(logname 2>/dev/null || true)}"
USER_UID="${USER_NAME:+$(id -u "$USER_NAME" 2>/dev/null || true)}"
USER_RUNTIME_DIR="${USER_UID:+/run/user/$USER_UID}"
USER_DBUS_ADDR="${USER_RUNTIME_DIR:+unix:path=$USER_RUNTIME_DIR/bus}"

prompt_snapshot_name() {
  local input
  local default_name="rpi-bt-call-bridge-snapshot-$TS"

  if [[ -t 0 ]]; then
    read -r -p "Snapshot name [$default_name]: " input
  fi

  input="${input:-$default_name}"
  input="$(printf '%s' "$input" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9._-]+/-/g; s/^-+//; s/-+$//; s/-{2,}/-/g')"

  if [[ -z "$input" ]]; then
    input="$default_name"
  fi

  printf '%s\n' "$input"
}

SNAPSHOT_NAME="$(prompt_snapshot_name)"
SNAP_DIR="$SCRIPT_DIR/$SNAPSHOT_NAME"
TARBALL_PATH="$SCRIPT_DIR/$SNAPSHOT_NAME.tar.gz"

if [[ -e "$SNAP_DIR" || -e "$TARBALL_PATH" ]]; then
  echo "Snapshot already exists: $SNAPSHOT_NAME" >&2
  exit 1
fi

mkdir -p "$SNAP_DIR"

echo "[1/8] Copying bridge files..."
mkdir -p "$SNAP_DIR/opt/bt-call-bridge" "$SNAP_DIR/etc/systemd/system" "$SNAP_DIR/var/lib"
cp -a /opt/bt-call-bridge/. "$SNAP_DIR/opt/bt-call-bridge/" 2>/dev/null || true
rm -rf "$SNAP_DIR/opt/bt-call-bridge/.git"
cp -a /etc/systemd/system/bt-call-bridge.service "$SNAP_DIR/etc/systemd/system/" 2>/dev/null || true

echo "[2/8] Copying Bluetooth state..."
cp -a /var/lib/bluetooth "$SNAP_DIR/var/lib/" 2>/dev/null || true
cp -a /var/lib/bluealsa "$SNAP_DIR/var/lib/" 2>/dev/null || true

echo "[3/8] Saving package inventory..."
dpkg -l | grep -E 'bluez|bluealsa|alsa|pipewire|wireplumber|pulseaudio|ofono' > "$SNAP_DIR/packages.txt" || true

echo "[4/8] Saving service state..."
systemctl list-unit-files | grep -E 'bluealsa|pipewire|wireplumber|pulseaudio|ofono|bt-call-bridge|bluetooth' > "$SNAP_DIR/system-services.txt" || true
systemctl --type=service --all | grep -E 'bluealsa|pipewire|wireplumber|pulseaudio|ofono|bt-call-bridge|bluetooth' > "$SNAP_DIR/system-services-runtime.txt" || true

if [[ -n "${USER_NAME:-}" && -n "${USER_RUNTIME_DIR:-}" && -S "${USER_RUNTIME_DIR}/bus" ]]; then
  sudo -u "$USER_NAME" \
    XDG_RUNTIME_DIR="$USER_RUNTIME_DIR" \
    DBUS_SESSION_BUS_ADDRESS="$USER_DBUS_ADDR" \
    systemctl --user list-unit-files | grep -E 'pipewire|wireplumber|pulseaudio' > "$SNAP_DIR/user-services.txt" || true
  sudo -u "$USER_NAME" \
    XDG_RUNTIME_DIR="$USER_RUNTIME_DIR" \
    DBUS_SESSION_BUS_ADDRESS="$USER_DBUS_ADDR" \
    systemctl --user --type=service --all | grep -E 'pipewire|wireplumber|pulseaudio' > "$SNAP_DIR/user-services-runtime.txt" || true
fi

echo "[5/8] Saving Bluetooth diagnostics..."
bluetoothctl devices > "$SNAP_DIR/bluetooth-devices.txt" || true
bluetoothctl info "$PHONE_MAC_DEFAULT" > "$SNAP_DIR/bluetooth-phone-info.txt" || true

echo "[6/8] Saving ALSA diagnostics..."
aplay -l > "$SNAP_DIR/aplay-l.txt" 2>&1 || true
arecord -l > "$SNAP_DIR/arecord-l.txt" 2>&1 || true

echo "[7/8] Saving notes..."
cat > "$SNAP_DIR/README-RESTORE.txt" <<'EOF'
This snapshot contains:
- /opt/bt-call-bridge
- /etc/systemd/system/bt-call-bridge.service
- /var/lib/bluetooth
- /var/lib/bluealsa
- package and service state captures

Typical restore flow:
1. Reinstall required packages if needed.
2. Restore /opt/bt-call-bridge
3. Restore bt-call-bridge.service
4. Restore /var/lib/bluetooth and /var/lib/bluealsa
5. Run systemctl daemon-reload
6. Enable/restart bt-call-bridge.service
7. Stop conflicting user audio services if needed:
   systemctl --user stop pipewire pipewire-pulse wireplumber

If the phone still refuses call audio after restore, delete and re-pair once.
EOF

cat > "$SNAP_DIR/restore_snapshot.sh" <<'EOF'
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
EOF
chmod +x "$SNAP_DIR/restore_snapshot.sh"

echo "[8/8] Packing tarball..."
tar -czf "$TARBALL_PATH" -C "$SCRIPT_DIR" "$(basename "$SNAP_DIR")"

if [[ -n "${USER_NAME:-}" ]]; then
  chown -R "$USER_NAME":"$USER_NAME" "$SNAP_DIR" "$TARBALL_PATH" 2>/dev/null || true
fi

echo
echo "Snapshot created:"
echo "  $SNAP_DIR"
echo "Tarball created:"
echo "  $TARBALL_PATH"
echo
echo "To restore later:"
echo "  cd '$SNAP_DIR'"
echo "  sudo ./restore_snapshot.sh"
