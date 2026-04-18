#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_PATH="${1:-$ROOT_DIR/bridge.conf}"

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "Config file not found: $CONFIG_PATH" >&2
  exit 1
fi

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo $0 $CONFIG_PATH" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$CONFIG_PATH"

BT_ALIAS="${BT_ALIAS:-PiCallBridge}"
BT_HCI="${BT_HCI:-hci0}"
PHONE_MAC="${PHONE_MAC:-}"
DISCOVERABLE_TIMEOUT="${DISCOVERABLE_TIMEOUT:-0}"
BLUEALSA_INITIAL_VOLUME="${BLUEALSA_INITIAL_VOLUME:-70}"
BLUEALSA_KEEP_ALIVE="${BLUEALSA_KEEP_ALIVE:--1}"
BLUEALSA_IO_RT_PRIORITY="${BLUEALSA_IO_RT_PRIORITY:-20}"
ENABLE_A2DP_SINK="${ENABLE_A2DP_SINK:-0}"
AGENT_CAPABILITY="${BT_AGENT_CAPABILITY:-NoInputNoOutput}"
IO_CAPABILITY="${BT_IO_CAPABILITY:-0x03}"

BLUEALSA_CMD="$(command -v bluealsad || command -v bluealsa || true)"
BTMGMT_CMD="$(command -v btmgmt || true)"
SYSTEMCTL_CMD="$(command -v systemctl || true)"
PKILL_CMD="$(command -v pkill || true)"
RUN_AS_USER="${SUDO_USER:-${USER:-tyrion}}"
RUN_AS_UID="$(id -u "$RUN_AS_USER" 2>/dev/null || echo 1000)"
USER_RUNTIME_DIR="/run/user/${RUN_AS_UID}"
USER_DBUS_ADDR="unix:path=${USER_RUNTIME_DIR}/bus"

TEMP_BLUEALSA_PID=""
BTCTL_READER_PID=""
BTCTL_PID=""
BTCTL_FIFO=""
BTCTL_LOG=""
BTCTL_FD_OPEN=0

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

run_user_systemctl() {
  if [[ -z "$SYSTEMCTL_CMD" ]]; then
    return 0
  fi
  sudo -u "$RUN_AS_USER" \
    XDG_RUNTIME_DIR="$USER_RUNTIME_DIR" \
    DBUS_SESSION_BUS_ADDRESS="$USER_DBUS_ADDR" \
    "$SYSTEMCTL_CMD" --user "$@" 2>/dev/null || true
}

run_btmgmt() {
  if [[ -z "$BTMGMT_CMD" ]]; then
    return 0
  fi
  "$BTMGMT_CMD" -i "$BT_HCI" "$@" 2>/dev/null || "$BTMGMT_CMD" "$@" 2>/dev/null || true
}

cleanup() {
  set +e
  if [[ -n "$BTCTL_READER_PID" ]]; then
    kill "$BTCTL_READER_PID" 2>/dev/null || true
  fi
  if [[ -n "$BTCTL_PID" ]]; then
    kill "$BTCTL_PID" 2>/dev/null || true
    wait "$BTCTL_PID" 2>/dev/null || true
  fi
  if [[ "$BTCTL_FD_OPEN" -eq 1 ]]; then
    exec 3>&-
  fi
  if [[ -n "$TEMP_BLUEALSA_PID" ]]; then
    kill "$TEMP_BLUEALSA_PID" 2>/dev/null || true
    wait "$TEMP_BLUEALSA_PID" 2>/dev/null || true
  fi
  if [[ -n "$BTCTL_FIFO" ]]; then
    rm -f "$BTCTL_FIFO"
  fi
  if [[ -n "$BTCTL_LOG" ]]; then
    rm -f "$BTCTL_LOG"
  fi
}

trap cleanup EXIT INT TERM

if [[ -z "$BLUEALSA_CMD" ]]; then
  echo "Could not find bluealsad/bluealsa in PATH" >&2
  exit 1
fi

log "Stopping conflicting bridge, Bluetooth audio, and desktop audio services"
if [[ -n "$SYSTEMCTL_CMD" ]]; then
  "$SYSTEMCTL_CMD" stop \
    bt-call-bridge.service \
    bluealsa.service \
    bluealsa-aplay.service \
    bt-speaker-agent.service \
    ofono.service 2>/dev/null || true
