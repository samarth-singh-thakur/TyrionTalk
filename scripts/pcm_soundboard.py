#!/usr/bin/env python3
import argparse
import select
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Tuple


def resolve_audio_path(raw_path: str, config_dir: Path, audio_dir: Path) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path.resolve(strict=False)

    audio_relative = path
    if path.parts and path.parts[0] == "audio":
        audio_relative = Path(*path.parts[1:])

    audio_path = (audio_dir / audio_relative).resolve(strict=False)
    if path.parts and path.parts[0] == "audio" and audio_path.is_file():
        return audio_path

    config_path = (config_dir / path).resolve(strict=False)
    if config_path.is_file():
        return config_path

    if audio_path.is_file():
        return audio_path

    return audio_path if path.parts and path.parts[0] == "audio" else config_path


def read_selection(selector_path: Path, default_selection: str) -> str:
    try:
        value = selector_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return default_selection
    except OSError:
        return default_selection
    return value or default_selection


def ffmpeg_command(ffmpeg_cmd: str, audio_path: Path, sample_rate: int, channels: int) -> list[str]:
    return [
        ffmpeg_cmd,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-stream_loop",
        "-1",
        "-re",
        "-i",
        str(audio_path),
        "-vn",
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate),
        "-",
    ]


class SoundboardLoop:
    def __init__(
        self,
        selector_path: Path,
        default_selection: str,
        config_dir: Path,
        audio_dir: Path,
        sample_rate: int,
        channels: int,
    ) -> None:
        ffmpeg_cmd = shutil.which("ffmpeg")
        if not ffmpeg_cmd:
            raise RuntimeError("ffmpeg is required for soundboard mode")

        self.ffmpeg_cmd = ffmpeg_cmd
        self.selector_path = selector_path
        self.default_selection = default_selection
        self.config_dir = config_dir
        self.audio_dir = audio_dir
        self.sample_rate = sample_rate
        self.channels = channels
        self.proc: Optional[subprocess.Popen] = None
        self.current_key: Optional[str] = None
        self.should_stop = False

        signal.signal(signal.SIGINT, self.handle_signal)
        signal.signal(signal.SIGTERM, self.handle_signal)

    def handle_signal(self, _signum, _frame) -> None:
        self.should_stop = True
        self.stop_proc()

    def resolve_active_selection(self) -> Tuple[str, Path]:
        selection = read_selection(self.selector_path, self.default_selection)
        if not selection:
            raise RuntimeError("Soundboard mode requires a default or selected audio file")
        resolved = resolve_audio_path(selection, self.config_dir, self.audio_dir)
        if not resolved.is_file():
            raise RuntimeError(f"Selected soundboard audio file not found: {resolved}")
        return selection, resolved

    def stop_proc(self) -> None:
        proc = self.proc
        self.proc = None
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)

    def restart_if_needed(self) -> None:
        selection_key, audio_path = self.resolve_active_selection()
        if self.proc is not None and self.proc.poll() is None and self.current_key == selection_key:
            return

        self.stop_proc()
        self.current_key = selection_key
        self.proc = subprocess.Popen(
            ffmpeg_command(self.ffmpeg_cmd, audio_path, self.sample_rate, self.channels),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    def run(self) -> int:
        while not self.should_stop:
            try:
                self.restart_if_needed()
            except RuntimeError as exc:
                print(f"pcm_soundboard.py: {exc}", file=sys.stderr, flush=True)
                time.sleep(1)
                continue

            proc = self.proc
            if proc is None or proc.stdout is None:
                time.sleep(0.1)
                continue

            ready, _, _ = select.select([proc.stdout], [], [], 0.2)
            if ready:
                chunk = proc.stdout.read1(8192)
                if chunk:
                    sys.stdout.buffer.write(chunk)
                    sys.stdout.buffer.flush()

            if proc.poll() is not None:
                self.proc = None
                time.sleep(0.2)

        return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Loop a selected soundboard clip and hot-swap on selector changes")
    parser.add_argument("--selector-path", required=True)
    parser.add_argument("--default-selection", default="")
    parser.add_argument("--config-dir", required=True)
    parser.add_argument("--audio-dir", required=True)
    parser.add_argument("--sample-rate", type=int, required=True)
    parser.add_argument("--channels", type=int, default=1)
    args = parser.parse_args(argv)

    loop = SoundboardLoop(
        selector_path=Path(args.selector_path).expanduser().resolve(strict=False),
        default_selection=args.default_selection.strip(),
        config_dir=Path(args.config_dir).expanduser().resolve(strict=False),
        audio_dir=Path(args.audio_dir).expanduser().resolve(strict=False),
        sample_rate=args.sample_rate,
        channels=args.channels,
    )
    return loop.run()


if __name__ == "__main__":
    raise SystemExit(main())
