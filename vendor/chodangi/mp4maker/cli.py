"""Command-line entry point for mp4maker.

Usage:
    python -m mp4maker <bundle_dir> [options]
    python -m mp4maker --probe                  # environment check only
    python -m mp4maker --dry-run <bundle_dir>   # validate + plan, no ffmpeg calls
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from . import __version__
from .bundle import load_bundle
from .concat import concat_with_crossfade, mux_softsub
from .ffmpeg_runner import FFmpegError, probe_binary, require_binaries
from .fonts import find_font, probe as probe_font
from .mlt import write_mlt
from .render_scene import SceneRenderConfig, render_scene
from .report import build_report, write_report
from .subtitles import copy_combined_for_softsub, write_scene_srts
from .timeline import build_timeline, total_output_duration


def main(argv: list[str] | None = None) -> int:
    # Windows console defaults to cp949 in ko-KR; force UTF-8 so Korean titles and em-dashes print.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.probe:
        return _cmd_probe()

    if args.bundle is None:
        parser.error("bundle directory is required (or use --probe)")

    bundle_dir = Path(args.bundle).resolve()

    try:
        return _cmd_render(args, bundle_dir)
    except FFmpegError as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        if e.stderr:
            print(f"\n--- ffmpeg stderr ---\n{e.stderr[-2000:]}", file=sys.stderr)
        return 2
    except (FileNotFoundError, ValueError) as e:
        print(f"\n[ERROR] {e}", file=sys.stderr)
        return 2


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m mp4maker",
        description="Build a 1080p MP4 from a chNN_bundle (script/images/audio/subtitles).",
    )
    p.add_argument("bundle", nargs="?", help="path to chNN_bundle directory")
    p.add_argument("--probe", action="store_true", help="check ffmpeg/fonts/python and exit")
    p.add_argument("--dry-run", action="store_true", help="validate + print plan, no ffmpeg calls")
    p.add_argument("--resolution", default="1920x1080", help="WxH, default 1920x1080")
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--crossfade", type=float, default=0.6, help="scene-boundary crossfade seconds")
    p.add_argument("--kenburns", choices=["auto", "off"], default="auto")
    p.add_argument("--no-subs", action="store_true",
                   help="자막을 영상에 굽지 않는다(하드번인 생략). 문제집/슬라이드 영상용")
    p.add_argument("--no-soft-sub", action="store_true", help="skip softsub mp4")
    p.add_argument("--no-mlt", action="store_true", help="skip MLT XML")
    p.add_argument("--keep-work", action="store_true", help="keep _work/ temp folder")
    p.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 4) - 1),
                   help="parallel scene renders (default: cpu-1)")
    p.add_argument("--only", default="", help="render only listed scenes (e.g. '1' or '1,3,5')")
    p.add_argument("--font-size", type=int, default=14,
                   help="subtitle font size in ASS units (default 14; 약간 작게 해 한 줄에 더 많이)")
    p.add_argument("--margin-v", type=int, default=40,
                   help="distance in ASS units from subtitle baseline to bottom of frame (default 40)")
    p.add_argument("--no-split-subs", action="store_true",
                   help="keep long SRT cues as-is instead of breaking into sentences")
    p.add_argument("--max-cue-seconds", type=float, default=7.0,
                   help="when splitting, max seconds per cue (default 7.0)")
    p.add_argument("--wrap-chars", type=int, default=50,
                   help="한 줄 최대 글자수(이보다 길면 줄바꿈). 기본 50 — 둘째 줄로 잘 안 넘어감 (0=비활성)")
    # 인코딩 품질/용량 (YouTube 업로드 친화 기본값)
    p.add_argument("--crf", type=int, default=20,
                   help="H.264 CRF: 작을수록 고화질·큰 용량 (기본 20; 18=고화질, 23=작은 용량)")
    p.add_argument("--preset", default="medium", help="x264 preset (기본 medium)")
    p.add_argument("--audio-bitrate", default="128k", help="AAC 오디오 비트레이트 (기본 128k)")
    p.add_argument("--maxrate", default="12M",
                   help="비디오 비트레이트 상한, '' 이면 무제한 (기본 12M ≈ YouTube 1080p 권장)")
    p.add_argument("--bufsize", default="24M", help="비트레이트 버퍼 (기본 24M)")
    p.add_argument("--version", action="version", version=f"mp4maker {__version__}")
    return p


def _cmd_probe() -> int:
    print(f"mp4maker {__version__}")
    print(f"python: {sys.version.split()[0]}")
    ffm = probe_binary("ffmpeg") or "(NOT FOUND on PATH — install via: winget install Gyan.FFmpeg)"
    ffp = probe_binary("ffprobe") or "(NOT FOUND on PATH)"
    print(f"ffmpeg:  {ffm}")
    print(f"ffprobe: {ffp}")
    print(probe_font())
    try:
        import pysrt  # noqa: F401
        print("pysrt:   installed")
    except ImportError:
        print("pysrt:   (NOT INSTALLED — pip install -r requirements.txt)")
    try:
        import lxml  # noqa: F401
        print("lxml:    installed")
    except ImportError:
        print("lxml:    (NOT INSTALLED — pip install -r requirements.txt)")
    return 0


def _parse_resolution(s: str) -> tuple[int, int]:
    try:
        w, h = s.lower().split("x")
        return int(w), int(h)
    except Exception as e:
        raise ValueError(f"invalid --resolution {s!r}, expected WxH") from e


def _parse_only(s: str) -> set[int] | None:
    if not s.strip():
        return None
    out: set[int] = set()
    for piece in s.split(","):
        piece = piece.strip()
        if not piece:
            continue
        out.add(int(piece))
    return out


def _cmd_render(args, bundle_dir: Path) -> int:
    width, height = _parse_resolution(args.resolution)
    only = _parse_only(args.only)

    print(f"[bundle] loading: {bundle_dir}")
    bundle = load_bundle(bundle_dir)
    print(f"[bundle] {bundle.chapter_id} '{bundle.title}'  scenes={len(bundle.scenes)}")
    for w in bundle.warnings:
        print(f"[warn]  {w}")

    if not args.dry_run:
        require_binaries()

    font_name, font_path = find_font()
    print(f"[font]  {font_name}" + (f"  ({font_path})" if font_path else "  (system default)"))

    print(f"[probe] measuring audio durations via ffprobe...")
    if args.dry_run:
        timeline = _dry_run_timeline(bundle, args.crossfade)
    else:
        timeline = build_timeline(bundle, args.crossfade)

    if only is not None:
        timeline = [e for e in timeline if e.scene.index in only]
        if not timeline:
            print(f"[error] --only {sorted(only)} matched no scenes")
            return 2
        print(f"[only]  rendering scenes: {[e.scene.index for e in timeline]}")

    expected_total = total_output_duration(timeline, args.crossfade)
    print(f"[plan]  scenes={len(timeline)}  expected final length={expected_total:.1f}s")
    for e in timeline:
        print(f"  sc{e.scene.index:02d}  {e.duration:6.2f}s  {e.scene.title}")

    if args.dry_run:
        print("\n[dry-run] no ffmpeg calls; exiting.")
        return 0

    bundle.draft_dir.mkdir(parents=True, exist_ok=True)
    bundle.work_dir.mkdir(parents=True, exist_ok=True)

    split_mode = "off" if args.no_split_subs else f"auto (<= {args.max_cue_seconds:.1f}s/cue)"
    print(f"[subs]  writing per-scene SRTs to {bundle.work_dir}  split={split_mode}", flush=True)
    scene_srts = write_scene_srts(
        bundle, timeline, bundle.work_dir,
        split_long_cues=not args.no_split_subs,
        max_cue_duration=args.max_cue_seconds,
        wrap_chars=args.wrap_chars,
    )

    cfg = SceneRenderConfig(
        width=width,
        height=height,
        fps=args.fps,
        font_name=font_name,
        font_size=args.font_size,
        margin_v=args.margin_v,
        crf=args.crf,
        preset=args.preset,
        audio_bitrate=args.audio_bitrate,
        maxrate=args.maxrate,
        bufsize=args.bufsize,
        kenburns_mode=args.kenburns,
        burn_subtitles=not args.no_subs,
    )

    total_scenes = len(timeline)
    print(f"[render] {total_scenes} scenes  jobs={args.jobs}  res={width}x{height}@{args.fps}fps", flush=True)
    t_start = time.time()
    render_times: dict[int, float] = {}
    scene_outs: dict[int, Path] = {}

    tasks = []
    for entry in timeline:
        out = bundle.work_dir / f"sc{entry.scene.index:02d}.mp4"
        srt = scene_srts[entry.scene.index]
        log = bundle.work_dir / f"ffmpeg_sc{entry.scene.index:02d}.log"
        cmd_dump = bundle.work_dir / f"ffmpeg_sc{entry.scene.index:02d}.cmd"
        tasks.append((entry, srt, out, cfg, log, cmd_dump))
        scene_outs[entry.scene.index] = out

    completed = 0
    if args.jobs <= 1 or len(tasks) == 1:
        for (entry, srt, out, cfg_, log, cmd_dump) in tasks:
            print(f"[scene] sc{entry.scene.index:02d} start  ({entry.duration:.1f}s)", flush=True)
            t0 = time.time()
            render_scene(entry, srt, out, cfg_, log_path=log, cmd_dump_path=cmd_dump)
            render_times[entry.scene.index] = time.time() - t0
            completed += 1
            print(
                f"[scene] sc{entry.scene.index:02d} done  ({render_times[entry.scene.index]:.1f}s)  "
                f"progress={completed}/{total_scenes}",
                flush=True,
            )
    else:
        with ProcessPoolExecutor(max_workers=args.jobs) as ex:
            fut_to_idx = {}
            for (entry, srt, out, cfg_, log, cmd_dump) in tasks:
                fut = ex.submit(_render_scene_worker, entry, srt, out, cfg_, log, cmd_dump)
                fut_to_idx[fut] = (entry.scene.index, time.time())
            for fut in as_completed(fut_to_idx):
                idx, t0 = fut_to_idx[fut]
                fut.result()
                render_times[idx] = time.time() - t0
                completed += 1
                print(
                    f"[scene] sc{idx:02d} done  ({render_times[idx]:.1f}s)  "
                    f"progress={completed}/{total_scenes}",
                    flush=True,
                )

    print(f"[stage] concat  crossfade={args.crossfade}s", flush=True)
    final_mp4 = bundle.draft_dir / f"{bundle.chapter_id}_final.mp4"
    ordered_clips = [scene_outs[e.scene.index] for e in timeline]
    concat_with_crossfade(
        ordered_clips,
        timeline,
        final_mp4,
        crossfade=args.crossfade,
        log_path=bundle.work_dir / "ffmpeg_concat.log",
        cmd_dump_path=bundle.work_dir / "ffmpeg_concat.cmd",
        crf=args.crf,
        preset=args.preset,
        audio_bitrate=args.audio_bitrate,
        maxrate=args.maxrate,
        bufsize=args.bufsize,
    )
    print(f"[done]  {final_mp4}")

    # SRT side-car (copy combined if present, normalized to UTF-8 no BOM)
    side_srt: Path | None = None
    if bundle.combined_srt_path:
        side_srt = bundle.draft_dir / f"{bundle.chapter_id}.srt"
        copy_combined_for_softsub(bundle, side_srt)
        print(f"[done]  {side_srt}")

    softsub_mp4: Path | None = None
    if not args.no_soft_sub and side_srt is not None:
        print("[stage] softsub", flush=True)
        softsub_mp4 = bundle.draft_dir / f"{bundle.chapter_id}_final_softsub.mp4"
        mux_softsub(final_mp4, side_srt, softsub_mp4, log_path=bundle.work_dir / "ffmpeg_softsub.log")
        print(f"[done]  {softsub_mp4}", flush=True)

    mlt_path: Path | None = None
    if not args.no_mlt:
        print("[stage] mlt", flush=True)
        mlt_path = bundle.draft_dir / f"{bundle.chapter_id}_project.mlt"
        write_mlt(bundle, timeline, scene_srts, mlt_path, fps=args.fps,
                  width=width, height=height, crossfade=args.crossfade)
        print(f"[done]  {mlt_path}", flush=True)

    total_render = time.time() - t_start
    report = build_report(
        bundle=bundle,
        timeline=timeline,
        scene_srts=scene_srts,
        render_times=render_times,
        output_video=final_mp4,
        output_softsub=softsub_mp4,
        output_mlt=mlt_path,
        output_srt=side_srt,
        fps=args.fps,
        width=width,
        height=height,
        crossfade=args.crossfade,
        kenburns_mode=args.kenburns,
        font_name=font_name,
        total_render_seconds=total_render,
    )
    report_path = bundle.draft_dir / "render_report.json"
    write_report(report, report_path)
    print(f"[done]  {report_path}")

    if not args.keep_work and only is None:
        shutil.rmtree(bundle.work_dir, ignore_errors=True)
        print(f"[clean] removed {bundle.work_dir}")

    print(f"\n[total] {total_render:.1f}s")
    return 0


def _render_scene_worker(entry, srt, out, cfg_, log, cmd_dump):
    """ProcessPool target: must be importable / top-level."""
    render_scene(entry, srt, out, cfg_, log_path=log, cmd_dump_path=cmd_dump)


def _dry_run_timeline(bundle, crossfade: float):
    """Build a fake timeline using narration_seconds_hint, no ffprobe."""
    from .timeline import TimelineEntry
    entries: list[TimelineEntry] = []
    cumulative = 0.0
    for n, sc in enumerate(bundle.scenes):
        dur = sc.narration_seconds_hint or 5.0
        start = max(0.0, cumulative - n * crossfade)
        entries.append(TimelineEntry(scene=sc, duration=dur, timeline_start=start, timeline_end=start + dur))
        cumulative += dur
    return entries