fi

if [[ -n "$PKILL_CMD" ]]; then
  "$PKILL_CMD" -f bt_call_bridge.py 2>/dev/null || true
  "$PKILL_CMD" -f '/usr/bin/bluealsa -S' 2>/dev/null || true
  "$PKILL_CMD" -f '/usr/bin/bluealsa-aplay -S' 2>/dev/null || true
  "$PKILL_CMD" -f '/usr/bin/bluetoothctl' 2>/dev/null || true
fi

run_user_systemctl stop \
  pipewire.service \
  pipewire-pulse.service \
  wireplumber.service \
  pipewire.socket \
  pipewire-pulse.socket \
  pulseaudio.service

log "Forcing controller IO capability to NoInputNoOutput via btmgmt"
run_btmgmt power off
run_btmgmt io-cap "$IO_CAPABILITY"
run_btmgmt bondable on
run_btmgmt connectable on
run_btmgmt ssp on
run_btmgmt power on

BLUEALSA_ARGS=(
  --initial-volume "$BLUEALSA_INITIAL_VOLUME"
  --keep-alive "$BLUEALSA_KEEP_ALIVE"
  --io-rt-priority "$BLUEALSA_IO_RT_PRIORITY"
  --device "$BT_HCI"
  --profile hfp-hf
  --profile hsp-hs
)

if [[ "${ENABLE_A2DP_SINK,,}" =~ ^(1|true|yes|on)$ ]]; then
  BLUEALSA_ARGS+=(--profile a2dp-sink)
fi

log "Starting temporary snapshot-style BlueALSA server"
"$BLUEALSA_CMD" "${BLUEALSA_ARGS[@]}" &
TEMP_BLUEALSA_PID=$!
sleep 1
if ! kill -0 "$TEMP_BLUEALSA_PID" 2>/dev/null; then
  echo "Temporary BlueALSA server failed to stay running" >&2
  exit 1
fi

send_btctl() {
  local line="$1"
  printf '%s\n' "$line" >&3
}

log "Starting persistent bluetoothctl agent with explicit capability: $AGENT_CAPABILITY"
BTCTL_FIFO="$(mktemp -u /tmp/btctl.XXXXXX.fifo)"
BTCTL_LOG="$(mktemp /tmp/btctl.XXXXXX.log)"
mkfifo "$BTCTL_FIFO"
bluetoothctl <"$BTCTL_FIFO" >"$BTCTL_LOG" 2>&1 &
BTCTL_PID=$!
exec 3>"$BTCTL_FIFO"
BTCTL_FD_OPEN=1

tail -f "$BTCTL_LOG" &
BTCTL_READER_PID=$!

sleep 1
send_btctl "power on"
send_btctl "agent off"
send_btctl "agent $AGENT_CAPABILITY"
send_btctl "default-agent"
send_btctl "system-alias $BT_ALIAS"
send_btctl "pairable on"
send_btctl "discoverable on"
send_btctl "discoverable-timeout $DISCOVERABLE_TIMEOUT"
send_btctl "show"

echo
echo "Temporary Bluetooth call-profile server is ready."
echo "Pair to $BT_ALIAS from the phone now."
echo "Keep this terminal open while pairing so the agent and HFP/HSP profiles stay alive."
echo "When the phone finishes pairing or fails, press Enter here to inspect the result."
read -r _

echo
echo "=== bluetoothctl devices ==="
bluetoothctl devices || true

if [[ -n "$PHONE_MAC" ]]; then
  echo
  echo "=== bluetoothctl info $PHONE_MAC ==="
  bluetoothctl info "$PHONE_MAC" || true
  echo
  echo "Attempting trust/connect for configured phone..."
  send_btctl "trust $PHONE_MAC"
  send_btctl "connect $PHONE_MAC"
  sleep 2
  echo
  echo "=== bluetoothctl info $PHONE_MAC (post-connect) ==="
  bluetoothctl info "$PHONE_MAC" || true
fi

echo
echo "=== system bus names matching bluealsa ==="
busctl --system list | grep bluealsa || true
echo
echo "=== active processes ==="
ps -ef | grep -E 'bluealsa|bluealsa-aplay|bluetoothctl|bt_call_bridge' | grep -v grep || true
echo
echo "Press Enter to stop the temporary debug server."
read -r _
