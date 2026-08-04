"""Concatenate per-scene MP4s with xfade (video) + acrossfade (audio)."""
from __future__ import annotations

from pathlib import Path

from .ffmpeg_runner import run_ffmpeg, dump_cmd_script
from .timeline import TimelineEntry


def concat_with_crossfade(
    scene_clips: list[Path],
    entries: list[TimelineEntry],
    out_path: Path,
    crossfade: float = 0.6,
    log_path: Path | None = None,
    cmd_dump_path: Path | None = None,
    crf: int = 20,
    preset: str = "medium",
    audio_bitrate: str = "128k",
    maxrate: str = "12M",
    bufsize: str = "24M",
) -> None:
    """Build one big filter_complex chaining xfade + acrossfade across all scenes.

    Each xfade offset = (sum of prior durations) - i*crossfade
    where i is the boundary index (0-based among boundaries).
    """
    if len(scene_clips) != len(entries):
        raise ValueError("scene_clips and entries length mismatch")
    if not scene_clips:
        raise ValueError("no scenes to concat")

    if len(scene_clips) == 1:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(scene_clips[0]),
            "-c", "copy",
            str(out_path),
        ]
        if cmd_dump_path is not None:
            dump_cmd_script(cmd, cmd_dump_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        run_ffmpeg(cmd, log_path=log_path)
        return

    inputs: list[str] = []
    for p in scene_clips:
        inputs.extend(["-i", str(p)])

    vlabels = [f"[{i}:v:0]" for i in range(len(scene_clips))]
    alabels = [f"[{i}:a:0]" for i in range(len(scene_clips))]

    parts: list[str] = []
    prev_v = vlabels[0]
    prev_a = alabels[0]
    cumulative = entries[0].duration

    for i in range(1, len(scene_clips)):
        offset = cumulative - crossfade
        if offset < 0:
            offset = 0.0
        out_v = f"[vx{i}]"
        out_a = f"[ax{i}]"
        parts.append(
            f"{prev_v}{vlabels[i]}xfade=transition=fade:duration={crossfade:.3f}:offset={offset:.3f}{out_v}"
        )
        parts.append(
            f"{prev_a}{alabels[i]}acrossfade=d={crossfade:.3f}:c1=tri:c2=tri{out_a}"
        )
        prev_v = out_v
        prev_a = out_a
        cumulative += entries[i].duration - crossfade

    filter_complex = ";".join(parts)

    # 씬이 많으면(예: 50문항≈200씬) filter_complex 를 명령줄에 직접 넣을 때 Windows 명령줄
    # 길이 한계(~32KB)를 넘어 [WinError 206] 이 난다. 필터 그래프를 파일로 넘겨 길이를 줄인다.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fc_script = out_path.parent / f".xfade_{out_path.stem}.txt"
    fc_script.write_text(filter_complex, encoding="utf-8")

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex_script", str(fc_script),
        "-map", prev_v,
        "-map", prev_a,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", preset,
        "-crf", str(crf),
        *(["-maxrate", maxrate, "-bufsize", bufsize] if maxrate else []),
        "-c:a", "aac",
        "-b:a", audio_bitrate,
        "-ar", "48000",
        "-ac", "2",
        "-movflags", "+faststart",
        str(out_path),
    ]

    if cmd_dump_path is not None:
        dump_cmd_script(cmd, cmd_dump_path)
    try:
        run_ffmpeg(cmd, log_path=log_path)
    finally:
        try:
            fc_script.unlink()
        except OSError:
            pass


def mux_softsub(video_in: Path, srt_in: Path, out_path: Path, log_path: Path | None = None) -> None:
    """Copy video stream and add soft subtitle track (mov_text). No re-encode."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_in),
        "-i", str(srt_in),
        "-map", "0:v:0",
        "-map", "0:a:0",
        "-map", "1:0",
        "-c:v", "copy",
        "-c:a", "copy",
        "-c:s", "mov_text",
        "-metadata:s:s:0", "language=kor",
        str(out_path),
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(cmd, log_path=log_path)
