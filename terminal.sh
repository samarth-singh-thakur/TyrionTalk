#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_PATH="${1:-$ROOT_DIR/bridge.conf}"
AUDIO_DIR="${2:-${AUDIO_DIR:-$ROOT_DIR/audio}}"

exec python3 "$ROOT_DIR/scripts/bt_call_bridge_terminal.py" \
  --config "$CONFIG_PATH" \
  --audio-dir "$AUDIO_DIR"
