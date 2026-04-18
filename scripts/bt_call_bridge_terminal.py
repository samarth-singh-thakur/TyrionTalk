#!/usr/bin/env python3
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

from bt_call_bridge import (
    DEFAULTS,
    BridgeError,
    default_audio_library_dir,
    discover_audio_files,
    format_config_path_value,
    list_alsa_devices,
    list_bluetooth_devices,
    parse_kv_config,
    resolve_config_path_value,
    shell_join,
    write_kv_config,
)


MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}$")


class BridgeTerminalApp:
    def __init__(self, config_path: Path, audio_dir: Optional[Path] = None):
        self.root_dir = Path(__file__).resolve().parents[1]
        self.script_dir = Path(__file__).resolve().parent
        self.bridge_script = self.script_dir / "bt_call_bridge.py"
        self.config_path = config_path.expanduser().resolve(strict=False)
        self.config_dir = self.config_path.parent
        self.audio_dir = (
            audio_dir.expanduser().resolve(strict=False)
            if audio_dir is not None
            else default_audio_library_dir()
        )
        self.cfg: Dict[str, str] = dict(DEFAULTS)
        self.dirty = False
        self.load_config()

    def load_config(self) -> None:
        if self.config_path.exists():
            self.cfg = parse_kv_config(str(self.config_path))
        else:
            self.cfg = dict(DEFAULTS)
        self.dirty = False

    def save_config(self) -> None:
        write_kv_config(self.config_path, self.cfg)
        self.dirty = False
        print(f"\nSaved configuration to {self.config_path}")

    def set_value(self, key: str, value: str) -> None:
        normalized = value.strip()
        if self.cfg.get(key, "") != normalized:
            self.cfg[key] = normalized
            self.dirty = True

    def current_uplink_summary(self) -> str:
        source = self.cfg.get("UPLINK_SOURCE", "mic").strip().lower() or "mic"
        if source == "file":
            file_value = self.cfg.get("UPLINK_AUDIO_FILE", "").strip() or "<not set>"
            return f"file -> {file_value}"
        if source == "soundboard":
            file_value = (
                self.read_soundboard_selection()
                or self.cfg.get("UPLINK_AUDIO_FILE", "").strip()
                or "<not set>"
            )
            return f"soundboard -> {file_value}"
        return f"mic -> {self.cfg.get('MIC_PCM', '<not set>')}"

    def soundboard_selector_path(self) -> Path:
        raw_path = self.cfg.get("SOUNDBOARD_SELECTOR_PATH", "").strip() or DEFAULTS[
            "SOUNDBOARD_SELECTOR_PATH"
        ]
        return resolve_config_path_value(raw_path, self.config_dir)

    def read_soundboard_selection(self) -> str:
        selector_path = self.soundboard_selector_path()
        try:
            return selector_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return ""
        except OSError:
            return ""

    def write_soundboard_selection(self, value: str) -> None:
        selector_path = self.soundboard_selector_path()
        selector_path.parent.mkdir(parents=True, exist_ok=True)
        selector_path.write_text(f"{value.strip()}\n", encoding="utf-8")

    def current_tap_summary(self) -> str:
        mode = self.cfg.get("UPLINK_TAP_MODE", "off").strip().lower() or "off"
        if mode == "file":
            return f"file -> {self.cfg.get('UPLINK_TAP_PATH', '<not set>') or '<not set>'}"
        if mode == "command":
            return f"command -> {self.cfg.get('UPLINK_TAP_COMMAND', '<not set>') or '<not set>'}"
        return "off"

    def show_menu(self) -> None:
        print("\n" + "=" * 72)
        print("BT Call Bridge Terminal")
        print("=" * 72)
        print(f"Config file : {self.config_path}")
        print(f"Audio search: {self.audio_dir}")
        print(f"Dirty       : {'yes' if self.dirty else 'no'}")
        if hasattr(sys, "stdin") and not sys.stdin.isatty():
            print("Warning     : stdin is not a TTY, so interactive prompts may not behave normally.")
        if hasattr(sys, "stdout") and not sys.stdout.isatty():
            print("Warning     : stdout is not a TTY, so full terminal UI features are limited.")
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            print("Note        : Bluetooth and audio actions usually need sudo/root on the Pi.")
        print()
        print(f"Phone       : {self.cfg.get('PHONE_MAC', '') or '<not set>'}")
        print(f"Output      : {self.cfg.get('AUX_PCM', '') or '<not set>'}")
        print(f"Uplink      : {self.current_uplink_summary()}")
        print(f"Tap         : {self.current_tap_summary()}")
        print(f"SCO rate    : {self.cfg.get('SCO_RATE', '') or '<not set>'}")
        print()
        print("1. Select paired phone / phone MAC")
        print("2. Select output device")
        print("3. Choose uplink source mode (mic, file, or soundboard)")
        print("4. Select microphone capture device")
        print("5. Select audio file / soundboard clip")
        print("6. Configure uplink stream tap")
        print("7. Save config")
        print("8. Pair phone / make adapter discoverable")
        print("9. Run preflight")
        print("10. Start bridge in foreground")
        print("11. Stop bridge/service")
        print("12. Reload config from disk")
        print("0. Save and exit")

    def prompt(self, message: str, default: Optional[str] = None) -> str:
        suffix = f" [{default}]" if default else ""
        try:
            value = input(f"{message}{suffix}: ").strip()
        except EOFError:
            return default or ""
        return value or (default or "")

    def pause(self) -> None:
        try:
            input("\nPress Enter to continue...")
        except EOFError:
            pass

    def save_if_needed(self) -> None:
        if self.dirty or not self.config_path.exists():
            self.save_config()

    def choose_phone(self) -> None:
        devices = list_bluetooth_devices()
        print("\nDetected Bluetooth devices:")
        if devices:
            for index, device in enumerate(devices, start=1):
                marker = " *" if self.cfg.get("PHONE_MAC", "").strip().lower() == device.mac.lower() else ""
                print(f"{index}. {device.name} ({device.mac}){marker}")
        else:
            print("No devices found via bluetoothctl. You can still enter a MAC manually.")
        print("m. Enter MAC manually")
        print("c. Clear PHONE_MAC")
        choice = self.prompt("Choose a device", default="m").lower()
        if choice == "c":
            self.set_value("PHONE_MAC", "")
            return
        if choice == "m":
            manual_mac = self.prompt("Enter phone MAC", default=self.cfg.get("PHONE_MAC", ""))
            if manual_mac and not MAC_RE.match(manual_mac):
                print("That does not look like a Bluetooth MAC address.")
                return
            self.set_value("PHONE_MAC", manual_mac.upper())
            return
        if choice.isdigit():
            index = int(choice) - 1
            if 0 <= index < len(devices):
                self.set_value("PHONE_MAC", devices[index].mac.upper())
                return
        print("Invalid selection.")

    def choose_output_device(self) -> None:
        devices = list_alsa_devices("playback")
        print("\nDetected playback devices:")
        if devices:
            for index, device in enumerate(devices, start=1):
                marker = " *" if self.cfg.get("AUX_PCM", "") == device.pcm else ""
                print(f"{index}. {device.label} -> {device.pcm}{marker}")
        else:
            print("No playback devices found via aplay -l. You can still enter a PCM manually.")
        print("m. Enter ALSA PCM manually")
        choice = self.prompt("Choose an output device", default="m").lower()
        if choice == "m":
            manual_pcm = self.prompt("Enter playback PCM", default=self.cfg.get("AUX_PCM", ""))
            if manual_pcm:
                self.set_value("AUX_PCM", manual_pcm)
            return
        if choice.isdigit():
            index = int(choice) - 1
            if 0 <= index < len(devices):
                self.set_value("AUX_PCM", devices[index].pcm)
                return
        print("Invalid selection.")

    def choose_uplink_source(self) -> None:
        current = self.cfg.get("UPLINK_SOURCE", "mic").strip().lower() or "mic"
        print("\n1. mic  -> capture from MIC_PCM")
        print("2. file -> loop an audio file into the call")
        print("3. soundboard -> watch a selector file and hot-swap the looping clip")
        default = {"mic": "1", "file": "2", "soundboard": "3"}.get(current, "1")
        choice = self.prompt("Choose uplink source", default=default)
        if choice == "1":
            self.set_value("UPLINK_SOURCE", "mic")
            return
        if choice == "2":
            self.set_value("UPLINK_SOURCE", "file")
            if not self.cfg.get("UPLINK_AUDIO_FILE", "").strip():
                self.choose_audio_file()
            return
        if choice == "3":
            self.set_value("UPLINK_SOURCE", "soundboard")
            if not (
                self.read_soundboard_selection() or self.cfg.get("UPLINK_AUDIO_FILE", "").strip()
            ):
                self.choose_audio_file()
            return
        print("Invalid selection.")

    def choose_mic_device(self) -> None:
        devices = list_alsa_devices("capture")
        print("\nDetected capture devices:")
        if devices:
            for index, device in enumerate(devices, start=1):
                marker = " *" if self.cfg.get("MIC_PCM", "") == device.pcm else ""
                print(f"{index}. {device.label} -> {device.pcm}{marker}")
        else:
            print("No capture devices found via arecord -l. You can still enter a PCM manually.")
        print("m. Enter ALSA PCM manually")
        choice = self.prompt("Choose a microphone device", default="m").lower()
        if choice == "m":
            manual_pcm = self.prompt("Enter microphone PCM", default=self.cfg.get("MIC_PCM", ""))
            if manual_pcm:
                self.set_value("MIC_PCM", manual_pcm)
            return
        if choice.isdigit():
            index = int(choice) - 1
            if 0 <= index < len(devices):
                self.set_value("MIC_PCM", devices[index].pcm)
                return
        print("Invalid selection.")

    def audio_candidates(self) -> List[Path]:
        candidates: Dict[str, Path] = {}
        for path in discover_audio_files(self.audio_dir):
            candidates[str(path)] = path
        return [candidates[key] for key in sorted(candidates)]

    def choose_audio_file(self) -> None:
        files = self.audio_candidates()
        source = self.cfg.get("UPLINK_SOURCE", "mic").strip().lower() or "mic"
        print("\nDetected audio files:")
        if files:
            for index, path in enumerate(files, start=1):
                display_path = format_config_path_value(path, self.config_dir)
                current_value = (
                    self.read_soundboard_selection()
                    if source == "soundboard"
                    else self.cfg.get("UPLINK_AUDIO_FILE", "")
                )
                marker = " *" if current_value == display_path else ""
                print(f"{index}. {display_path}{marker}")
        else:
            print("No audio files found under the search paths. You can still enter a path manually.")
        if source == "soundboard":
            print(f"Soundboard selector: {self.soundboard_selector_path()}")
            print("Selecting a clip updates the live soundboard selector file.")
        print("m. Enter audio file path manually")
        choice = self.prompt("Choose an audio file", default="m").lower()
        if choice == "m":
            manual_path = self.prompt(
                "Enter audio file path",
                default=(
                    self.read_soundboard_selection()
                    if source == "soundboard"
                    else self.cfg.get("UPLINK_AUDIO_FILE", "")
                ),
            )
            if manual_path:
                if source == "soundboard":
                    self.write_soundboard_selection(manual_path)
                    if not self.cfg.get("UPLINK_AUDIO_FILE", "").strip():
                        self.set_value("UPLINK_AUDIO_FILE", manual_path)
                    print(f"Updated soundboard clip via {self.soundboard_selector_path()}")
                else:
                    self.set_value("UPLINK_AUDIO_FILE", manual_path)
            return
        if choice.isdigit():
            index = int(choice) - 1
            if 0 <= index < len(files):
                selection = format_config_path_value(files[index], self.config_dir)
                if source == "soundboard":
                    self.write_soundboard_selection(selection)
                    if not self.cfg.get("UPLINK_AUDIO_FILE", "").strip():
                        self.set_value("UPLINK_AUDIO_FILE", selection)
                    print(f"Updated soundboard clip via {self.soundboard_selector_path()}")
                else:
                    self.set_value("UPLINK_AUDIO_FILE", selection)
                return
        print("Invalid selection.")

    def configure_tap(self) -> None:
        current_mode = self.cfg.get("UPLINK_TAP_MODE", "off").strip().lower() or "off"
        print("\n1. off     -> no extra copy of uplink audio")
        print("2. file    -> append raw PCM to a file or FIFO")
        print("3. command -> stream raw PCM to another process on stdin")
        print("           The tap uses mono 16-bit PCM at SCO_RATE.")
        print(
            "           Command taps also receive BT_BRIDGE_STREAM_SAMPLE_RATE, "
            "BT_BRIDGE_STREAM_CHANNELS, and BT_BRIDGE_STREAM_SAMPLE_FORMAT."
        )
        default = {"off": "1", "file": "2", "command": "3"}.get(current_mode, "1")
        choice = self.prompt("Choose tap mode", default=default)
        if choice == "1":
            self.set_value("UPLINK_TAP_MODE", "off")
            return
        if choice == "2":
            path_value = self.prompt(
                "Enter tap file/FIFO path",
                default=self.cfg.get("UPLINK_TAP_PATH", "/tmp/bt-call-bridge-uplink.pcm"),
            )
            if not path_value:
                print("Tap path cannot be empty.")
                return
            self.set_value("UPLINK_TAP_MODE", "file")
            self.set_value("UPLINK_TAP_PATH", path_value)
            return
        if choice == "3":
            command_value = self.prompt(
                "Enter tap command",
                default=self.cfg.get("UPLINK_TAP_COMMAND", ""),
            )
            if not command_value:
                print("Tap command cannot be empty.")
                return
            self.set_value("UPLINK_TAP_MODE", "command")
            self.set_value("UPLINK_TAP_COMMAND", command_value)
            return
        print("Invalid selection.")

    def run_command(self, command: List[str], heading: str) -> None:
        self.save_if_needed()
        print(f"\n{heading}")
        print(f"$ {shell_join(command)}\n")
        try:
            completed = subprocess.run(command, cwd=self.root_dir, check=False)
        except FileNotFoundError as exc:
            print(f"Failed to run command: {exc}")
            self.pause()
            return
        if completed.returncode != 0:
            print(f"\nCommand exited with rc={completed.returncode}")
        self.pause()

    def pair_phone(self) -> None:
        self.run_command(
            [str(self.root_dir / "pair_phone.sh"), str(self.config_path)],
            "Making the adapter pairable/discoverable...",
        )

    def run_preflight(self) -> None:
        self.run_command(
            [sys.executable, str(self.bridge_script), "--config", str(self.config_path), "--preflight"],
            "Running preflight checks...",
        )

    def start_bridge(self) -> None:
        self.run_command(
            [str(self.root_dir / "start.sh"), str(self.config_path)],
            "Starting the bridge in the foreground. Press Ctrl+C to stop it and return here.",
        )

    def stop_bridge(self) -> None:
        self.run_command(
            [str(self.root_dir / "stop.sh"), str(self.config_path)],
            "Stopping bridge processes and restoring regular audio services...",
        )

    def run(self) -> int:
        while True:
            self.show_menu()
            choice = self.prompt("Select an option", default="0").strip().lower()
            if choice == "1":
                self.choose_phone()
            elif choice == "2":
                self.choose_output_device()
            elif choice == "3":
                self.choose_uplink_source()
            elif choice == "4":
                self.choose_mic_device()
            elif choice == "5":
                self.choose_audio_file()
            elif choice == "6":
                self.configure_tap()
            elif choice == "7":
                self.save_config()
                self.pause()
            elif choice == "8":
                self.pair_phone()
            elif choice == "9":
                self.run_preflight()
            elif choice == "10":
                self.start_bridge()
            elif choice == "11":
                self.stop_bridge()
            elif choice == "12":
                self.load_config()
                print(f"\nReloaded configuration from {self.config_path}")
                self.pause()
            elif choice == "0":
                self.save_if_needed()
                return 0
            else:
                print("Invalid selection.")
                self.pause()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="SSH-friendly terminal UI for the Bluetooth call bridge")
    parser.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "bridge.conf"))
    parser.add_argument(
        "--audio-dir",
        help="Directory to scan for audio files when UPLINK_SOURCE=file",
    )
    args = parser.parse_args(argv)

    app = BridgeTerminalApp(
        config_path=Path(args.config),
        audio_dir=Path(args.audio_dir) if args.audio_dir else None,
    )
    try:
        return app.run()
    except BridgeError as exc:
        print(f"Bridge configuration error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nExiting terminal UI.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
