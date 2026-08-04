"""Render a single scene to an intermediate MP4 (still image + audio + burn-in subtitle + Ken Burns)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .ffmpeg_runner import run_ffmpeg, dump_cmd_script
from .kenburns import for_scene as kb_for_scene
from .timeline import TimelineEntry


@dataclass
class SceneRenderConfig:
    width: int = 1920
    height: int = 1080
    fps: int = 30
    font_name: str = "Pretendard"
    font_size: int = 16            # ASS units; libass scales to PlayResY internally
    margin_v: int = 40             # bottom margin in ASS units (=> ~bottom 5% on 1080p)
    crf: int = 20                  # YouTube 업로드용 기본(20). 작게=고화질/큰용량
    preset: str = "medium"
    audio_bitrate: str = "128k"
    maxrate: str = "12M"           # 비트레이트 상한 (YouTube 1080p 권장 ~8-12Mbps). ""=무제한
    bufsize: str = "24M"
    kenburns_mode: str = "auto"    # "auto" or "off"
    burn_subtitles: bool = True    # False 면 자막을 영상에 굽지 않는다(문제집 영상 등)


def render_scene(
    entry: TimelineEntry,
    srt_path: Path,
    out_path: Path,
    cfg: SceneRenderConfig,
    log_path: Path | None = None,
    cmd_dump_path: Path | None = None,
) -> None:
    """Render one scene to out_path. Uses zoompan + scale + subtitles burn-in.

    Filter graph:
      [0:v] scale=W*4:H*4 oversample
         -> zoompan (Ken Burns)
         -> setsar=1, fps=FPS
         -> subtitles burn-in
         -> format=yuv420p
      [0:a from audio file] -> aresample

    Output: H.264 + AAC mp4 (CRF preset).
    """
    scene = entry.scene
    duration = entry.duration
    fps = cfg.fps
    W, H = cfg.width, cfg.height
    d_frames = max(1, int(round(duration * fps)))

    kb = kb_for_scene(scene.index, duration, fps, mode=cfg.kenburns_mode)

    # Oversample factor to keep zoompan crisp at zoom 1.08.
    over = 4
    over_w, over_h = W * over, H * over

    # ffmpeg subtitles filter path needs forward slashes and an escaped colon for Windows.
    srt_for_filter = _escape_for_subtitles_filter(srt_path)

    # Alignment=2 → bottom-center. MarginV is the gap (in ASS units) from the
    # bottom of the frame to the bottom of the subtitle box, so small MarginV pushes
    # the line closer to the lower edge. BorderStyle=1 = outline+shadow (no opaque box).
    # WrapStyle=2 means "no automatic wrap; only break at \\n we insert ourselves",
    # which keeps Korean words like '소포가' from being chopped mid-word.
    style = (
        f"FontName={cfg.font_name},"
        f"FontSize={cfg.font_size},"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,"
        "BackColour=&H80000000,"
        "BorderStyle=1,"
        "Outline=2,Shadow=1,"
        "Bold=1,"
        "Alignment=2,"
        "MarginL=80,MarginR=80,"
        f"MarginV={cfg.margin_v},"
        "WrapStyle=2"
    )

    # 자막 번인 여부: burn_subtitles=False 면 subtitles 필터를 생략한다.
    def _sub(src: str) -> str:
        if cfg.burn_subtitles:
            return f"[{src}]subtitles='{srt_for_filter}':force_style='{style}'[v]"
        return f"[{src}]null[v]"

    clip_path = getattr(scene, "clip_path", None)
    if clip_path is not None:
        # 하이브리드: ComfyUI img2video 클립을 나레이션 길이만큼 loop (Ken Burns 생략).
        # 짧은 클립(~2-5s)을 -stream_loop 로 반복하고 -t 로 나레이션 길이에 맞춘다.
        filter_v = (
            f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps}[scaled];"
            + _sub("scaled")
        )
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", str(clip_path),
            "-i", str(scene.audio_path),
            "-filter_complex", filter_v,
            "-map", "[v]",
            "-map", "1:a:0",
            "-t", f"{duration:.3f}",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", cfg.preset,
            "-crf", str(cfg.crf),
            *(["-maxrate", cfg.maxrate, "-bufsize", cfg.bufsize] if cfg.maxrate else []),
            "-r", str(fps),
            "-c:a", "aac",
            "-b:a", cfg.audio_bitrate,
            "-ar", "48000",
            "-ac", "2",
            "-movflags", "+faststart",
            str(out_path),
        ]
    else:
        filter_v = (
            # scale image to oversampled canvas, preserving aspect with letterbox pad
            f"[0:v]scale={over_w}:{over_h}:force_original_aspect_ratio=decrease,"
            f"pad={over_w}:{over_h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1[over];"
            # zoompan back down to target — produces fps frames over duration
            f"[over]zoompan=z='{kb.z_expr}':x='{kb.x_expr}':y='{kb.y_expr}':"
            f"d={d_frames}:fps={fps}:s={W}x{H}[zoomed];"
            + _sub("zoomed")
        )
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-framerate", str(fps),
            "-i", str(scene.image_path),
            "-i", str(scene.audio_path),
            "-filter_complex", filter_v,
            "-map", "[v]",
            "-map", "1:a:0",
            "-t", f"{duration:.3f}",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", cfg.preset,
            "-crf", str(cfg.crf),
            *(["-maxrate", cfg.maxrate, "-bufsize", cfg.bufsize] if cfg.maxrate else []),
            "-r", str(fps),
            "-c:a", "aac",
            "-b:a", cfg.audio_bitrate,
            "-ar", "48000",
            "-ac", "2",
            "-movflags", "+faststart",
            str(out_path),
        ]

    if cmd_dump_path is not None:
        dump_cmd_script(cmd, cmd_dump_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(cmd, log_path=log_path)


def _escape_for_subtitles_filter(p: Path) -> str:
    """ffmpeg subtitles filter needs path quoting + colon escape on Windows."""
    s = str(p.resolve()).replace("\\", "/")
    # On Windows, the drive colon must be escaped from ffmpeg filter parser.
    if len(s) > 1 and s[1] == ":":
        s = s[0] + r"\:" + s[2:]
    # Escape single quotes embedded in path (rare)
    return s.replace("'", r"\'")
