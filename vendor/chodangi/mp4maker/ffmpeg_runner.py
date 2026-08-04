"""Thin subprocess wrapper around ffmpeg / ffprobe with command logging."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional


class FFmpegError(RuntimeError):
    def __init__(self, message: str, cmd: list[str], stderr: str = ""):
        super().__init__(message)
        self.cmd = cmd
        self.stderr = stderr


def probe_binary(name: str) -> Optional[str]:
    """Return absolute path to ffmpeg/ffprobe or None if not on PATH."""
    return shutil.which(name)


def require_binaries() -> None:
    missing = [n for n in ("ffmpeg", "ffprobe") if probe_binary(n) is None]
    if missing:
        raise FFmpegError(
            f"Missing on PATH: {', '.join(missing)}. "
            f"Install with: winget install Gyan.FFmpeg",
            cmd=[],
        )


def ffprobe_duration(path: Path) -> float:
    """Return media duration in seconds via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(path),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise FFmpegError(f"ffprobe failed on {path}", cmd, out.stderr)
    data = json.loads(out.stdout or "{}")
    try:
        return float(data["format"]["duration"])
    except (KeyError, ValueError) as e:
        raise FFmpegError(f"ffprobe gave no duration for {path}", cmd, out.stderr) from e


def run_ffmpeg(cmd: list[str], log_path: Optional[Path] = None) -> None:
    """Run ffmpeg, write stderr to log_path on failure, raise on non-zero exit."""
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(
                "CMD:\n" + " ".join(_quote(a) for a in cmd) + "\n\nSTDERR:\n" + (proc.stderr or ""),
                encoding="utf-8",
            )
        raise FFmpegError(
            f"ffmpeg failed (exit {proc.returncode}). See log: {log_path}",
            cmd,
            proc.stderr or "",
        )


def dump_cmd_script(cmd: list[str], path: Path) -> None:
    """Write a .cmd file that reproduces the ffmpeg call."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "@echo off\r\n" + " ".join(_quote(a) for a in cmd) + "\r\n",
        encoding="utf-8",
    )


def _quote(arg: str) -> str:
    if not arg:
        return '""'
    if any(ch in arg for ch in ' \t"&|<>^()'):
        return '"' + arg.replace('"', '\\"') + '"'
    return arg
