#!/usr/bin/env python3
import argparse
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import BinaryIO, Optional


CHUNK_SIZE = 4096


def timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def log(message: str) -> None:
    print(f"[{timestamp()}] {message}", file=sys.stderr, flush=True)


def write_fd(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def open_tap_path(path: Path) -> Optional[BinaryIO]:
    resolved_path = path.expanduser().resolve(strict=False)
    if resolved_path.exists() and stat.S_ISFIFO(resolved_path.stat().st_mode):
        try:
            fd = os.open(resolved_path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            log(f"FIFO tap unavailable at {resolved_path}: {exc}. Continuing without file tap.")
            return None
        return os.fdopen(fd, "wb", buffering=0)

    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    return open(resolved_path, "ab", buffering=0)


def start_tap_command(command: str, sample_rate: str, channels: str, sample_format: str) -> subprocess.Popen:
    env = os.environ.copy()
    env["BT_BRIDGE_STREAM_SAMPLE_RATE"] = sample_rate
    env["BT_BRIDGE_STREAM_CHANNELS"] = channels
    env["BT_BRIDGE_STREAM_SAMPLE_FORMAT"] = sample_format
    env["BT_BRIDGE_STREAM_ENCODING"] = "pcm_s16le"
    return subprocess.Popen(
        ["bash", "-lc", command],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=None,
        env=env,
        bufsize=0,
    )


def close_quietly(handle: Optional[BinaryIO]) -> None:
    if handle is None:
        return
    try:
        handle.close()
    except OSError:
        pass


def close_command(proc: Optional[subprocess.Popen]) -> None:
    if proc is None:
        return
    if proc.stdin:
        try:
            proc.stdin.close()
        except OSError:
            pass
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Mirror raw PCM stdin to stdout and optionally to a file or command."
    )
    parser.add_argument("--tap-path", help="Append the stream to this file or FIFO.")
    parser.add_argument(
        "--tap-command",
        help="Run a command and forward the stream to the command's stdin.",
    )
    parser.add_argument("--sample-rate", default="16000")
    parser.add_argument("--channels", default="1")
    parser.add_argument("--sample-format", default="S16_LE")
    args = parser.parse_args(argv)

    tap_file: Optional[BinaryIO] = None
    tap_proc: Optional[subprocess.Popen] = None
    should_stop = False

    def handle_signal(_signum, _frame):
        nonlocal should_stop
        should_stop = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    if args.tap_path:
        tap_file = open_tap_path(Path(args.tap_path))
        if tap_file:
            log(f"Writing a copy of the uplink stream to {Path(args.tap_path).resolve(strict=False)}")

    if args.tap_command:
        tap_proc = start_tap_command(
            args.tap_command,
            sample_rate=args.sample_rate,
            channels=args.channels,
            sample_format=args.sample_format,
        )
        log(f"Streaming a copy of the uplink stream into command: {args.tap_command}")

    try:
        while not should_stop:
            chunk = os.read(0, CHUNK_SIZE)
            if not chunk:
                break
            write_fd(1, chunk)

            if tap_file is not None:
                try:
                    tap_file.write(chunk)
                except BrokenPipeError:
                    log("Tap file/FIFO disconnected; disabling file tap.")
                    close_quietly(tap_file)
                    tap_file = None
                except OSError as exc:
                    log(f"Tap file write failed ({exc}); disabling file tap.")
                    close_quietly(tap_file)
                    tap_file = None

            if tap_proc is not None and tap_proc.stdin is not None:
                if tap_proc.poll() is not None:
                    log(f"Tap command exited with rc={tap_proc.returncode}; disabling command tap.")
                    close_command(tap_proc)
                    tap_proc = None
                else:
                    try:
                        tap_proc.stdin.write(chunk)
                    except BrokenPipeError:
                        log("Tap command closed stdin; disabling command tap.")
                        close_command(tap_proc)
                        tap_proc = None
                    except OSError as exc:
                        log(f"Tap command write failed ({exc}); disabling command tap.")
                        close_command(tap_proc)
                        tap_proc = None
    finally:
        close_quietly(tap_file)
        close_command(tap_proc)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
