#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="${TARGET_DIR:-/opt/bt-call-bridge}"
SERVICE_NAME="bt-call-bridge.service"
GLOBAL_BIN_DIR="${GLOBAL_BIN_DIR:-/usr/local/bin}"
TERMINAL_CMD_NAME="${TERMINAL_CMD_NAME:-tyrionTalks}"
AUDIO_LIBRARY_DIR_NAME="${AUDIO_LIBRARY_DIR_NAME:-audio}"

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
  ffmpeg \
  python3

if command -v systemctl >/dev/null 2>&1; then
  systemctl stop \
    bluealsa.service \
    bluealsa-aplay.service \
    bt-speaker-agent.service \
    ofono.service 2>/dev/null || true
  systemctl disable \
    bluealsa.service \
    bluealsa-aplay.service \
    bt-speaker-agent.service \
    ofono.service 2>/dev/null || true
fi

if command -v btmgmt >/dev/null 2>&1; then
  btmgmt power off 2>/dev/null || true
  btmgmt io-cap 0x03 2>/dev/null || true
  btmgmt bondable on 2>/dev/null || true
  btmgmt connectable on 2>/dev/null || true
  btmgmt ssp on 2>/dev/null || true
  btmgmt power on 2>/dev/null || true
fi

mkdir -p "$TARGET_DIR"
find "$TARGET_DIR" -mindepth 1 -maxdepth 1 ! -name 'bridge.conf' -exec rm -rf {} +
cp -a "$ROOT_DIR/." "$TARGET_DIR/"
mkdir -p "$TARGET_DIR/$AUDIO_LIBRARY_DIR_NAME"
find "$TARGET_DIR" -maxdepth 1 -type f -iname '*.mp3' -exec cp -f {} "$TARGET_DIR/$AUDIO_LIBRARY_DIR_NAME/" \;

if [[ ! -f "$TARGET_DIR/bridge.conf" ]]; then
  cp "$TARGET_DIR/bridge.conf.example" "$TARGET_DIR/bridge.conf"
fi

install -m 0644 "$TARGET_DIR/systemd/$SERVICE_NAME" "/etc/systemd/system/$SERVICE_NAME"
install -d "$GLOBAL_BIN_DIR"
cat > "$GLOBAL_BIN_DIR/$TERMINAL_CMD_NAME" <<EOF
#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR=$(printf '%q' "$TARGET_DIR")
CONFIG_PATH="\${1:-\$TARGET_DIR/bridge.conf}"
AUDIO_DIR="\${2:-\$TARGET_DIR/$AUDIO_LIBRARY_DIR_NAME}"

if [[ \$EUID -ne 0 ]]; then
  exec sudo "$GLOBAL_BIN_DIR/$TERMINAL_CMD_NAME" "\$@"
fi

exec "\$TARGET_DIR/terminal.sh" "\$CONFIG_PATH" "\$AUDIO_DIR"
EOF
chmod 0755 "$GLOBAL_BIN_DIR/$TERMINAL_CMD_NAME"
systemctl daemon-reload

echo
echo "Installed to $TARGET_DIR"
echo "Global launcher installed: $GLOBAL_BIN_DIR/$TERMINAL_CMD_NAME"
echo "Audio library: $TARGET_DIR/$AUDIO_LIBRARY_DIR_NAME"
echo "Disabled conflicting system Bluetooth audio services: bluealsa, bluealsa-aplay, bt-speaker-agent, ofono"
echo "Forced Bluetooth pairing mode to NoInputNoOutput via btmgmt io-cap 0x03"
echo "Next:"
echo "  1) Launch from anywhere: $TERMINAL_CMD_NAME"
echo "     Optional custom config/audio dir: $TERMINAL_CMD_NAME /path/to/bridge.conf /path/to/audio-dir"
echo "  2) Or pair manually: $TARGET_DIR/pair_phone.sh $TARGET_DIR/bridge.conf"
echo "  3) Start manually: sudo $TARGET_DIR/start.sh $TARGET_DIR/bridge.conf"
echo "     or via systemd: sudo systemctl enable --now $SERVICE_NAME"
