"""Align per-scene SRT files.

If a scene has its own chNN_XX_narration.srt, use it directly (re-timed to start at 0).
Otherwise, extract its slice from the combined chNN.srt by character-proportional split,
following the SceneWeaver-CapCut convention.
"""
from __future__ import annotations

import re
from pathlib import Path

import pysrt

from .bundle import Bundle, Scene
from .timeline import TimelineEntry


_PUNCT = re.compile(r"(?<=[\.\?\!。！？])\s+")
_MAX_CUE = 7.0
_MIN_CUE = 1.5
_MAX_LINE_CHARS = 30   # one-line guideline; longer lines wrap on ffmpeg side


def write_scene_srts(
    bundle: Bundle,
    timeline: list[TimelineEntry],
    work_dir: Path,
    split_long_cues: bool = True,
    max_cue_duration: float = 5.0,
    wrap_chars: int = 25,
) -> dict[int, Path]:
    """For every scene, write `work_dir / scNN.srt` with cues starting at 00:00:00.

    If split_long_cues=True, any cue longer than max_cue_duration seconds is broken
    into sentence-level sub-cues with proportional timing so subtitles change with
    the narration instead of one giant block sitting on screen.

    Returns: {scene_index: path_to_srt}
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    out: dict[int, Path] = {}

    combined = _load_combined(bundle.combined_srt_path) if bundle.combined_srt_path else None

    for entry in timeline:
        scene = entry.scene
        if scene.subtitle_path and scene.subtitle_path.exists():
            cues = _load_srt(scene.subtitle_path)
            cues = _rebase_to_zero(cues, scene_duration=entry.duration)
        elif combined is not None:
            cues = _slice_from_combined(
                combined,
                scene_idx=scene.index,
                total_scenes=len(timeline),
                scene_duration=entry.duration,
            )
        else:
            cues = _from_narration_text(scene.narration_text, entry.duration)

        if split_long_cues:
            cues = _split_long_cues(cues, max_cue_duration=max_cue_duration)

        if wrap_chars and wrap_chars > 0:
            cues = _split_to_one_line_cues(cues, max_line_chars=wrap_chars)

        path = work_dir / f"sc{scene.index:02d}.srt"
        _write_srt(cues, path)
        out[scene.index] = path

    return out


def _split_to_one_line_cues(
    cues: list[pysrt.SubRipItem],
    max_line_chars: int,
) -> list[pysrt.SubRipItem]:
    """Break each cue that would wrap into multiple lines into separate cues.

    Strategy: greedy fill by 어절 (whitespace-separated). Each resulting line
    becomes its own SRT cue, with the parent cue's duration split proportionally
    to line character count. This guarantees one-line-at-a-time playback and
    keeps Korean particles attached to their noun (e.g. '소포가' stays together).
    """
    out: list[pysrt.SubRipItem] = []
    next_idx = 1
    for cue in cues:
        text = (cue.text or "").strip()
        if not text:
            continue

        start_ms = _to_ms(cue.start)
        end_ms = _to_ms(cue.end)
        dur_ms = max(0, end_ms - start_ms)

        lines = _wrap_into_lines(text, max_line_chars)
        if len(lines) <= 1:
            out.append(pysrt.SubRipItem(
                index=next_idx,
                start=_from_ms(start_ms),
                end=_from_ms(end_ms),
                text=lines[0] if lines else text,
            ))
            next_idx += 1
            continue

        total_chars = sum(len(line) for line in lines) or 1
        cursor_ms = start_ms
        last_i = len(lines) - 1
        for i, line in enumerate(lines):
            share = len(line) / total_chars
            sub_dur = int(dur_ms * share)
            sub_dur = max(int(_MIN_CUE * 1000), sub_dur)
            sub_start = cursor_ms
            sub_end = min(end_ms, sub_start + sub_dur)
            if i == last_i:
                sub_end = end_ms
            if sub_end <= sub_start:
                sub_end = min(end_ms, sub_start + int(_MIN_CUE * 1000))
            out.append(pysrt.SubRipItem(
                index=next_idx,
                start=_from_ms(sub_start),
                end=_from_ms(sub_end),
                text=line,
            ))
            cursor_ms = sub_end
            next_idx += 1
    return out


def _wrap_into_lines(text: str, max_line_chars: int) -> list[str]:
    """Greedy word-boundary wrap returning a list of lines, none longer than max_line_chars."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_line_chars:
        return [text]
    words = text.split()
    if not words:
        return [text]
    lines: list[str] = []
    cur = ""
    for w in words:
        cand = f"{cur} {w}".strip() if cur else w
        if len(cand) <= max_line_chars:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [text]


