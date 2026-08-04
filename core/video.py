# -*- coding: utf-8 -*-
r"""영상 엔진 브릿지 — 대본 분량 계산 + 렌더 서브프로세스.

**이 모듈은 무거운 것을 import 하지 않는다.** onnxruntime·pywin32·soundfile 은
엔진 프로젝트(260804-ppt2eduvideo)의 별도 venv 에만 있다. 이 앱은 프로세스를 띄우고
progress.json 만 읽는다. 그래서 얻는 것:

  1) onnxruntime 이 이 앱의 의존성과 싸우지 않는다
  2) PowerPoint COM 의 스레드 제약(CoInitialize·단일 인스턴스)을 건드리지 않는다
  3) **앱을 닫아도 렌더가 계속된다** — 100분 영상은 1시간 넘게 걸린다
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

# ── 분량 기준 (엔진 실측값과 같은 수를 쓴다) ──────────────────────────────
CHARS_PER_SEC = 7.53        # Supertonic F2 / speed 1.02, 8씬 8,576자 → 1,139초
# 슬라이드 한 장에 머무는 시간. 2.5분(150초)은 정지화면으로는 너무 길어
# 1.25분(75초)으로 내렸다 — 100분이면 80장이고, 슬라이드당 대본도
# 1,100자에서 약 560자로 줄어 쓰기·검수가 쉬워진다.
MINUTES_PER_SLIDE = 1.25    # 80장 = 100분, 40장 = 50분
TARGET_HEADROOM = 1.08      # 생성이 늘 조금 모자란다 → 목표를 위로 잡는다
MIN_RATIO = 0.85            # 목표의 85% 미만이면 보강 재요청
DEFAULT_VIDEO_MIN_PER_HOUR = 50   # 2시간 수업 = 100분

# 슬라이드 유형별 분량 가중치 — 섹션 표지에 2.5분을 말할 수는 없다
KIND_WEIGHT = {
    "cover": 0.30, "section": 0.35, "agenda": 0.55, "objectives": 0.7,
    "closing": 0.7, "quiz": 0.9, "photo": 0.9, "stat": 1.0,
    "bullets": 1.0, "cards": 1.1, "compare": 1.1, "process": 1.1, "table": 1.1,
}

STAGE_LABEL = {"render": "슬라이드", "script": "대본", "bundle": "번들",
               "tts": "음성·자막", "compose": "영상 합성", "viewer": "뷰어"}
STAGE_ORDER = ("render", "script", "bundle", "tts", "compose", "viewer")


# ── 엔진 위치 ─────────────────────────────────────────────────────────────
def engine_root() -> Path:
    r"""엔진 프로젝트. 이 앱이 그 안에 있으므로 부모다. 옮겨도 따라오게 역산한다."""
    if (env := (os.environ.get("VIDEO_ENGINE") or "").strip().strip('"')):
        return Path(env).expanduser().resolve()
    # 합친 뒤에는 core/ 가 저장소 루트 바로 아래다 → parents[1] 이 루트다.
    return Path(__file__).resolve().parents[1]


def engine_python() -> Path:
    root = engine_root()
    for rel in (("Scripts", "python.exe"), ("bin", "python")):
        p = root / ".venv" / Path(*rel)
        if p.exists():
            return p
    return root / ".venv" / "Scripts" / "python.exe"


def engine_ready() -> tuple[bool, str]:
    """무엇이 없는지 말해 주는 준비 점검. 화면이 먼저 이걸 보여준다."""
    root, py = engine_root(), engine_python()
    if not py.exists():
        return False, f"엔진 가상환경이 없습니다: {py}"
    if not (root / "scripts" / "run_job.py").exists():
        return False, f"run_job.py 가 없습니다: {root / 'scripts'}"
    if not any((root / "vendor" / "chodangi" / "assets" / "onnx").glob("*.onnx")):
        return False, "Supertonic 음성 모델(assets/onnx/*.onnx)이 없습니다."
    if not shutil.which("ffmpeg"):
        return False, "ffmpeg 가 PATH 에 없습니다."
    return True, str(root)


# ── 대본 분량 ─────────────────────────────────────────────────────────────
def video_minutes(form: Dict[str, Any]) -> int:
    """이 차시 영상의 목표 길이. 직접 지정이 없으면 수업 시간에서 유도한다."""
    try:
        v = int(form.get("video_minutes") or 0)
    except (TypeError, ValueError):
        v = 0
    if v > 0:
        return v
    try:
        h = int(form.get("hours") or 2)
    except (TypeError, ValueError):
        h = 2
    return max(1, h) * DEFAULT_VIDEO_MIN_PER_HOUR


def target_slides(form: Dict[str, Any]) -> int:
    return max(4, round(video_minutes(form) / MINUTES_PER_SLIDE))


def target_chars(plan: List[Dict], minutes: float) -> Dict[int, int]:
    """목표 시간을 슬라이드 유형 가중치로 나눠 슬라이드별 목표 글자수를 낸다."""
    total = minutes * 60.0 * CHARS_PER_SEC * TARGET_HEADROOM
    w = {i: KIND_WEIGHT.get(str(s.get("type") or "bullets"), 1.0)
         for i, s in enumerate(plan, start=1)}
    wsum = sum(w.values()) or 1.0
    return {i: max(120, int(total * x / wsum)) for i, x in w.items()}


def est_seconds(text: str) -> float:
    return len(text or "") / CHARS_PER_SEC


def slide_brief(i: int, s: Dict[str, Any]) -> str:
    """프롬프트에 넣을 슬라이드 요약. chip(핵심 메시지)이 대본 뼈대다."""
    parts = [f"[슬라이드 {i}] 유형 {s.get('type') or 'bullets'}"]
    if s.get("level"):
        parts[0] += f" · 인지수준 {s['level']}"
    parts.append(f"제목: {s.get('title') or ''}")
    if s.get("chip"):
        parts.append(f"핵심: {s['chip']}")
    if s.get("kicker"):
        parts.append(f"머리말: {s['kicker']}")
    for b in (s.get("bullets") or []):
        parts.append(f"- {b}")
    for it in (s.get("items") or []):
        if isinstance(it, dict):
            desc = it.get("desc") or " ".join(str(x) for x in (it.get("lines") or []))
            parts.append(f"- {it.get('label', '')}: {desc}" if desc else f"- {it.get('label','')}")
    if s.get("headers") and s.get("rows"):
        parts.append("표 헤더: " + " | ".join(str(h) for h in s["headers"]))
        for row in (s.get("rows") or [])[:6]:
            parts.append("  행: " + " | ".join(str(c) for c in row))
    if s.get("question"):
        parts.append(f"문제: {s['question']}")
        for c in (s.get("choices") or []):
            parts.append(f"  보기: {c}")
        if s.get("answer"):
            parts.append(f"  정답: {s['answer']}")
    if s.get("key"):
        parts.append(f"마무리 한 줄: {s['key']}")
    return "\n".join(parts)


def build_report(plan: List[Dict], narr: Dict[int, str], targets: Dict[int, int]) -> List[Dict]:
    out = []
    for i, s in enumerate(plan, start=1):
        text = narr.get(i, "") or ""
        tgt = targets.get(i, 0)
        out.append({"index": i, "type": s.get("type") or "bullets",
                    "title": s.get("title") or "", "chars": len(text), "target": tgt,
                    "ratio": round(len(text) / tgt, 2) if tgt else None,
                    "est_seconds": round(est_seconds(text), 1)})
    return out


def report_summary(report: List[Dict], minutes: float | None) -> str:
    chars = sum(r["chars"] for r in report)
    sec = sum(r["est_seconds"] for r in report)
    msg = (f"슬라이드 {len(report)}장 · {chars:,}자 · 예상 {sec/60:.1f}분 "
           f"· 평균 {chars//max(len(report),1)}자")
    if minutes:
        msg += f" · 목표 {minutes:.0f}분 대비 {(sec/60-minutes)/minutes*100:+.0f}%"
    return msg


# ── 렌더 잡 ───────────────────────────────────────────────────────────────
def write_job(lec: Path, **fields) -> Path:
    job = {"lecture_dir": str(lec),
           "created_at": datetime.now().isoformat(timespec="seconds")}
    job.update({k: v for k, v in fields.items() if v is not None})
    p = lec / "job.json"
    p.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def start(lec: Path, **fields) -> Dict[str, Any]:
    """엔진을 띄운다. 부모(이 서버)가 죽어도 살아남는다."""
    ok, why = engine_ready()
    if not ok:
        raise RuntimeError(why)
    lec.mkdir(parents=True, exist_ok=True)
    job = write_job(lec, **fields)
    (lec / "progress.json").unlink(missing_ok=True)

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"       # 엔진 로그가 전부 한국어다
    env["PYTHONUNBUFFERED"] = "1"
    log = (lec / "engine.log").open("a", encoding="utf-8")
    log.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
    log.flush()

    flags = 0
    if sys.platform == "win32":
        # CREATE_NO_WINDOW — 검은 콘솔 창을 띄우지 않는다. DETACHED_PROCESS 만으로는
        #   python.exe 가 콘솔 앱이라 창이 하나 뜬다(실제로 떴다). 로그는 engine.log 로
        #   가므로 창은 볼 것이 없다.
        # CREATE_NEW_PROCESS_GROUP — 서버 창에서 Ctrl+C 를 눌러도 렌더는 안 죽는다.
        flags = (subprocess.CREATE_NEW_PROCESS_GROUP
                 | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    proc = subprocess.Popen(
        [str(engine_python()), str(engine_root() / "scripts" / "run_job.py"), str(job)],
        cwd=str(engine_root()), env=env, stdout=log, stderr=subprocess.STDOUT,
        creationflags=flags)
    return {"pid": proc.pid, "job": str(job)}


def read_progress(lec: Path) -> Dict[str, Any] | None:
    p = lec / "progress.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None          # 쓰는 중일 수 있다 — 다음 폴링에서 읽힌다


def is_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
                             capture_output=True, text=True, errors="replace")
        return str(pid) in (out.stdout or "")
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def running(lec: Path) -> bool:
    pr = read_progress(lec)
    if not pr or pr.get("finished_at"):
        return False
    return is_alive(pr.get("pid"))


def died(pr: Dict[str, Any] | None) -> bool:
    """끝나지도 않았는데 프로세스가 없다 = 하드 킬로 죽었다.

    강제 종료(taskkill·재부팅·전원)에는 예외 처리가 돌지 않아 error 가 비어 있고
    stage 는 'run' 으로 남는다. 이걸 구분하지 않으면 죽은 렌더를 '완료' 로 보여준다
    (실제로 그렇게 보였다 — TTS 중에 죽었는데 화면은 지난 실행 완료라고 했다).
    """
    if not pr or pr.get("finished_at") or pr.get("error"):
        return False
    return not is_alive(pr.get("pid"))


def cancel(lec: Path) -> bool:
    """트리째 죽인다 — ffmpeg·POWERPNT 가 손자로 남으면 다음 렌더가 충돌한다."""
    pr = read_progress(lec)
    pid = (pr or {}).get("pid")
    if not pid:
        return False
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(int(pid))], capture_output=True)
    else:
        import signal
        os.kill(int(pid), signal.SIGKILL)
    return True


def summary(pr: Dict[str, Any] | None) -> str:
    if not pr:
        return "실행 이력 없음"
    if pr.get("error"):
        return f"실패 — {pr['error']}"
    if died(pr):
        st = STAGE_LABEL.get(pr.get("stage", ""), pr.get("stage", ""))
        return f"중단됨 ({st} 단계에서 프로세스가 사라졌습니다)"
    if pr.get("finished_at"):
        return "완료"
    st = STAGE_LABEL.get(pr.get("stage", ""), pr.get("stage", ""))
    d, t = pr.get("done", 0), pr.get("total", 0)
    return f"{st} {d}/{t}" if t else (st or "준비 중")


def overall_ratio(pr: Dict[str, Any] | None) -> float:
    """단계별 실측 소요 비중으로 가중 — 합성이 압도적으로 길다."""
    if not pr:
        return 0.0
    weight = {"render": .03, "script": .25, "bundle": .02,
              "tts": .25, "compose": .43, "viewer": .02}
    stages = pr.get("stages") or {}
    acc = 0.0
    for s, w in weight.items():
        state = stages.get(s, "skip")
        if state in ("done", "skip"):
            acc += w
        elif state == "run":
            t = pr.get("total") or 0
            acc += w * (pr.get("done", 0) / t if t else 0.15)
    return min(1.0, acc)


# ── 대본 인쇄 (강사 확정용) ────────────────────────────────────────────────
#   영상과 분리한다. 대본은 사진·렌더 없이도 나오므로, 강사가 먼저 종이로 확인하고
#   확정한 뒤에 사진을 채우고 영상을 만드는 순서가 맞다.
#
#   슬라이드는 **작게 왼쪽**에 넣는다. 어느 화면에 대한 말인지 알아볼 정도면 되고,
#   크게 넣으면 82씬이 60쪽을 넘어 확인이 오히려 어려워진다.
_PRINT_CSS = """
*{box-sizing:border-box}
body{margin:0;padding:16mm 13mm;background:#fff;color:#111;
  font:10.5pt/1.7 'Malgun Gothic','맑은 고딕',system-ui,sans-serif}
