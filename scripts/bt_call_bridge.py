#!/usr/bin/env python3
import argparse
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


DEFAULTS: Dict[str, str] = {
    "PHONE_MAC": "58:43:AB:D9:8C:6E",
    "BT_ALIAS": "PiCallBridge",
    "BT_HCI": "hci0",
    "AUX_PCM": "plughw:CARD=Headphones,DEV=0",
    "MIC_PCM": "plughw:CARD=C920,DEV=0",
    "UPLINK_SOURCE": "mic",
    "UPLINK_AUDIO_FILE": "",
    "SCO_RATE": "16000",
    "AUTO_CONNECT": "1",
    "DISCOVERABLE": "1",
    "PAIRABLE": "1",
    "BLUEALSA_INITIAL_VOLUME": "70",
    "BLUEALSA_KEEP_ALIVE": "-1",
    "BLUEALSA_IO_RT_PRIORITY": "20",
    "ENABLE_A2DP_SINK": "0",
    "BLUEALSA_EXTRA_ARGS": "",
    "RESTART_DELAY_SECS": "2",
    "PAIRABLE_TIMEOUT": "0",
    "DISCOVERABLE_TIMEOUT": "0",
    "UPLINK_TAP_MODE": "off",
    "UPLINK_TAP_PATH": "",
    "UPLINK_TAP_COMMAND": "",
}

CONFIG_LAYOUT: List[Tuple[str, List[str]]] = [
    ("Bluetooth and phone", ["PHONE_MAC", "BT_ALIAS", "BT_HCI"]),
    (
        "Audio routing",
        [
            "AUX_PCM",
            "MIC_PCM",
            "UPLINK_SOURCE",
            "UPLINK_AUDIO_FILE",
            "SCO_RATE",
        ],
    ),
    (
        "Uplink stream tap",
        [
            "UPLINK_TAP_MODE",
            "UPLINK_TAP_PATH",
            "UPLINK_TAP_COMMAND",
        ],
    ),
    (
        "Bridge runtime",
        [
            "AUTO_CONNECT",
            "DISCOVERABLE",
            "PAIRABLE",
            "PAIRABLE_TIMEOUT",
            "DISCOVERABLE_TIMEOUT",
            "RESTART_DELAY_SECS",
        ],
    ),
    (
        "BlueALSA tuning",
        [
            "BLUEALSA_INITIAL_VOLUME",
            "BLUEALSA_KEEP_ALIVE",
            "BLUEALSA_IO_RT_PRIORITY",
            "ENABLE_A2DP_SINK",
            "BLUEALSA_EXTRA_ARGS",
        ],
    ),
]

SUPPORTED_AUDIO_EXTENSIONS = {
    ".aac",
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
    ".webm",
}
AUDIO_LIBRARY_DIRNAME = "audio"

ALSA_DEVICE_RE = re.compile(
    r"^card\s+(?P<card_index>\d+):\s+(?P<card_id>[^\s]+)\s+\[(?P<card_name>.*?)\],\s+"
    r"device\s+(?P<device_index>\d+):\s+(?P<device_id>[^\[]*?)\s+\[(?P<device_name>.*?)\]\s*$"
)


@dataclass
class ChildProcess:
    name: str
    cmd: List[str]
    proc: subprocess.Popen


@dataclass
class AlsaDevice:
    kind: str
    card_index: int
    card_id: str
    card_name: str
    device_index: int
    device_name: str

    @property
    def pcm(self) -> str:
        return f"plughw:CARD={self.card_id},DEV={self.device_index}"

    @property
    def label(self) -> str:
        return f"{self.card_name} / {self.device_name}"


@dataclass
class BluetoothDevice:
    mac: str
    name: str


class BridgeError(RuntimeError):
    pass


