#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="${1:-/opt/bt-call-bridge/bridge.conf}"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

# Best-effort config load
if [[ -f "$CONFIG_PATH" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_PATH" || true
fi

# Infer the normal desktop user so we can restore user services properly
RUN_AS_USER="${SUDO_USER:-${USER:-tyrion}}"
RUN_AS_UID="$(id -u "$RUN_AS_USER" 2>/dev/null || echo 1000)"
USER_RUNTIME_DIR="/run/user/${RUN_AS_UID}"
USER_DBUS_ADDR="unix:path=${USER_RUNTIME_DIR}/bus"

run_user_systemctl() {
  sudo -u "$RUN_AS_USER" \
    XDG_RUNTIME_DIR="$USER_RUNTIME_DIR" \
    DBUS_SESSION_BUS_ADDRESS="$USER_DBUS_ADDR" \
    systemctl --user "$@" || true
}

stop_bridge_processes() {
  log "Stopping bt-call-bridge processes"

  # Stop service if it exists and is running
  sudo systemctl stop bt-call-bridge.service 2>/dev/null || true

  # Stop bridge-owned processes only
  sudo pkill -f '/usr/bin/bluealsad --initial-volume .* --device hci0' || true
  sudo pkill -f '/usr/bin/bluealsa --initial-volume .* --device hci0' || true
  sudo pkill -f '/usr/bin/bluealsa-aplay --profile-sco --pcm=' || true
  sudo pkill -f 'arecord -D plughw:CARD=C920,DEV=0' || true
  sudo pkill -f 'ffmpeg -hide_banner -loglevel error -nostdin -stream_loop -1 -re -i ' || true
  sudo pkill -f 'aplay -D bluealsa:DEV=' || true
  sudo pkill -f 'bt_call_bridge.py' || true

  sleep 1

  # Clean up any still-running matching processes
  PIDS="$(ps -eo pid=,args= | grep -E 'bluealsad --initial-volume|bluealsa --initial-volume|bluealsa-aplay --profile-sco|arecord -D plughw:CARD=C920,DEV=0|ffmpeg -hide_banner -loglevel error -nostdin -stream_loop -1 -re -i |aplay -D bluealsa:DEV=|bt_call_bridge.py' | grep -v grep | awk '{print $1}' || true)"
  if [[ -n "${PIDS// }" ]]; then
    log "Force-killing remaining bridge processes: $PIDS"
    sudo kill -9 $PIDS || true
  fi
}

restore_system_services() {
  log "Restoring standard system Bluetooth/audio services"

  # Restart services that were stopped to make the bridge work.
  # We START them, but do not enable/disable anything permanently.
  sudo systemctl start bluealsa.service 2>/dev/null || true
  sudo systemctl start bluealsa-aplay.service 2>/dev/null || true
  sudo systemctl start bt-speaker-agent.service 2>/dev/null || true
}

restore_user_services() {
  log "Restoring user audio session services for ${RUN_AS_USER}"

  # Start sockets first so normal desktop audio comes back cleanly
  run_user_systemctl start pipewire.socket
  run_user_systemctl start pipewire-pulse.socket
  run_user_systemctl start wireplumber.service
  run_user_systemctl start pipewire.service
  run_user_systemctl start pipewire-pulse.service
}

main() {
  stop_bridge_processes
  restore_system_services
  restore_user_services

  log "Done"
  log "Sanity check:"
  log "  systemctl --user status pipewire wireplumber pipewire-pulse"
  log "  systemctl status bluealsa bluealsa-aplay bt-speaker-agent"
}

main "$@"
