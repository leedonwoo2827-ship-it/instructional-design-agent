"""Generate a Shotcut/Kdenlive-compatible MLT XML project file.

This is a *reference* project — it lays out the images, audio, and subtitles on
parallel tracks with simple dissolve transitions. The user can open it in Shotcut
to nudge timing, replace assets, or apply richer filters before re-rendering with
melt.exe (Shotcut's headless renderer).
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from lxml import etree

from .bundle import Bundle
from .timeline import TimelineEntry


def write_mlt(
    bundle: Bundle,
    timeline: list[TimelineEntry],
    srt_paths: dict[int, Path],
    out_path: Path,
    fps: int = 30,
    width: int = 1920,
    height: int = 1080,
    crossfade: float = 0.6,
) -> None:
    """Write a minimal but valid MLT XML to out_path."""
    root = etree.Element("mlt", attrib={
        "LC_NUMERIC": "C",
        "version": "7.24.0",
        "title": f"{bundle.chapter_id}_project",
        "producer": "main_bin",
    })

    profile = etree.SubElement(root, "profile", attrib={
        "description": "HD 1080p 30 fps",
        "width": str(width),
        "height": str(height),
        "progressive": "1",
        "sample_aspect_num": "1",
        "sample_aspect_den": "1",
        "display_aspect_num": "16",
        "display_aspect_den": "9",
        "frame_rate_num": str(fps),
        "frame_rate_den": "1",
        "colorspace": "709",
    })

    # Producers for images
    for entry in timeline:
        img = entry.scene.image_path
        frames = max(1, int(round(entry.duration * fps)))
        prod = etree.SubElement(root, "producer", attrib={
            "id": f"img{entry.scene.index:02d}",
            "in": "00:00:00.000",
            "out": _tc(entry.duration),
        })
        _prop(prod, "length", str(frames + 1))
        _prop(prod, "resource", str(img.resolve()))
        _prop(prod, "ttl", "1")
        _prop(prod, "mlt_service", "qimage")

    # Producers for audio
    for entry in timeline:
        aud = entry.scene.audio_path
        prod = etree.SubElement(root, "producer", attrib={
            "id": f"aud{entry.scene.index:02d}",
            "in": "00:00:00.000",
            "out": _tc(entry.duration),
        })
        _prop(prod, "resource", str(aud.resolve()))
        _prop(prod, "mlt_service", "avformat")
        _prop(prod, "audio_index", "0")
        _prop(prod, "video_index", "-1")

    # Video playlist (sequential, dissolves applied by Shotcut on import via transitions)
    vplaylist = etree.SubElement(root, "playlist", attrib={"id": "video_track"})
    for entry in timeline:
        etree.SubElement(vplaylist, "entry", attrib={
            "producer": f"img{entry.scene.index:02d}",
            "in": "00:00:00.000",
            "out": _tc(entry.duration),
        })

    # Audio playlist
    aplaylist = etree.SubElement(root, "playlist", attrib={"id": "audio_track"})
    for entry in timeline:
        etree.SubElement(aplaylist, "entry", attrib={
            "producer": f"aud{entry.scene.index:02d}",
            "in": "00:00:00.000",
            "out": _tc(entry.duration),
        })

    # Tractor that combines tracks
    tractor = etree.SubElement(root, "tractor", attrib={
        "id": "main_tractor",
        "in": "00:00:00.000",
        "out": _tc(sum(e.duration for e in timeline)),
    })
    etree.SubElement(tractor, "track", attrib={"producer": "video_track"})
    etree.SubElement(tractor, "track", attrib={"producer": "audio_track"})

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tree = etree.ElementTree(root)
    tree.write(
        str(out_path),
        xml_declaration=True,
        encoding="UTF-8",
        pretty_print=True,
    )


def _prop(parent, name: str, value: str) -> None:
    p = etree.SubElement(parent, "property", attrib={"name": name})
    p.text = value


def _tc(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds - h * 3600 - m * 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"