def _split_long_cues(
    cues: list[pysrt.SubRipItem],
    max_cue_duration: float = 5.0,
) -> list[pysrt.SubRipItem]:
    """Break any cue longer than max_cue_duration into sentence-level sub-cues.

    Sentences split on . ? ! 。 ！ ？. Sub-cue durations are proportional to
    character counts, clamped to [_MIN_CUE, _MAX_CUE].
    """
    out: list[pysrt.SubRipItem] = []
    next_idx = 1
    max_ms = int(max_cue_duration * 1000)

    for cue in cues:
        text = (cue.text or "").strip()
        if not text:
            continue
        start_ms = _to_ms(cue.start)
        end_ms = _to_ms(cue.end)
        dur_ms = max(0, end_ms - start_ms)

        if dur_ms <= max_ms:
            out.append(pysrt.SubRipItem(
                index=next_idx,
                start=_from_ms(start_ms),
                end=_from_ms(end_ms),
                text=text,
            ))
            next_idx += 1
            continue

        parts = [p.strip() for p in _PUNCT.split(text) if p.strip()]
        if len(parts) <= 1:
            parts = _split_by_length(text, target_chars=24)
        if not parts:
            parts = [text]

        total_chars = sum(len(p) for p in parts) or 1
        cursor_ms = start_ms
        last_i = len(parts) - 1
        for i, part in enumerate(parts):
            share = len(part) / total_chars
            sub_dur = int(dur_ms * share)
            sub_dur = max(int(_MIN_CUE * 1000), min(int(_MAX_CUE * 1000), sub_dur))
            sub_start = cursor_ms
            sub_end = min(end_ms, sub_start + sub_dur)
            if i == last_i:
                sub_end = end_ms
            if sub_end <= sub_start:
                sub_end = sub_start + int(_MIN_CUE * 1000)
            out.append(pysrt.SubRipItem(
                index=next_idx,
                start=_from_ms(sub_start),
                end=_from_ms(sub_end),
                text=part,
            ))
            next_idx += 1
            cursor_ms = sub_end

    return out


def _split_by_length(text: str, target_chars: int = 24) -> list[str]:
    """When sentence punctuation is absent, split on Korean clausal commas / spaces."""
    if len(text) <= target_chars:
        return [text]
    # Prefer splitting on Korean conjunctions and commas.
    soft = re.split(r"(?<=[,，])\s+|(?<=\s)(?=그러나|하지만|그리고|그러므로|그래서)", text)
    soft = [s.strip() for s in soft if s.strip()]
    if all(len(s) <= target_chars * 1.5 for s in soft) and len(soft) > 1:
        return soft
    # Fallback: hard wrap by character count at word/space boundary.
    out: list[str] = []
    buf = ""
    for token in re.findall(r"\S+\s*", text):
        if len(buf) + len(token) > target_chars and buf:
            out.append(buf.strip())
            buf = token
        else:
            buf += token
    if buf.strip():
        out.append(buf.strip())
    return out or [text]


def _load_srt(path: Path) -> list[pysrt.SubRipItem]:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    subs = pysrt.from_string(text)
    return list(subs)


def _load_combined(path: Path) -> list[pysrt.SubRipItem]:
    return _load_srt(path)


