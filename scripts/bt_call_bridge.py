#!/usr/bin/env python3
import argparse
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


DEFAULTS: Dict[str, str] = {
    "PHONE_MAC": "",
    "BT_ALIAS": "PiCallBridge",
    "BT_HCI": "hci0",
    "AUX_PCM": "plughw:CARD=Headphones,DEV=0",
    "MIC_PCM": "plughw:CARD=C920,DEV=0",
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
}


@dataclass
class ChildProcess:
    name: str
    cmd: List[str]
    proc: subprocess.Popen


class BridgeError(RuntimeError):
    pass


class BTCallBridge:
    def __init__(self, cfg: Dict[str, str]):
        self.cfg = cfg
        self.children: Dict[str, ChildProcess] = {}
        self.should_stop = False
        self.bluealsa_cmd = self._find_first(["bluealsad", "bluealsa"])
        self.bluealsa_aplay_cmd = self._find_required("bluealsa-aplay")
        self.bluetoothctl_cmd = self._find_required("bluetoothctl")
        self.arecord_cmd = self._find_required("arecord")
        self.aplay_cmd = self._find_required("aplay")
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
        commands = ["power on", "agent on", "default-agent"]
        alias = self.cfg["BT_ALIAS"].strip()
        if alias:
            commands.append(f"system-alias {alias}")
        if self._parse_bool(self.cfg["PAIRABLE"]):
            commands.append("pairable on")
            commands.append(f"pairable-timeout {self.cfg['PAIRABLE_TIMEOUT']}")
        if self._parse_bool(self.cfg["DISCOVERABLE"]):
            commands.append("discoverable on")
            commands.append(f"discoverable-timeout {self.cfg['DISCOVERABLE_TIMEOUT']}")
        self.btctl(commands)

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
        # bluealsad and bluealsa accept the same core options for this use-case.
        cmd.extend([
            "--initial-volume", self.cfg["BLUEALSA_INITIAL_VOLUME"],
            "--keep-alive", self.cfg["BLUEALSA_KEEP_ALIVE"],
            "--io-rt-priority", self.cfg["BLUEALSA_IO_RT_PRIORITY"],
            "--device", self.cfg["BT_HCI"],
            "--profile", "hfp-hf",
            "--profile", "hsp-hs",
        ])
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
            "--profile=SCO",
            f"--pcm={self.cfg['AUX_PCM']}",
            phone_mac,
        ]

    def build_uplink_cmd(self) -> List[str]:
        phone_mac = self.cfg["PHONE_MAC"].strip() or "00:00:00:00:00:00"
        sco_rate = self.cfg["SCO_RATE"].strip()
        mic_pcm = self.cfg["MIC_PCM"].strip()
        bluealsa_pcm = f"bluealsa:DEV={phone_mac},PROFILE=sco,HWCOMPAT=silence"
        pipeline = (
            f"exec {shlex.quote(self.arecord_cmd)} -D {shlex.quote(mic_pcm)} -q -f S16_LE -c 1 -r {shlex.quote(sco_rate)} "
            f"| {shlex.quote(self.aplay_cmd)} -D {shlex.quote(bluealsa_pcm)} -q -f S16_LE -c 1 -r {shlex.quote(sco_rate)}"
        )
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
        self.trust_and_connect_phone()
        while not self.should_stop:
            self.ensure_started("bluealsa", self.build_bluealsa_cmd())
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


def list_alsa() -> int:
    cmds = [
        ["bash", "-lc", "echo '=== aplay -l ==='; aplay -l || true; echo; echo '=== arecord -l ==='; arecord -l || true"],
    ]
    for cmd in cmds:
        subprocess.run(cmd, check=False)
    return 0


def preflight(path: str) -> int:
    cfg = parse_kv_config(path)
    bridge = BTCallBridge(cfg)
    print("BlueALSA daemon:", bridge.bluealsa_cmd)
    print("bluealsa-aplay:", bridge.bluealsa_aplay_cmd)
    print("bluetoothctl:", bridge.bluetoothctl_cmd)
    print("arecord:", bridge.arecord_cmd)
    print("aplay:", bridge.aplay_cmd)
    print("PHONE_MAC:", cfg["PHONE_MAC"] or "<empty>")
    print("AUX_PCM:", cfg["AUX_PCM"])
    print("MIC_PCM:", cfg["MIC_PCM"])
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Bluetooth phone-call bridge for Raspberry Pi using BlueALSA + ALSA")
    parser.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "bridge.conf"))
    parser.add_argument("--list-alsa", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args(argv)

    if args.list_alsa:
        return list_alsa()
    if args.preflight:
        return preflight(args.config)

    cfg = parse_kv_config(args.config)
    bridge = BTCallBridge(cfg)
    bridge.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
