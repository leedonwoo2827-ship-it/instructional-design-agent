# -*- coding: utf-8 -*-
r"""렌더 결과 검증 — 소요 시간 표 + 크로스페이드 정렬.

    .venv\Scripts\python scripts\verify_render.py "<06_영상 폴더>"

크로스페이드가 어긋나면 씬마다 오차가 **누적**된다. 82씬이면 최대 48초까지
밀릴 수 있어서, 앞부분만 보면 멀쩡해 보이고 뒤로 갈수록 벌어진다.
그래서 **후반부 씬의 화면 시작과 음성/자막 시작**을 따로 확인한다.

식:  씬 N 시작 = sum(dur[0..N-1]) - N x crossfade
     총 길이   = sum(dur) - (N-1) x crossfade
"""
from __future__ import annotations

import glob
import io
import json
import os
import re
import subprocess
import sys
from pathlib import Path

CROSSFADE = 0.6
CPS = 7.53          # 실측 발화 속도(자/초)
# 시작과 **끝**을 모두 잡는다. 끝을 안 보면 마지막 큐의 시작을 총 길이와 비교해
# 멀쩡한 영상을 DRIFT 로 오판한다(실제로 그렇게 오판했다 — 3.2초는 마지막 자막의
# 표시 시간이었다).
_TS = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
                 r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")

out = io.open(sys.stdout.fileno(), "w", encoding="utf-8", closefd=False)


def dur(path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    try:
        return float((r.stdout or "0").strip())
    except ValueError:
        return 0.0


def first_cue(path: Path) -> float | None:
    if not path.is_file():
        return None
    m = _TS.search(path.read_text(encoding="utf-8", errors="replace"))
    if not m:
        return None
    h, mi, s, ms = m.groups()[:4]
    return int(h) * 3600 + int(mi) * 60 + int(s) + int(ms) / 1000


def hhmmss(iso: str) -> str:
    return iso[-8:] if iso else "-"


def main() -> int:
    V = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    pr = json.loads((V / "progress.json").read_text(encoding="utf-8"))

    # ── 1) 시간 표 ──
    log = (V / "engine.log").read_text(encoding="utf-8", errors="replace")
    out.write("== 소요 시간 ==\n")
    rows = [("시작", hhmmss(pr.get("started_at", ""))),
            ("완료", hhmmss(pr.get("finished_at", "")) or "진행 중"),
            ("마지막 갱신", hhmmss(pr.get("updated_at", "")))]
    for label, v in rows:
        out.write(f"  {label:12s} {v}\n")
    if pr.get("started_at") and pr.get("finished_at"):
        from datetime import datetime
        a = datetime.fromisoformat(pr["started_at"])
        b = datetime.fromisoformat(pr["finished_at"])
        out.write(f"  {'총 소요':12s} {(b - a).total_seconds()/60:.0f}분\n")
    out.write(f"  {'오류':12s} {pr.get('error') or '없음'}\n")

    # ── 2) 크로스페이드 검산 ──
    wavs = sorted(glob.glob(str(V / "번들" / "ch*" / "audio" / "*_narration.wav")))
    mp4s = sorted(glob.glob(str(V / "완성" / "*.mp4")))
    if not wavs:
        out.write("\n[중단] wav 가 없습니다.\n")
        return 1
    durs = [dur(w) for w in wavs]
    n = len(durs)
    wsum = sum(durs)
    expect = wsum - (n - 1) * CROSSFADE
    out.write(f"\n== 크로스페이드 ({n}씬) ==\n")
    out.write(f"  wav 합계     {wsum:9.2f}s  ({wsum/60:.1f}분)\n")
    out.write(f"  - {n-1} x {CROSSFADE}   {(n-1)*CROSSFADE:9.2f}s\n")
    out.write(f"  = 예상       {expect:9.2f}s  ({expect/60:.1f}분)\n")
    if mp4s:
        actual = dur(mp4s[-1])
        err = abs(actual - expect)
        out.write(f"  실제 영상    {actual:9.2f}s  ({actual/60:.1f}분)\n")
        out.write(f"  오차         {err:9.3f}s   -> {'OK' if err < 1.0 else 'MISMATCH'}\n")
    else:
        out.write("  실제 영상    (아직 없음)\n")
        actual = None

    # ── 3) 후반부 씬 정렬 — 누적 오차는 뒤에서 드러난다 ──
    sub = V / "번들"
    subs = sorted(glob.glob(str(sub / "ch*" / "subtitles" / "*_narration.srt")))
    out.write("\n== 씬 시작 시각 (뒤쪽 8개) ==\n")
    out.write("   씬   wav길이   예상 시작   자막 첫 큐(전역)   차이\n")
    cum = 0.0
    starts = []
    for i, d in enumerate(durs):
        starts.append(max(0.0, cum - i * CROSSFADE))
        cum += d
    bad = 0
    for i in range(max(0, n - 8), n):
        s = starts[i]
        cue = first_cue(Path(subs[i])) if i < len(subs) else None
        if cue is None:
            out.write(f"  {i+1:3d} {durs[i]:9.2f} {s:11.2f}   (자막 없음)\n")
            continue
        gap = abs(cue)          # 씬별 srt 는 0 기준 -> 첫 큐가 0 근처여야 정상
        flag = "" if gap < 1.0 else "  <- 어긋남"
        if gap >= 1.0:
            bad += 1
        out.write(f"  {i+1:3d} {durs[i]:9.2f} {s:11.2f} {cue:14.2f}{'':6s}{gap:6.2f}{flag}\n")

    # 통합 자막의 마지막 큐가 총 길이와 맞는가 — 누적 드리프트의 최종 판정
    comb = sorted(glob.glob(str(sub / "ch*" / "subtitles" / "ch*.srt")))
    comb = [c for c in comb if "_narration" not in os.path.basename(c)]
    if comb:
        txt = Path(comb[0]).read_text(encoding="utf-8", errors="replace")
        cues = _TS.findall(txt)
        if cues:
            def _sec(g):
                h, mi, s_, ms = g
                return int(h) * 3600 + int(mi) * 60 + int(s_) + int(ms) / 1000
            last_start, last_end = _sec(cues[-1][:4]), _sec(cues[-1][4:])
            ref = actual if actual else expect
            out.write(f"\n  통합 자막 {len(cues)}큐 · 마지막 큐 "
                      f"{last_start:.2f}s → {last_end:.2f}s\n")
            out.write(f"  마지막 큐 **끝** {last_end:8.2f}s  vs  영상 {ref:8.2f}s"
                      f"   차이 {abs(ref - last_end):.2f}s"
                      f"  -> {'OK' if abs(ref - last_end) < 3 else 'DRIFT'}\n")

    out.write(f"\n판정: {'정렬 정상' if bad == 0 else f'{bad}개 씬 어긋남 -> --crossfade 0 (하드컷) 권장'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