def _rebase_to_zero(cues: list[pysrt.SubRipItem], scene_duration: float) -> list[pysrt.SubRipItem]:
    """Shift first cue start to 00:00:00 and cap last cue end at scene_duration."""
    if not cues:
        return cues
    first_start_ms = _to_ms(cues[0].start)
    rebased: list[pysrt.SubRipItem] = []
    for i, c in enumerate(cues, start=1):
        start_ms = max(0, _to_ms(c.start) - first_start_ms)
        end_ms = max(start_ms + int(_MIN_CUE * 1000), _to_ms(c.end) - first_start_ms)
        end_ms = min(end_ms, int(scene_duration * 1000))
        if end_ms <= start_ms:
            end_ms = min(int(scene_duration * 1000), start_ms + int(_MIN_CUE * 1000))
        rebased.append(pysrt.SubRipItem(
            index=i,
            start=_from_ms(start_ms),
            end=_from_ms(end_ms),
            text=c.text,
        ))
    return rebased


def _slice_from_combined(
    cues: list[pysrt.SubRipItem],
    scene_idx: int,
    total_scenes: int,
    scene_duration: float,
) -> list[pysrt.SubRipItem]:
    """Pick the cue with matching index, or the Nth block. Combined SRT has one block per scene."""
    if 1 <= scene_idx <= len(cues):
        c = cues[scene_idx - 1]
        end_ms = min(int(scene_duration * 1000), _to_ms(c.end) - _to_ms(c.start))
        if end_ms < int(_MIN_CUE * 1000):
            end_ms = int(scene_duration * 1000)
        return [pysrt.SubRipItem(
            index=1,
            start=_from_ms(0),
            end=_from_ms(end_ms),
            text=c.text,
        )]
    return []


def _from_narration_text(text: str, scene_duration: float) -> list[pysrt.SubRipItem]:
    """Fallback: split narration into sentences proportional to char counts."""
    if not text.strip():
        return []
    parts = [p.strip() for p in _PUNCT.split(text) if p.strip()]
    if not parts:
        parts = [text.strip()]
    total_chars = sum(len(p) for p in parts) or 1
    cues: list[pysrt.SubRipItem] = []
    cursor_ms = 0
    end_cap_ms = int(scene_duration * 1000)
    for i, p in enumerate(parts, start=1):
        share = len(p) / total_chars
        dur_ms = int(scene_duration * 1000 * share)
        dur_ms = max(int(_MIN_CUE * 1000), min(int(_MAX_CUE * 1000), dur_ms))
        start_ms = cursor_ms
        end_ms = min(end_cap_ms, start_ms + dur_ms)
        if i == len(parts):
            end_ms = end_cap_ms
        cues.append(pysrt.SubRipItem(
            index=i,
            start=_from_ms(start_ms),
            end=_from_ms(end_ms),
            text=p,
        ))
        cursor_ms = end_ms
    return cues


def _write_srt(cues: list[pysrt.SubRipItem], path: Path) -> None:
    if not cues:
        path.write_text("", encoding="utf-8")
        return
    subs = pysrt.SubRipFile(items=cues)
    subs.save(str(path), encoding="utf-8")


def _to_ms(t: pysrt.SubRipTime) -> int:
    return ((t.hours * 60 + t.minutes) * 60 + t.seconds) * 1000 + t.milliseconds


def _from_ms(ms: int) -> pysrt.SubRipTime:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return pysrt.SubRipTime(hours=h, minutes=m, seconds=s, milliseconds=ms)


def copy_combined_for_softsub(bundle: Bundle, dest: Path) -> Path | None:
    """Copy the combined SRT to draft/ for soft-sub muxing and SRT side-car."""
    if bundle.combined_srt_path is None:
        return None
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        bundle.combined_srt_path.read_text(encoding="utf-8-sig", errors="replace"),
        encoding="utf-8",
    )
    return dest
