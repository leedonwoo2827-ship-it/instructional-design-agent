"""Build the per-scene timeline using actual audio durations from ffprobe."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .bundle import Bundle, Scene
from .ffmpeg_runner import ffprobe_duration


@dataclass
class TimelineEntry:
    scene: Scene
    duration: float        # actual wav length in seconds
    timeline_start: float  # start position on the concatenated timeline (accounts for crossfade overlap)
    timeline_end: float    # = timeline_start + duration


def build_timeline(bundle: Bundle, crossfade: float) -> list[TimelineEntry]:
    """Measure each scene's audio length and compute timeline offsets.

    On the final concatenated video, scene N starts at:
        sum(scene[0..N-1].duration) - N * crossfade
    because each xfade boundary overlaps `crossfade` seconds.
    """
    entries: list[TimelineEntry] = []
    cumulative_audio = 0.0
    for n, scene in enumerate(bundle.scenes):
        dur = ffprobe_duration(scene.audio_path)
        start = cumulative_audio - n * crossfade
        if start < 0:
            start = 0.0
        entries.append(TimelineEntry(
            scene=scene,
            duration=dur,
            timeline_start=start,
            timeline_end=start + dur,
        ))
        cumulative_audio += dur
    return entries


def total_output_duration(entries: list[TimelineEntry], crossfade: float) -> float:
    if not entries:
        return 0.0
    n = len(entries)
    raw = sum(e.duration for e in entries)
    return max(0.0, raw - (n - 1) * crossfade)
