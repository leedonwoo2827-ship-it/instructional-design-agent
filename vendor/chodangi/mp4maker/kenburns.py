"""Deterministic Ken Burns parameters per scene index.

ffmpeg zoompan filter operates on an oversampled image. We render at 2x resolution
upstream (scale=4000:-1 etc.) to avoid pixelation, then zoompan + scale to target.
The 'd' (duration in input frames) is set to scene_duration * fps so the move
spans the entire scene exactly.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class KenBurnsParams:
    z_expr: str    # zoom expression as a function of `on` (current output frame index)
    x_expr: str
    y_expr: str


# Map scene_index % 4 -> motion variant.
# zoompan's `on` is the OUTPUT frame index (0 .. d-1). We normalize to t = on/(d-1).
# zoom_start->zoom_end:
#   t goes 0..1, zoom = start + (end-start) * t
# To keep movement subtle and not jittery, all zooms stay <= 1.08.

def for_scene(scene_index: int, duration: float, fps: int, mode: str = "auto") -> KenBurnsParams:
    """Pick deterministic motion based on scene index.

    mode = "off" disables motion (still uses zoompan with z=1.0 for consistent sizing).
    """
    if mode == "off":
        return KenBurnsParams(z_expr="1.0", x_expr="iw/2-(iw/zoom/2)", y_expr="ih/2-(ih/zoom/2)")

    d_frames = max(1, int(round(duration * fps)))
    variant = (scene_index - 1) % 4  # 1-based scene index

    # All zoom expressions are linear ramps over the duration.
    # `on` = current output frame in 0..(d-1)
    t = f"(on/{d_frames - 1 if d_frames > 1 else 1})"

    if variant == 0:
        # zoom-in 1.00 -> 1.08, centered
        z = f"(1.00+0.08*{t})"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif variant == 1:
        # zoom-out 1.08 -> 1.00, centered
        z = f"(1.08-0.08*{t})"
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
    elif variant == 2:
        # pan-right at zoom 1.04 — start x at left bias, end at right bias
        z = "1.04"
        # x ranges from 0 to (iw - iw/zoom). At z=1.04 the visible width is iw/1.04 ≈ 0.96*iw.
        # So x can range 0 .. 0.04*iw. Use linear ramp.
        x = f"((iw-iw/zoom)*{t})"
        y = "ih/2-(ih/zoom/2)"
    else:
        # pan-left at zoom 1.04 — start x at right bias, end at left bias
        z = "1.04"
        x = f"((iw-iw/zoom)*(1-{t}))"
        y = "ih/2-(ih/zoom/2)"

    return KenBurnsParams(z_expr=z, x_expr=x, y_expr=y)
