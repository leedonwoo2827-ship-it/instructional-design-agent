"""Write render_report.json summarizing scene timings, files, warnings."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from .bundle import Bundle
from .timeline import TimelineEntry


@dataclass
class SceneRecord:
    scene: int
    title: str
    duration_seconds: float
    timeline_start: float
    timeline_end: float
    image: str
    audio: str
    subtitle: str
    render_seconds: Optional[float] = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class RenderReport:
    chapter: int
    chapter_id: str
    title: str
    bundle_root: str
    output_video: str
    output_softsub: Optional[str]
    output_mlt: Optional[str]
    output_srt: Optional[str]
    fps: int
    resolution: str
    crossfade: float
    kenburns: str
    font: str
    total_output_seconds: float
    expected_output_seconds: float
    scenes: list[SceneRecord]
    bundle_warnings: list[str]
    total_render_seconds: float


def build_report(
    bundle: Bundle,
    timeline: list[TimelineEntry],
    scene_srts: dict[int, Path],
    render_times: dict[int, float],
    output_video: Path,
    output_softsub: Optional[Path],
    output_mlt: Optional[Path],
    output_srt: Optional[Path],
    fps: int,
    width: int,
    height: int,
    crossfade: float,
    kenburns_mode: str,
    font_name: str,
    total_render_seconds: float,
) -> RenderReport:
    scenes: list[SceneRecord] = []
    for entry in timeline:
        sc = entry.scene
        scenes.append(SceneRecord(
            scene=sc.index,
            title=sc.title,
            duration_seconds=round(entry.duration, 3),
            timeline_start=round(entry.timeline_start, 3),
            timeline_end=round(entry.timeline_end, 3),
            image=str(sc.image_path) if sc.image_path else "",
            audio=str(sc.audio_path) if sc.audio_path else "",
            subtitle=str(scene_srts.get(sc.index, "")),
            render_seconds=round(render_times.get(sc.index, 0.0), 2) if sc.index in render_times else None,
            warnings=list(sc.warnings),
        ))
    expected = sum(e.duration for e in timeline) - max(0, len(timeline) - 1) * crossfade
    return RenderReport(
        chapter=bundle.chapter,
        chapter_id=bundle.chapter_id,
        title=bundle.title,
        bundle_root=str(bundle.root),
        output_video=str(output_video),
        output_softsub=str(output_softsub) if output_softsub else None,
        output_mlt=str(output_mlt) if output_mlt else None,
        output_srt=str(output_srt) if output_srt else None,
        fps=fps,
        resolution=f"{width}x{height}",
        crossfade=crossfade,
        kenburns=kenburns_mode,
        font=font_name,
        total_output_seconds=round(max(0.0, expected), 3),
        expected_output_seconds=round(max(0.0, expected), 3),
        scenes=scenes,
        bundle_warnings=list(bundle.warnings),
        total_render_seconds=round(total_render_seconds, 2),
    )


def write_report(report: RenderReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