def shell_join(parts: Sequence[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def resolve_config_path_value(raw_path: str, config_dir: Path) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = config_dir / path
    return path.resolve(strict=False)


def format_config_path_value(path: Path, config_dir: Path) -> str:
    resolved_path = path.expanduser().resolve(strict=False)
    resolved_config_dir = config_dir.resolve(strict=False)
    try:
        return str(resolved_path.relative_to(resolved_config_dir))
    except ValueError:
        return str(resolved_path)


def parse_alsa_devices(output: str, kind: str) -> List[AlsaDevice]:
    devices: List[AlsaDevice] = []
    for raw_line in output.splitlines():
        match = ALSA_DEVICE_RE.match(raw_line.strip())
        if not match:
            continue
        devices.append(
            AlsaDevice(
                kind=kind,
                card_index=int(match.group("card_index")),
                card_id=match.group("card_id").strip(),
                card_name=match.group("card_name").strip(),
                device_index=int(match.group("device_index")),
                device_name=match.group("device_name").strip(),
            )
        )
    return devices


def list_alsa_devices(kind: str) -> List[AlsaDevice]:
    if kind not in {"playback", "capture"}:
        raise ValueError("kind must be 'playback' or 'capture'")

    cmd_name = "aplay" if kind == "playback" else "arecord"
    cmd_path = shutil.which(cmd_name)
    if not cmd_path:
        return []

    cp = subprocess.run([cmd_path, "-l"], capture_output=True, text=True, check=False)
    combined_output = cp.stdout
    if cp.stderr:
        combined_output = f"{combined_output}\n{cp.stderr}"
    return list(
        sorted(
            parse_alsa_devices(combined_output, kind),
            key=lambda device: (device.card_index, device.device_index),
        )
    )


def list_bluetooth_devices() -> List[BluetoothDevice]:
    cmd_path = shutil.which("bluetoothctl")
    if not cmd_path:
        return []

    cp = subprocess.run([cmd_path, "devices"], capture_output=True, text=True, check=False)
    devices: List[BluetoothDevice] = []
    for raw_line in cp.stdout.splitlines():
        line = raw_line.strip()
        if not line.startswith("Device "):
            continue
        try:
            _, mac, name = line.split(maxsplit=2)
        except ValueError:
            continue
        devices.append(BluetoothDevice(mac=mac.strip(), name=name.strip()))
    return devices


def discover_audio_files(search_root: Path, max_depth: int = 4) -> List[Path]:
    resolved_root = search_root.expanduser().resolve(strict=False)
    if not resolved_root.exists():
        return []

    audio_files: List[Path] = []
    for path in resolved_root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_AUDIO_EXTENSIONS:
            continue
        try:
            depth = len(path.relative_to(resolved_root).parts)
        except ValueError:
            continue
        if depth > max_depth:
            continue
        audio_files.append(path.resolve(strict=False))
    return sorted(audio_files)


def resolve_audio_library_path(config_dir: Path, filename: str) -> Path:
    return (config_dir / AUDIO_LIBRARY_DIRNAME / filename).resolve(strict=False)


class BTCallBridge:
    def __init__(self, cfg: Dict[str, str], config_path: Optional[str] = None):
        self.cfg = cfg
        self.children: Dict[str, ChildProcess] = {}
        self.should_stop = False
        self.config_dir = Path(config_path).resolve().parent if config_path else Path.cwd()
        self.uplink_source = self.cfg.get("UPLINK_SOURCE", "mic").strip().lower() or "mic"
        if self.uplink_source not in {"mic", "file"}:
            raise BridgeError("UPLINK_SOURCE must be 'mic' or 'file'")
        self.uplink_tap_mode = self._resolve_uplink_tap_mode()
        self.python_cmd = shutil.which("python3") or sys.executable
        if not self.python_cmd:
            raise BridgeError("Could not locate python3 for the uplink fanout helper")
        self.stream_fanout_script = Path(__file__).resolve().with_name("pcm_stream_fanout.py")
        self.systemctl_cmd = shutil.which("systemctl")
        self.busctl_cmd = shutil.which("busctl")
        self.pkill_cmd = shutil.which("pkill")
        self.run_as_user = os.environ.get("SUDO_USER") or os.environ.get("USER") or "tyrion"
        self.run_as_uid = self._detect_run_as_uid()
        self.bluealsa_cmd = self._find_first(["bluealsad", "bluealsa"])
        self.bluealsa_aplay_cmd = self._find_required("bluealsa-aplay")
        self.bluetoothctl_cmd = self._find_required("bluetoothctl")
        self.aplay_cmd = self._find_required("aplay")
        self.arecord_cmd: Optional[str] = None
        self.ffmpeg_cmd: Optional[str] = None
        if self.uplink_source == "mic":
            self.arecord_cmd = self._find_required("arecord")
        else:
            self.ffmpeg_cmd = self._find_required("ffmpeg")
        self._register_signals()

    @staticmethod
    def _find_first(names: List[str]) -> str:
        for name in names:
            path = shutil.which(name)
            if path:
                return path
        raise BridgeError(
            "Could not find BlueALSA daemon binary. Expected 'bluealsad' or 'bluealsa' in PATH."
        )

    @staticmethod
    def _find_required(name: str) -> str:
        path = shutil.which(name)
        if not path:
            raise BridgeError(f"Required command not found in PATH: {name}")
        return path

    @staticmethod
    def _parse_bool(value: str) -> bool:
        return value.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _timestamp() -> str:
        return time.strftime("%Y-%m-%d %H:%M:%S")

    def _resolve_uplink_tap_mode(self) -> str:
        mode = self.cfg.get("UPLINK_TAP_MODE", "off").strip().lower() or "off"
        if mode not in {"off", "file", "command"}:
            raise BridgeError("UPLINK_TAP_MODE must be 'off', 'file', or 'command'")
        return mode

    def _detect_run_as_uid(self) -> str:
        try:
            cp = subprocess.run(
                ["id", "-u", self.run_as_user],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return "1000"
        if cp.returncode == 0 and cp.stdout.strip():
            return cp.stdout.strip()
        return "1000"

    def log(self, msg: str) -> None:
        print(f"[{self._timestamp()}] {msg}", flush=True)

    def _register_signals(self) -> None:
        def handler(signum, _frame):
            self.log(f"Received signal {signum}; shutting down.")
            self.should_stop = True
            self.stop_all()
            sys.exit(0)

        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

    def btctl(self, commands: List[str], check: bool = False) -> subprocess.CompletedProcess:
        payload = "\n".join(commands + ["quit"]) + "\n"
        self.log(f"bluetoothctl <= {' ; '.join(commands)}")
        cp = subprocess.run(
            [self.bluetoothctl_cmd],
            input=payload,
            text=True,
            capture_output=True,
            check=False,
        )
        if cp.stdout.strip():
            self.log(cp.stdout.strip())
        if cp.stderr.strip():
            self.log(cp.stderr.strip())
        if check and cp.returncode != 0:
            raise BridgeError(f"bluetoothctl failed with rc={cp.returncode}")
        return cp

    def configure_adapter(self) -> None:
        agent_capability = self.cfg.get("BT_AGENT_CAPABILITY", "").strip()
        commands = ["power on"]
        if agent_capability:
            commands.append(f"agent {agent_capability}")
        else:
            commands.append("agent on")
        commands.append("default-agent")
        alias = self.cfg["BT_ALIAS"].strip()
        if alias:
            commands.append(f"system-alias {alias}")
        if self._parse_bool(self.cfg["PAIRABLE"]):
            commands.append("pairable on")
        if self._parse_bool(self.cfg["DISCOVERABLE"]):
            commands.append("discoverable on")
            commands.append(f"discoverable-timeout {self.cfg['DISCOVERABLE_TIMEOUT']}")
        self.btctl(commands)

    def stop_conflicting_services(self) -> None:
        if os.geteuid() != 0:
            return
        if self.systemctl_cmd:
            services = [
                "bluealsa.service",
                "bluealsa-aplay.service",
                "bt-speaker-agent.service",
            ]
            subprocess.run(
                [self.systemctl_cmd, "stop", *services],
                capture_output=True,
                text=True,
                check=False,
            )
        if self.pkill_cmd:
            for pattern in (
                "/usr/bin/bluealsa -S",
                "/usr/bin/bluealsa-aplay -S",
            ):
                subprocess.run(
                    [self.pkill_cmd, "-f", pattern],
                    capture_output=True,
                    text=True,
                    check=False,
                )
        if self.systemctl_cmd:
            user_runtime_dir = f"/run/user/{self.run_as_uid}"
            user_bus_addr = f"unix:path={user_runtime_dir}/bus"
            for unit in (
                "pipewire.service",
                "pipewire-pulse.service",
                "wireplumber.service",
                "pipewire.socket",
                "pipewire-pulse.socket",
            ):
                subprocess.run(
                    [
                        "sudo",
                        "-u",
                        self.run_as_user,
                        "env",
                        f"XDG_RUNTIME_DIR={user_runtime_dir}",
                        f"DBUS_SESSION_BUS_ADDRESS={user_bus_addr}",
                        self.systemctl_cmd,
                        "--user",
                        "stop",
                        unit,
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )

    def bluealsa_name_is_owned(self) -> bool:
        if not self.busctl_cmd:
            return False
        cp = subprocess.run(
            [self.busctl_cmd, "--system", "list"],
            capture_output=True,
            text=True,
            check=False,
        )
        if cp.returncode != 0:
            return False
        return "org.bluealsa" in cp.stdout

    def ensure_bluealsa_ready(self) -> bool:
        child = self.children.get("bluealsa")
        if child is None or child.proc.poll() is not None:
            self.stop_conflicting_services()
            self.spawn("bluealsa", self.build_bluealsa_cmd())
            child = self.children["bluealsa"]

        if not self.busctl_cmd:
            time.sleep(1)
            return child.proc.poll() is None

        deadline = time.time() + 5
        while time.time() < deadline:
            if child.proc.poll() is not None:
                self.log(f"Process bluealsa exited with rc={child.proc.returncode} before it became ready.")
                return False
            if self.bluealsa_name_is_owned():
                return True
            time.sleep(0.2)
        self.log("Timed out waiting for BlueALSA to claim org.bluealsa.")
        return False

    def trust_and_connect_phone(self) -> None:
        phone_mac = self.cfg["PHONE_MAC"].strip()
        if not phone_mac:
            self.log("PHONE_MAC is empty; skipping trust/connect step.")
            return
        commands = [f"trust {phone_mac}"]
        if self._parse_bool(self.cfg["AUTO_CONNECT"]):
            commands.append(f"connect {phone_mac}")
        self.btctl(commands)

    def build_bluealsa_cmd(self) -> List[str]:
        cmd = [self.bluealsa_cmd]
        cmd.extend(
            [
                "--initial-volume",
                self.cfg["BLUEALSA_INITIAL_VOLUME"],
                "--keep-alive",
                self.cfg["BLUEALSA_KEEP_ALIVE"],
                "--io-rt-priority",
                self.cfg["BLUEALSA_IO_RT_PRIORITY"],
                "--device",
                self.cfg["BT_HCI"],
                "--profile",
                "hfp-hf",
                "--profile",
                "hsp-hs",
            ]
        )
        if self._parse_bool(self.cfg["ENABLE_A2DP_SINK"]):
            cmd.extend(["--profile", "a2dp-sink"])
        extra = self.cfg.get("BLUEALSA_EXTRA_ARGS", "").strip()
        if extra:
            cmd.extend(shlex.split(extra))
        return cmd

    def build_downlink_cmd(self) -> List[str]:
        phone_mac = self.cfg["PHONE_MAC"].strip() or "00:00:00:00:00:00"
        return [
            self.bluealsa_aplay_cmd,
            "--profile-sco",
            f"--pcm={self.cfg['AUX_PCM']}",
            phone_mac,
        ]

    def resolve_uplink_audio_file(self) -> Path:
        raw_path = self.cfg.get("UPLINK_AUDIO_FILE", "").strip()
        if not raw_path:
            raise BridgeError("UPLINK_SOURCE=file requires UPLINK_AUDIO_FILE to be set")

        audio_path = resolve_config_path_value(raw_path, self.config_dir)
        if not audio_path.is_file() and Path(raw_path).name == raw_path:
            audio_library_path = resolve_audio_library_path(self.config_dir, raw_path)
            if audio_library_path.is_file():
                return audio_library_path
        if not audio_path.is_file():
            raise BridgeError(f"Configured uplink audio file not found: {audio_path}")
        return audio_path

    def resolve_uplink_tap_path(self) -> Path:
        raw_path = self.cfg.get("UPLINK_TAP_PATH", "").strip()
        if not raw_path:
            raise BridgeError("UPLINK_TAP_MODE=file requires UPLINK_TAP_PATH to be set")
        return resolve_config_path_value(raw_path, self.config_dir)

    def build_mic_uplink_source_cmd(self) -> List[str]:
        sco_rate = self.cfg["SCO_RATE"].strip()
        mic_pcm = self.cfg["MIC_PCM"].strip()
        assert self.arecord_cmd is not None
        return [
            self.arecord_cmd,
            "-D",
            mic_pcm,
            "-q",
            "-f",
            "S16_LE",
            "-c",
            "1",
            "-r",
            sco_rate,
        ]

    def build_file_uplink_source_cmd(self) -> List[str]:
        sco_rate = self.cfg["SCO_RATE"].strip()
        audio_file = self.resolve_uplink_audio_file()
        assert self.ffmpeg_cmd is not None
        return [
            self.ffmpeg_cmd,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-stream_loop",
            "-1",
            "-re",
            "-i",
            str(audio_file),
            "-vn",
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ac",
            "1",
            "-ar",
            sco_rate,
            "-",
        ]

    def build_uplink_sink_cmd(self) -> List[str]:
        phone_mac = self.cfg["PHONE_MAC"].strip() or "00:00:00:00:00:00"
        sco_rate = self.cfg["SCO_RATE"].strip()
        bluealsa_pcm = f"bluealsa:DEV={phone_mac},PROFILE=sco"
        return [
            self.aplay_cmd,
            "-D",
            bluealsa_pcm,
            "-q",
            "-f",
            "S16_LE",
            "-c",
            "1",
            "-r",
            sco_rate,
        ]

    def build_uplink_fanout_cmd(self) -> Optional[List[str]]:
        if self.uplink_tap_mode == "off":
            return None
        if not self.stream_fanout_script.is_file():
            raise BridgeError(f"Missing uplink fanout helper: {self.stream_fanout_script}")

        cmd = [
            self.python_cmd,
            str(self.stream_fanout_script),
            "--sample-rate",
            self.cfg["SCO_RATE"].strip(),
            "--channels",
            "1",
            "--sample-format",
            "S16_LE",
        ]
        if self.uplink_tap_mode == "file":
            cmd.extend(["--tap-path", str(self.resolve_uplink_tap_path())])
        elif self.uplink_tap_mode == "command":
            tap_command = self.cfg.get("UPLINK_TAP_COMMAND", "").strip()
            if not tap_command:
                raise BridgeError(
                    "UPLINK_TAP_MODE=command requires UPLINK_TAP_COMMAND to be set"
                )
            cmd.extend(["--tap-command", tap_command])
        return cmd

    def build_uplink_cmd(self) -> List[str]:
        source_cmd = (
            self.build_file_uplink_source_cmd()
            if self.uplink_source == "file"
            else self.build_mic_uplink_source_cmd()
        )
        pipeline_parts = [shell_join(source_cmd)]
        fanout_cmd = self.build_uplink_fanout_cmd()
        if fanout_cmd:
            pipeline_parts.append(shell_join(fanout_cmd))
        pipeline_parts.append(shell_join(self.build_uplink_sink_cmd()))
        pipeline = "set -o pipefail; " + " | ".join(pipeline_parts)
        return ["bash", "-lc", pipeline]

    def spawn(self, name: str, cmd: List[str]) -> None:
        self.log(f"Starting {name}: {' '.join(shlex.quote(p) for p in cmd)}")
        proc = subprocess.Popen(cmd)
        self.children[name] = ChildProcess(name=name, cmd=cmd, proc=proc)

    def ensure_started(self, name: str, cmd: List[str]) -> None:
        child = self.children.get(name)
        if child is None:
            self.spawn(name, cmd)
            return
        rc = child.proc.poll()
        if rc is not None:
            self.log(f"Process {name} exited with rc={rc}; restarting.")
            time.sleep(float(self.cfg["RESTART_DELAY_SECS"]))
            self.spawn(name, cmd)

    def stop_all(self) -> None:
        for name, child in list(self.children.items()):
            proc = child.proc
            if proc.poll() is None:
                self.log(f"Stopping {name} (pid={proc.pid}).")
                proc.terminate()
        deadline = time.time() + 5
        while time.time() < deadline:
            alive = [c for c in self.children.values() if c.proc.poll() is None]
            if not alive:
                break
            time.sleep(0.2)
        for name, child in list(self.children.items()):
            proc = child.proc
            if proc.poll() is None:
                self.log(f"Killing {name} (pid={proc.pid}).")
                proc.kill()
        self.children.clear()

    def run(self) -> None:
        self.configure_adapter()
        while not self.should_stop:
            if not self.ensure_bluealsa_ready():
                time.sleep(float(self.cfg["RESTART_DELAY_SECS"]))
                continue
            self.trust_and_connect_phone()
            self.ensure_started("downlink", self.build_downlink_cmd())
            self.ensure_started("uplink", self.build_uplink_cmd())
            if self._parse_bool(self.cfg["AUTO_CONNECT"]):
                self.trust_and_connect_phone()
            time.sleep(5)


def parse_kv_config(path: str) -> Dict[str, str]:
    cfg = dict(DEFAULTS)
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            cfg[key.strip()] = value.strip().strip('"')
    return cfg


def render_kv_config(cfg: Dict[str, str]) -> str:
    lines: List[str] = [
        "# Bluetooth Call Bridge configuration",
        "# Generated by bt_call_bridge.py / bt_call_bridge_terminal.py",
        "",
    ]
    rendered_keys = set()
    for section_name, section_keys in CONFIG_LAYOUT:
        lines.append(f"# {section_name}")
        for key in section_keys:
            rendered_keys.add(key)
            lines.append(f"{key}={cfg.get(key, DEFAULTS.get(key, ''))}")
        lines.append("")

    extra_keys = sorted(key for key in cfg if key not in rendered_keys)
    if extra_keys:
        lines.append("# Extra keys")
        for key in extra_keys:
            lines.append(f"{key}={cfg[key]}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_kv_config(path: Path, cfg: Dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_kv_config(cfg), encoding="utf-8")


def list_alsa() -> int:
    for kind, title in (("playback", "Playback"), ("capture", "Capture")):
        devices = list_alsa_devices(kind)
        print(f"=== {title} devices ===")
        if not devices:
            print("No ALSA devices found or the required command is unavailable.")
        else:
            for index, device in enumerate(devices, start=1):
                print(f"{index}. {device.label}")
                print(f"   PCM: {device.pcm}")
        print()
    return 0


def list_bt_devices() -> int:
    devices = list_bluetooth_devices()
    if not devices:
        print("No Bluetooth devices found or bluetoothctl is unavailable.")
        return 0
    for index, device in enumerate(devices, start=1):
        print(f"{index}. {device.name} ({device.mac})")
    return 0


def preflight(path: str) -> int:
    cfg = parse_kv_config(path)
    bridge = BTCallBridge(cfg, config_path=path)
    print("BlueALSA daemon:", bridge.bluealsa_cmd)
    print("bluealsa-aplay:", bridge.bluealsa_aplay_cmd)
    print("bluetoothctl:", bridge.bluetoothctl_cmd)
    print("aplay:", bridge.aplay_cmd)
    print("UPLINK_SOURCE:", bridge.uplink_source)
    if bridge.uplink_source == "mic":
        print("arecord:", bridge.arecord_cmd)
        print("MIC_PCM:", cfg["MIC_PCM"])
    else:
        print("ffmpeg:", bridge.ffmpeg_cmd)
        print("UPLINK_AUDIO_FILE:", bridge.resolve_uplink_audio_file())
    print("PHONE_MAC:", cfg["PHONE_MAC"] or "<empty>")
    print("AUX_PCM:", cfg["AUX_PCM"])
    print("UPLINK_TAP_MODE:", bridge.uplink_tap_mode)
    if bridge.uplink_tap_mode == "file":
        print("UPLINK_TAP_PATH:", bridge.resolve_uplink_tap_path())
    elif bridge.uplink_tap_mode == "command":
        print("UPLINK_TAP_COMMAND:", cfg["UPLINK_TAP_COMMAND"])
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bluetooth phone-call bridge for Raspberry Pi using BlueALSA + ALSA"
    )
    parser.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "bridge.conf"))
    parser.add_argument("--list-alsa", action="store_true")
    parser.add_argument("--list-bt-devices", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args(argv)

    if args.list_alsa:
        return list_alsa()
    if args.list_bt_devices:
        return list_bt_devices()
    if args.preflight:
        return preflight(args.config)

    cfg = parse_kv_config(args.config)
    bridge = BTCallBridge(cfg, config_path=args.config)
    bridge.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