.head{border-bottom:2px solid #222;padding-bottom:4mm;margin-bottom:6mm}
.head h1{margin:0;font-size:15pt;letter-spacing:-.02em}
.head .meta{color:#444;font-size:9.5pt;margin-top:2mm}
.head .sign{margin-top:4mm;font-size:9.5pt;color:#333}
.head .sign span{display:inline-block;min-width:50mm;border-bottom:1px solid #999;
  margin:0 6mm 0 2mm}
.bar{padding:0 0 5mm;margin-bottom:5mm;border-bottom:1px solid #eee;
  display:flex;gap:8px;align-items:center;flex-wrap:wrap}
button{font:11.5pt inherit;padding:7px 18px;border-radius:999px;border:1px solid #1668c1;
  background:#1668c1;color:#fff;cursor:pointer}
button.ghost{background:#fff;color:#1668c1}
.hint{color:#666;font-size:9.5pt}

.scene{break-inside:avoid;page-break-inside:avoid;margin:0 0 5mm;
  border:1px solid #ddd;border-radius:5px;padding:3.5mm 4mm}
.st{display:flex;gap:3mm;align-items:baseline;margin-bottom:2.5mm;
  padding-bottom:1.8mm;border-bottom:1px solid #eee;flex-wrap:wrap}
.st b{font-size:11pt}
.st .t{flex:1 1 auto}
.st .tc{font-variant-numeric:tabular-nums;color:#1668c1;font-weight:600;
  font-size:9.5pt;white-space:nowrap}
.st .n{color:#666;font-size:8.5pt;white-space:nowrap}
.st .warn{color:#b45309;font-weight:600}

/* 슬라이드 왼쪽 · 대본 오른쪽 */
.row{display:grid;grid-template-columns:50mm 1fr;gap:4mm;align-items:start}
.row.noimg{grid-template-columns:1fr}
.row img{width:50mm;border:1px solid #ddd;border-radius:3px;display:block}
.narr{font-size:10.5pt;line-height:1.8;white-space:pre-wrap;word-break:keep-all}

.chk{display:flex;gap:5mm;align-items:flex-start;margin-top:2.5mm;padding-top:2mm;
  border-top:1px dashed #ccc}
.chk .box{flex:0 0 auto;font-size:9pt;color:#333;white-space:nowrap}
.chk .box i{display:inline-block;width:3.4mm;height:3.4mm;border:1px solid #666;
  border-radius:1px;vertical-align:-.4mm;margin-right:1.4mm}
.chk .memo{flex:1 1 auto;border-bottom:1px solid #ddd;min-height:9mm}
@media print{
  body{padding:0}
  .bar{display:none}
  @page{size:A4;margin:13mm 11mm 15mm}
}
"""


def _mmss(sec: float) -> str:
    """0:24 / 55:58 / 1:52:12 — 1시간을 넘으면 시를 붙인다.

    100분 강의를 분:초로만 쓰면 '110:12' 가 되어 종이에서 읽히지 않는다.
    """
    s = int(sec)
    h, m, s = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def scene_times(narr_chars: List[int], bundle: Path | None,
                crossfade: float = 0.6) -> tuple[List[tuple], bool]:
    """씬별 (시작, 끝) 초. 실측 wav 가 있으면 그걸 쓰고, 없으면 글자수로 추정한다.

    ★ 크로스페이드를 빼야 실제 영상의 시각과 맞는다:
        씬 N 시작 = sum(dur[0..N-1]) - N x crossfade
      이걸 빼먹으면 82씬에서 최대 48초가 밀린다.

    돌려주는 bool 은 '실측인가' — 종이에 추정치를 실측처럼 적으면 안 된다.
    """
    durs: List[float] = []
    measured = False
    if bundle and bundle.is_dir():
        wavs = sorted(bundle.glob("audio/*_narration.wav"))
        if len(wavs) == len(narr_chars) and wavs:
            import wave
            try:
                for w in wavs:
                    with wave.open(str(w), "rb") as f:
                        durs.append(f.getnframes() / float(f.getframerate()))
                measured = True
            except Exception:      # noqa: BLE001 — 못 읽으면 추정으로 간다
                durs, measured = [], False
    if not measured:
        durs = [c / CHARS_PER_SEC for c in narr_chars]

    out, cum = [], 0.0
    for i, d in enumerate(durs):
        start = max(0.0, cum - i * crossfade)
        out.append((start, start + d))
        cum += d
    return out, measured


def script_print_html(*, course: str, week: int, doc: Dict[str, Any],
                      minutes: float | None = None,
                      slide_img: Dict[int, str] | None = None,
                      bundle: Path | None = None) -> str:
    """나레이션 대본 → 인쇄용 HTML. 씬마다 확인란·메모 여백과 시간대를 붙인다."""
    import html as _h

    rows = sorted(doc.get("slides") or [], key=lambda r: int(r["index"]))
    rep = {int(r["index"]): r for r in (doc.get("report") or [])}
    chars = [len(r.get("narration") or "") for r in rows]
    total = sum(chars)
    times, measured = scene_times(chars, bundle)
    end = times[-1][1] if times else 0.0
    tgt = minutes or doc.get("minutes") or 0
    imgs = slide_img or {}

    secs = []
    for k, r in enumerate(rows):
        i = int(r["index"])
        m = rep.get(i, {})
        n = chars[k]
        warn = ""
        if m.get("ratio") is not None and m["ratio"] < MIN_RATIO:
            warn = f' · <span class="warn">목표의 {m["ratio"]:.0%}</span>'
        st, en = times[k]
        src = imgs.get(i, "")
        img = f'<img src="{src}" alt="슬라이드 {i}">' if src else ""
        secs.append(f"""
<div class="scene">
  <div class="st"><b>씬 {i:02d}</b>
    <span class="tc">{_mmss(st)} ~ {_mmss(en)}</span>
    <span class="t">{_h.escape(r.get('title') or '')}</span>
    <span class="n">{n:,}자{f" / 목표 {m['target']:,}" if m.get('target') else ''}{warn}</span>
  </div>
  <div class="row{'' if src else ' noimg'}">
    {img}
    <div class="narr">{_h.escape(r.get('narration') or '')}</div>
  </div>
  <div class="chk">
    <span class="box"><i></i>확인</span>
    <span class="box"><i></i>수정 필요</span>
    <span class="memo"></span>
  </div>
</div>""")

    kind = "실측(음성 파일 기준)" if measured else "추정(글자수 기준)"
    return f"""<!doctype html>
<meta charset="utf-8">
<title>{_h.escape(course)} {week}주차 — 나레이션 대본</title>
<style>{_PRINT_CSS}</style>
<div class="bar">
  <button onclick="window.print()">인쇄</button>
  <button class="ghost" onclick="window.close()">닫기</button>
  <span class="hint">PDF 로 받으려면 인쇄 대화상자에서 대상을 &#39;PDF 로 저장&#39; 으로 고르세요. 이 줄은 인쇄되지 않습니다.</span>
</div>
<div class="head">
  <h1>{_h.escape(course)} · {week}주차 나레이션 대본</h1>
  <div class="meta">씬 {len(rows)}개 · {total:,}자 · 전체 {_mmss(end)}
    ({end/60:.1f}분){f" · 목표 {tgt:.0f}분" if tgt else ""} · 시간 {kind}</div>
  <div class="sign">확인<span></span>일자<span></span></div>
</div>
{''.join(secs)}
"""
