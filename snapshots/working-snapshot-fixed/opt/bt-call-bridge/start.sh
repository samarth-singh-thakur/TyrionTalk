#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_PATH="${1:-$ROOT_DIR/bridge.conf}"

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "Config file not found: $CONFIG_PATH" >&2
  exit 1
fi

exec python3 "$ROOT_DIR/scripts/bt_call_bridge.py" --config "$CONFIG_PATH"
