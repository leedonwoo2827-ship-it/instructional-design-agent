# -*- coding: utf-8 -*-
r"""씬별 뷰어 생성 — 강의 폴더에 viewer.html 을 만든다.

    .venv\Scripts\python scripts\make_viewer.py "D:\00work\lecture-out-260804\샘플\2강"

번들이 실제로 어떻게 구성됐는지를 한 화면에서 보게 하는 것이 목적이다:
  - 씬 목록 (번호·제목·길이·시작시각)
  - 씬별 슬라이드 이미지
  - **음성대본** (narration_text — TTS 입력) 과 **실제 발화형** (발음 교정 적용 후)
  - **자막대본** (씬별 SRT 큐 + 타임코드)
  - **씬별 wav 플레이어** — 음성이 씬 단위로 쪼개져 번들되는 걸 눈으로 확인
  - **번역 칸** (언어 미정. 어느 언어든 넣을 수 있고 브라우저에 저장 후 JSON 내보내기)

viewer.html 은 강의 폴더 안에 놓이므로 02/slides, 04/*/audio, 05/out.mp4 를 상대경로로 참조한다.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import config  # noqa: E402  (엔진 경로 주입)

CROSSFADE = config.CROSSFADE_SEC
_TS = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")


def _secs(h, m, s, ms) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def parse_srt(path: Path) -> list[dict]:
    if not path.exists():
        return []
    cues, cur = [], None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if (m := _TS.search(line)):
            cur = {"start": _secs(*m.groups()[:4]), "end": _secs(*m.groups()[4:]), "text": ""}
            cues.append(cur)
        elif cur is not None and line.strip() and not line.strip().isdigit():
            cur["text"] = (cur["text"] + " " + line.strip()).strip()
    return cues


def wav_duration(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip() or 0)
    except Exception:  # noqa: BLE001
        return 0.0


_PMAP = None


def _pmap():
    """발음 교정 맵. 실패하면 조용히 넘기지 않고 한 번 알린다 —
    조용한 폴백은 '변화 없음'과 '기능 고장'을 구분할 수 없게 만든다."""
    global _PMAP
    if _PMAP is None:
        from voicewright.pronunciation import load_pronunciation_map
        from voicewright.settings import load as load_settings
        _PMAP = load_pronunciation_map(load_settings().pronunciation_map_path)
    return _PMAP


def pronounce(text: str) -> str:
    """발음 교정 적용 결과 — TTS 가 실제로 읽은 문장. 자막에는 적용되지 않는다."""
    return _pmap().apply(text, spell_unknown_acronyms=True, convert_years=True)


def mmss(sec: float) -> str:
    return f"{int(sec // 60)}:{sec % 60:04.1f}"


def _any(base: Path, pats: tuple[str, ...]) -> bool:
    return any(next(iter(base.glob(p)), None) is not None for p in pats)


def scan_workspace(lec: Path) -> list[dict]:
    """워크스페이스 전체의 과목/주차 목록 + 단계 상태.

    위에 띄우는 목록 패널이 쓴다. 상태는 xam-local 의 3색 규칙을 따른다:
    회색(안 함) → 노랑(일부) → 청록(완료). **안 한 쪽에는 색을 주지 않는다.**
    """
    root = lec.parent.parent
    out = []
    for subj in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")):
        weeks = []
        for wk in sorted(subj.iterdir(), key=lambda p: (len(p.name), p.name)):
            if not wk.is_dir():
                continue
            st = {
                "원본": bool(list((wk / "00").glob("*.pptx"))) if (wk / "00").is_dir() else False,
                "덱": (wk / "01" / "deck.pptx").exists(),
                "슬라이드": _any(wk, ("슬라이드/*.png", "02/slides/*.png")),
                "대본": _any(wk, ("나레이션.json", "03/script.json")),
                "번들": _any(wk, ("번들/ch*/audio", "04/ch*/audio")),
                "영상": _any(wk, ("완성/*.mp4", "05/out.mp4")),
            }
            done = sum(st.values())
            weeks.append({
                "name": wk.name, "stages": st, "done": done, "total": len(st),
                "state": "done" if done == len(st) else ("part" if done else "todo"),
                "here": wk.resolve() == lec.resolve(),
                "href": f"../../{subj.name}/{wk.name}/viewer.html",
                "has_viewer": (wk / "viewer.html").exists() or wk.resolve() == lec.resolve(),
            })
        if weeks:
            out.append({"subject": subj.name, "weeks": weeks})
    return out


def find_bundle(lec: Path) -> Path:
    """번들 위치 — 새 구조(06_영상/번들/chNN)를 먼저 보고, 옛 구조(04/chNN)도 받는다."""
    for sub in ("번들", "04"):
        hits = sorted(p for p in (lec / sub).glob("ch*") if p.is_dir()) \
            if (lec / sub).is_dir() else []
        if hits:
            return hits[0]
    hits = sorted(p for p in lec.glob("**/ch[0-9][0-9]")
                  if p.is_dir() and (p / "audio").is_dir())
    if hits:
        return hits[0]
    raise SystemExit(f"번들을 찾지 못했습니다: {lec}")


def collect(lec: Path) -> dict:
    b = find_bundle(lec)
    brel = b.relative_to(lec).as_posix()
    sp = next(iter((b / "script").glob("*_script.json")), None)
    if sp is None:
        raise SystemExit(f"번들 script JSON 이 없습니다: {b}")
    doc = json.loads(sp.read_text(encoding="utf-8"))

    # 03/script.json 의 분량 리포트(있으면 함께 보여준다)
    rep_by_idx = {}
    s3 = next((q for q in (lec / "나레이션.json", lec / "03" / "script.json") if q.is_file()), None)
    if s3 is not None:
        try:
            d3 = json.loads(s3.read_text(encoding="utf-8"))
            rep_by_idx = {int(r["index"]): r for r in (d3.get("report") or [])}
        except Exception:  # noqa: BLE001
            pass

    scenes, cum = [], 0.0
    for sc in doc.get("scenes", []):
        n = int(sc.get("scene", 0))
        stem = f"{b.name if b.name.startswith('ch') else 'ch'}"
        wavs = sorted((b / "audio").glob(f"*_{n:02d}_narration.wav"))
        wav = wavs[0] if wavs else None
        dur = wav_duration(wav) if wav else 0.0
        srts = sorted((b / "subtitles").glob(f"*_{n:02d}_narration.srt"))
        cues = parse_srt(srts[0]) if srts else []

        # 통합 타임라인상의 시작 시각 (엔진과 같은 식: cum - n*crossfade)
        start = max(0.0, cum - (len(scenes)) * CROSSFADE)
        cum += dur

        narr = sc.get("narration_text", "") or ""
        img = sc.get("image_filename", "")
        # 번들 안 이미지가 원본이지만, 02/slides 쪽이 있으면 그걸 쓴다(용량 중복 방지)
        img_rel = f"{brel}/images/{img}" if (b / "images" / img).is_file() else ""
        if not img_rel:
            for sub in ("슬라이드", "02/slides"):
                cands = sorted((lec / sub).glob(f"{n:03d}.png")) if (lec / sub).is_dir() else []
                if cands:
                    img_rel = f"{sub}/{cands[0].name}"
                    break

        r = rep_by_idx.get(n, {})
        scenes.append({
            "n": n, "title": sc.get("title", ""), "kind": r.get("kind", ""),
            "narration": narr, "spoken": pronounce(narr),
            "srt_text": sc.get("srt_text") or "",
            "cues": cues, "dur": dur, "start": start,
            "wav": f"{brel}/audio/{wav.name}" if wav else "",
            "wav_mb": (wav.stat().st_size / 1048576) if wav else 0,
            "img": img_rel,
            "chars": len(narr), "target": r.get("target", 0), "ratio": r.get("ratio"),
        })

    # 완성 영상 — 새 구조는 완성/영상_vN.mp4, 옛 구조는 05/out.mp4
    vid = srt = None
    for sub in ("완성", "05"):
        d = lec / sub
        if d.is_dir():
            hits = sorted(d.glob("*.mp4"))
            if hits:
                vid = hits[-1]
                cand = vid.with_suffix(".srt")
                srt = cand if cand.is_file() else next(iter(sorted(d.glob("*.srt"))), None)
                break
    vid_rel = vid.relative_to(lec).as_posix() if vid else ""
    srt_rel = srt.relative_to(lec).as_posix() if srt else ""

    total = cum - max(0, len(scenes) - 1) * CROSSFADE
    return {
        "title": doc.get("title", lec.name), "voice": doc.get("voice", ""),
        "speed": doc.get("speed", ""), "chapter": doc.get("chapter", ""),
        "lecture": f"{lec.parent.name} / {lec.name}",
        "video": vid_rel, "vtt": srt_rel,
        "total": total, "scenes": scenes,
    }


CSS = """
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css');
/* 토큰은 instructional-design-agent app.py 의 _CSS 와 동일하게 맞춘다 — 같은 제품이다. */
*{box-sizing:border-box}
:root{
  --canvas:#ffffff; --bg:#fbfbfd; --ink:#141518; --ink2:#5b6472;
  --line:#ececee; --line-soft:#f2f2f4;
  --brand:#3b4ec8; --brand2:#2c3aa0; --brand-soft:#eef0fb;
  --lilac:#efeaff; --lime:#eef6dd; --cream:#fbf3e6; --mint:#e6f6ef;
  --ok:#0e9f6e; --warn:#b45309; --err:#cc4117;
}
body{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.7 'Pretendard',-apple-system,'Malgun Gothic',sans-serif}
a{color:var(--brand)}
.mono{font-family:'SF Mono','JetBrains Mono',Consolas,monospace}

/* 헤더 — ida-header 와 같은 형태 (헤어라인, 그림자 없음, radius 20) */
header{position:sticky;top:0;z-index:20;background:var(--bg);
  padding:12px 24px 10px;display:flex;gap:14px;align-items:center;flex-wrap:wrap;
  border-bottom:1px solid var(--line)}
.eyebrow{font-family:'SF Mono',Consolas,monospace;font-size:11px;letter-spacing:.16em;
  text-transform:uppercase;color:var(--ink2)}
h1{font-size:20px;margin:0;font-weight:800;letter-spacing:-.03em}
.meta{color:var(--ink2);font-size:13px}
.tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-left:auto}
button,select{font:13px/1.4 inherit;font-weight:600;padding:7px 15px;border-radius:999px;
  border:1px solid var(--line);background:var(--canvas);color:var(--ink);cursor:pointer;
  transition:.15s}
button:hover,select:hover{border-color:var(--brand);color:var(--brand)}
button.primary{background:var(--brand);border-color:var(--brand);color:#fff}
button.primary:hover{background:var(--brand2);border-color:var(--brand2);color:#fff}

/* ── 위에 띄우는 목록 패널 (두 레이어 UX) ─────────────────────────────
   xam-local 규약: 목록은 위, 작업은 아래. 패널은 **언마운트하지 않는다** —
   숨길 때도 DOM 에 남겨 편집 중인 텍스트와 스크롤 위치를 잃지 않는다. */
#scrim{position:fixed;inset:0;background:rgba(20,21,24,.28);z-index:30;
  opacity:0;pointer-events:none;transition:opacity .16s}
body.panel #scrim{opacity:1;pointer-events:auto}
#panel{position:fixed;left:50%;top:64px;transform:translate(-50%,-8px) scale(.985);
  width:min(760px,calc(100vw - 32px));max-height:calc(100vh - 96px);overflow-y:auto;z-index:31;
  background:var(--canvas);border:1px solid var(--line);border-radius:20px;padding:18px 20px;
  opacity:0;pointer-events:none;transition:opacity .16s,transform .16s}
body.panel #panel{opacity:1;pointer-events:auto;transform:translate(-50%,0) scale(1)}
.ptitle{font-weight:700;font-size:14.5px;margin:0 0 4px;letter-spacing:-.01em}
.subj{font-weight:700;font-size:13px;color:var(--ink2);margin:16px 0 6px}
.subj:first-of-type{margin-top:10px}
.wk{display:grid;grid-template-columns:74px 1fr auto;gap:10px;align-items:center;
  padding:9px 12px;border:1px solid var(--line);border-radius:14px;margin-bottom:6px;
  text-decoration:none;color:inherit;background:var(--canvas)}
.wk:hover{border-color:var(--brand)}
.wk.here{border-color:var(--brand);background:var(--brand-soft)}
.wk .nm{font-weight:700;font-size:13.5px}
/* 3색 규칙: 회색(안 함) → 노랑(일부) → 청록(완료).
   ★ 안 한 쪽에는 색을 주지 않는다. */
.dots{display:flex;gap:4px;flex-wrap:wrap}
.dot{font-size:10.5px;padding:2px 8px;border-radius:999px;border:1px solid var(--line);
  color:var(--ink2);background:var(--canvas)}
.dot.on{background:var(--mint);border-color:#bfe6d6;color:var(--ok);font-weight:600}
.pill{font-size:11px;padding:3px 10px;border-radius:999px;font-weight:600;white-space:nowrap}
.pill.todo{background:var(--line-soft);color:var(--ink2)}
.pill.part{background:var(--cream);color:var(--warn)}
.pill.done{background:var(--mint);color:var(--ok)}

/* ── 아래 베이스 레이어 (작업) ───────────────────────────────────── */
.wrap{display:grid;grid-template-columns:252px 1fr;align-items:start}
nav{position:sticky;top:58px;max-height:calc(100vh - 58px);overflow-y:auto;padding:12px 10px}
nav a{display:grid;grid-template-columns:26px 1fr auto;gap:8px;padding:7px 10px;
  border-radius:999px;color:inherit;text-decoration:none;font-size:13px;align-items:baseline}
nav a:hover{background:var(--brand-soft)}
nav a.on{background:var(--brand);color:#fff}
nav .num{color:var(--ink2);font-variant-numeric:tabular-nums;font-size:11.5px}
nav a.on .num,nav a.on .d{color:#fff;opacity:.9}
nav .t{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
nav .d{color:var(--ink2);font-size:11px;font-variant-numeric:tabular-nums}
main{padding:16px 24px 60px;min-width:0;max-width:1240px}
section.scene{border:1px solid var(--line);border-radius:20px;margin-bottom:14px;
  overflow:hidden;background:var(--canvas)}
.sh{display:flex;gap:9px;align-items:baseline;padding:14px 18px;flex-wrap:wrap;
  border-bottom:1px solid var(--line-soft)}
.sh b{font-size:14.5px;font-weight:800;letter-spacing:-.01em}
.badge{font-size:11px;padding:3px 9px;border-radius:999px;background:var(--bg);
  border:1px solid var(--line);color:var(--ink2)}
.body{padding:16px 18px;display:grid;gap:14px}
img.slide{width:100%;max-width:620px;border:1px solid var(--line);border-radius:16px;display:block}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:900px){.wrap{grid-template-columns:1fr}
  nav{position:static;max-height:none;border-bottom:1px solid var(--line)}
  .cols{grid-template-columns:1fr}}
.pane{min-width:0}
.pane h4{margin:0 0 6px;font-size:12px;color:var(--ink2);font-weight:700;
  display:flex;gap:6px;align-items:baseline;flex-wrap:wrap}
.pane .n{font-weight:400;color:var(--ink2);font-size:11px}
.txt{background:var(--bg);border:1px solid var(--line);border-radius:14px;padding:11px 13px;
  font-size:14px;white-space:pre-wrap;word-break:break-word}
.txt.cues{font-family:'SF Mono',Consolas,monospace;font-size:12.5px;line-height:1.65;
  background:var(--lilac);border-color:#e2d9ff}
.cue{display:grid;grid-template-columns:118px 1fr;gap:8px;padding:2px 0}
.cue .ts{color:var(--ink2);font-variant-numeric:tabular-nums;font-size:11.5px}
audio{width:100%;margin-top:4px;height:34px}
.diff{background:var(--cream);border-radius:4px;padding:0 3px;font-weight:600;color:var(--warn)}
textarea.tr{width:100%;min-height:104px;background:var(--bg);color:var(--ink);
  border:1px dashed #d8d8dc;border-radius:14px;padding:11px 13px;
  font:14px/1.7 inherit;resize:vertical}
textarea.tr:focus{border-color:var(--brand);border-style:solid;outline:none}
video{width:100%;max-width:900px;border-radius:20px;display:block;margin-bottom:8px}
.note{color:var(--ink2);font-size:12.5px;margin:0 0 18px}
.note b{color:var(--ink)}
"""


CSS2 = """
/* 페이지 전환 탭 */
.pagetabs{display:flex;gap:8px}
.pagetabs a{font-size:13px;font-weight:600;padding:7px 15px;border-radius:999px;
  border:1px solid var(--line);background:var(--canvas);color:var(--ink);text-decoration:none}
.pagetabs a:hover{border-color:var(--brand);color:var(--brand)}
.pagetabs a.on{background:var(--brand);border-color:var(--brand);color:#fff}

/* 번들 트리 */
.tree{font-family:'SF Mono',Consolas,monospace;font-size:12.5px;line-height:1.85;
  background:var(--canvas);border:1px solid var(--line);border-radius:16px;padding:14px 18px;
  white-space:pre;overflow-x:auto;margin-bottom:14px}
.tree .d{color:var(--brand);font-weight:600}
.tree .sz{color:var(--ink2)}

/* 실시간 자막 */
.live{position:sticky;top:58px;z-index:9;background:var(--canvas);border:1px solid var(--line);
  border-radius:16px;padding:12px 16px;margin-bottom:14px}
.live .cur{font-size:17px;font-weight:600;min-height:1.7em;letter-spacing:-.01em}
.live .nxt{font-size:13px;color:var(--ink2);min-height:1.6em;margin-top:4px}
.cue.now{background:var(--brand-soft);border-radius:8px;font-weight:600}

/* 산식 표 */
table.calc{border-collapse:collapse;width:100%;font-size:13px;background:var(--canvas);
  border:1px solid var(--line);border-radius:16px;overflow:hidden}
table.calc th,table.calc td{padding:8px 12px;border-bottom:1px solid var(--line-soft);text-align:right}
table.calc th{background:var(--bg);font-weight:700;color:var(--ink2);font-size:11.5px}
table.calc td:first-child,table.calc th:first-child{text-align:left}
table.calc tr:last-child td{border-bottom:none;font-weight:700;background:var(--mint)}
table.calc td.num{font-variant-numeric:tabular-nums;font-family:'SF Mono',Consolas,monospace}

/* 발음 카드 */
.pron{display:grid;grid-template-columns:1fr;gap:8px}
.pron .row{display:grid;grid-template-columns:76px 1fr;gap:10px;align-items:start;
  padding:10px 12px;background:var(--bg);border:1px solid var(--line);border-radius:14px}
.pron .lbl{font-size:11px;font-weight:700;color:var(--ink2);padding-top:2px}
.pron .same{color:var(--ink2);font-size:12.5px}
.toggle{display:flex;gap:6px;align-items:center;font-size:12.5px;color:var(--ink2)}
"""

JS_COMMON = r"""
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];

// 목록 패널 — 언마운트하지 않는다(편집 중 텍스트·스크롤 보존)
const openBtn = $('#openlist');
if (openBtn) {
  openBtn.onclick = () => document.body.classList.toggle('panel');
  $('#scrim').onclick = () => document.body.classList.remove('panel');
  addEventListener('keydown', e => { if (e.key === 'Escape') document.body.classList.remove('panel'); });
}

// 씬 목록 하이라이트
const links = $$('nav a');
if (links.length) {
  const obs = new IntersectionObserver(es => es.forEach(e => {
    if (e.isIntersecting) links.forEach(l => l.classList.toggle('on', l.hash === '#s' + e.target.dataset.n));
  }), { rootMargin: '-90px 0px -70% 0px' });
  $$('section.scene').forEach(s => obs.observe(s));
}
"""

JS_SCRIPT_PAGE = r"""
// 슬라이드 표시 토글 — 최종 폴더에서는 대본만 보고 싶을 때가 많다
const sw = $('#showslides');
if (sw) {
  const KEY = 'showslides';
  const apply = v => { $$('img.slide').forEach(i => i.style.display = v ? 'block' : 'none'); sw.checked = v; };
  apply(localStorage.getItem(KEY) !== '0');
  sw.onchange = () => { localStorage.setItem(KEY, sw.checked ? '1' : '0'); apply(sw.checked); };
}

// 대본 전체 복사 / 내려받기
const plain = () => $$('section.scene').map(s => {
  const n = s.dataset.n, t = s.querySelector('.sh span').textContent;
  const v = s.querySelector('[data-k=voice]')?.textContent.trim() || '';
  const c = s.querySelector('[data-k=sub]')?.innerText.trim() || '';
  return `## 씬 ${n} — ${t}\n\n[음성대본]\n${v}\n\n[자막대본]\n${c}\n`;
}).join('\n');
const cp = $('#copy');
if (cp) cp.onclick = async () => {
  await navigator.clipboard.writeText(plain());
  cp.textContent = '복사됨'; setTimeout(() => cp.textContent = '전체 복사', 1400);
};
const dl = $('#download');
if (dl) dl.onclick = () => {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([plain()], { type: 'text/markdown;charset=utf-8' }));
  a.download = document.body.dataset.lec.replace(/[\/\s]+/g, '_') + '_대본.md'; a.click();
};
"""

JS_BUNDLE_PAGE = r"""
// 실시간 자막 — 영상 재생 위치에 맞춰 현재/다음 큐를 띄우고 목록도 따라 하이라이트
const CUES = window.__CUES__ || [];
const v = $('video');
if (v && CUES.length) {
  const cur = $('#livecur'), nxt = $('#livenxt');
  let last = -1;
  const tick = () => {
    const t = v.currentTime;
    let i = CUES.findIndex(c => t >= c.s && t < c.e);
    if (i < 0) i = -1;
    if (i !== last) {
      last = i;
      cur.textContent = i >= 0 ? CUES[i].t : '—';
      nxt.textContent = (i + 1 < CUES.length) ? '다음: ' + CUES[i + 1].t : '';
      $$('.cue').forEach(e => e.classList.remove('now'));
      if (i >= 0) {
        const el = document.getElementById('g' + i);
        if (el) el.classList.add('now');
      }
    }
  };
  v.addEventListener('timeupdate', tick);
  $$('[data-seek]').forEach(b => b.onclick = () => {
    v.currentTime = parseFloat(b.dataset.seek); v.play();
    v.scrollIntoView({ block: 'start', behavior: 'smooth' });
  });
  $$('.cue[data-t]').forEach(c => c.onclick = () => {
    v.currentTime = parseFloat(c.dataset.t); v.play();
  });
}
"""


def mark_diff(orig: str, spoken: str) -> str:
    """발음 교정으로 바뀐 부분만 강조. 단어 단위 비교로 충분하다."""
    import difflib
    a, b = orig.split(), spoken.split()
    out = []
    for tag, _i1, _i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        seg = html.escape(" ".join(b[j1:j2]))
        if not seg:
            continue
        out.append(f'<span class="diff">{seg}</span>' if tag in ("replace", "insert") else seg)
    return " ".join(out)


def _panel(ws: list[dict], lec_label: str) -> str:
    """위에 띄우는 강의 목록 패널. 3색 규칙: 회색(안 함) → 노랑(일부) → 청록(완료)."""
    esc = html.escape
    blocks = []
    for s in ws:
        rows = []
        for w in s["weeks"]:
            dots = "".join(
                f'<span class="dot{" on" if v else ""}">{esc(k)}</span>'
                for k, v in w["stages"].items())
            label = {"done": "완료", "part": f'{w["done"]}/{w["total"]}', "todo": "시작 전"}[w["state"]]
            rows.append(
                f'<a class="wk{" here" if w["here"] else ""}" href="{esc(w["href"])}">'
                f'<span class="nm">{esc(w["name"])}</span>'
                f'<span class="dots">{dots}</span>'
                f'<span class="pill {w["state"]}">{label}</span></a>')
        blocks.append(f'<div class="subj">{esc(s["subject"])}</div>' + "".join(rows))
    return f"""<div id="scrim"></div>
<div id="panel">
  <p class="ptitle">강의 목록</p>
  <p class="note">현재: <b>{esc(lec_label)}</b> · 단계 배지는 완료된 것에만 색이 붙습니다.</p>
  {''.join(blocks) or '<p class="note">워크스페이스에 강의가 없습니다.</p>'}
</div>"""


def _shell(d: dict, ws: list[dict], page: str, tools: str, body: str,
           extra_css: str = "", extra_js: str = "") -> str:
    esc = html.escape
    tabs = "".join(
        f'<a href="{href}" class="{"on" if page == key else ""}">{label}</a>'
        for key, href, label in (("script", "script.html", "대본"),
                                 ("bundle", "bundle.html", "번들 구성")))
    nav = "\n".join(
        f'<a href="#s{s["n"]}"><span class="num">{s["n"]:02d}</span>'
        f'<span class="t">{esc(s["title"] or "(제목 없음)")}</span>'
        f'<span class="d">{s["dur"]:.0f}s</span></a>' for s in d["scenes"])
    return f"""<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(d['lecture'])} — {esc(dict(script='대본', bundle='번들 구성')[page])}</title>
<style>{CSS}{CSS2}{extra_css}</style>
<body data-lec="{esc(d['lecture'])}">
{_panel(ws, d['lecture'])}
<header>
  <div>
    <div class="eyebrow">Lecture Video</div>
    <h1>{esc(d['lecture'])}</h1>
  </div>
  <div class="pagetabs">{tabs}</div>
  <span class="meta">씬 {len(d['scenes'])} · {d['total']/60:.1f}분</span>
  <div class="tools">
    {tools}
    <button id="openlist">강의 목록</button>
  </div>
</header>
<div class="wrap">
  <nav>{nav}</nav>
  <main>{body}</main>
</div>
<script>{JS_COMMON}{extra_js}</script>
"""


def render_script(d: dict, ws: list[dict]) -> str:
    """최종 폴더용 — 슬라이드 · 음성대본 · 자막대본만. 쭉 나열한다."""
    esc = html.escape
    secs = []
    for s in d["scenes"]:
        cues = "\n".join(esc(c["text"]) for c in s["cues"]) or "(자막 없음)"
        img = (f'<img class="slide" src="{s["img"]}" alt="슬라이드 {s["n"]}" loading="lazy">'
               if s["img"] else "")
        secs.append(f"""
<section class="scene" id="s{s['n']}" data-n="{s['n']}">
  <div class="sh"><b>씬 {s['n']:02d}</b><span>{esc(s['title'] or '(제목 없음)')}</span>
    <span class="badge">{s['dur']:.0f}초</span>
    <span class="badge">{s['chars']:,}자</span></div>
  <div class="body">
    {img}
    <div class="cols">
      <div class="pane"><h4>음성대본 <span class="n">TTS 입력</span></h4>
        <div class="txt" data-k="voice">{esc(s['narration'])}</div></div>
      <div class="pane"><h4>자막대본 <span class="n">{len(s['cues'])}줄</span></h4>
        <div class="txt" data-k="sub">{cues}</div></div>
    </div>
  </div>
</section>""")
    tools = ('<label class="toggle"><input type="checkbox" id="showslides" checked> 슬라이드</label>'
             '<button id="copy">전체 복사</button><button id="download">.md 내려받기</button>')
    body = ('<p class="note">최종 대본입니다. 음성대본은 TTS 입력이고, 자막대본은 영상에 표시되는 '
            '문장입니다. 발음 교정·타임코드·번들 구조는 <b>번들 구성</b> 탭에 있습니다.</p>'
            + "".join(secs))
    return _shell(d, ws, "script", tools, body, extra_js=JS_SCRIPT_PAGE)


def render_bundle(d: dict, ws: list[dict], lec: Path) -> str:
    """번들 해부 — 음성이 어떻게 씬 단위로 쪼개져 담기고, 발음·자막이 어디서 갈라지는가."""
    esc = html.escape

    # ── 실제 폴더 트리 (파일명·크기) ──
    tree_lines = []
    b = find_bundle(lec)
    brel = b.relative_to(lec).as_posix()
    tree_lines.append(f'<span class="d">{brel}/</span>   ← chodangi 번들')
    for sub in ("script", "images", "audio", "subtitles", "clips", "draft"):
        sd = b / sub
        if not sd.is_dir():
            continue
        files = sorted(sd.iterdir())
        tot = sum(f.stat().st_size for f in files if f.is_file())
        tree_lines.append(f'  <span class="d">{sub}/</span>'
                          f'{"":{max(1, 12 - len(sub))}}<span class="sz">'
                          f'{len(files)}개 · {tot/1048576:.1f} MB</span>')
        for f in files[:3]:
            if f.is_file():
                tree_lines.append(f'    {esc(f.name)}'
                                  f'{"":{max(1, 34 - len(f.name))}}'
                                  f'<span class="sz">{f.stat().st_size/1024:,.0f} KB</span>')
        if len(files) > 3:
            tree_lines.append(f'    <span class="sz">… {len(files)-3}개 더</span>')
    for stage, label in (("완성", "최종 산출물"), ("05", "최종 산출물")):
        sd = lec / stage
        if sd.is_dir():
            tree_lines.append(f'<span class="d">{stage}/</span>   ← {label}')
            for f in sorted(sd.iterdir()):
                if f.is_file():
                    tree_lines.append(f'  {esc(f.name)}'
                                      f'{"":{max(1, 36 - len(f.name))}}'
                                      f'<span class="sz">{f.stat().st_size/1048576:.1f} MB</span>')

    # ── 크로스페이드 산식 표 ──
    wav_sum = sum(s["dur"] for s in d["scenes"])
    n = len(d["scenes"])
    rows = "".join(
        f'<tr><td>씬 {s["n"]:02d}</td><td class="num">{s["dur"]:.2f}</td>'
        f'<td class="num">{s["start"]:.2f}</td>'
        f'<td class="num">{s["start"]+s["dur"]:.2f}</td>'
        f'<td class="num">{len(s["cues"])}</td></tr>' for s in d["scenes"])
    calc = f"""<table class="calc">
  <tr><th>씬</th><th>wav 길이(초)</th><th>시작(초)</th><th>끝(초)</th><th>자막 큐</th></tr>
  {rows}
  <tr><td>합계</td><td class="num">{wav_sum:.2f}</td><td class="num">—</td>
      <td class="num">{d['total']:.2f}</td>
      <td class="num">{sum(len(s['cues']) for s in d['scenes'])}</td></tr>
</table>
<p class="note">wav 합계 <b>{wav_sum:.2f}초</b>에서 씬 경계 {n-1}곳 × {CROSSFADE}초를 빼면
  <b>{d['total']:.2f}초</b>({d['total']/60:.2f}분)입니다. 자막 타임코드도 같은 식으로 보정되므로
  이 값이 어긋나면 자막이 씬마다 누적으로 밀립니다.</p>"""

    # ── 전역 자막 큐 (실시간 하이라이트용) ──
    gcues, gi = [], 0
    for s in d["scenes"]:
        for c in s["cues"]:
            gcues.append({"s": s["start"] + c["start"], "e": s["start"] + c["end"],
                          "t": c["text"], "i": gi, "scene": s["n"]})
            gi += 1

    # ── 씬 카드 ──
    secs = []
    for s in d["scenes"]:
        changed = s["spoken"].strip() != s["narration"].strip()
        pron = (f'<div class="row"><span class="lbl">발음대본</span>'
                f'<div>{mark_diff(s["narration"], s["spoken"])}</div></div>'
                if changed else
                '<div class="row"><span class="lbl">발음대본</span>'
                '<div class="same">음성대본과 동일 — 발음사전에 걸린 단어가 없습니다.</div></div>')
        cue_rows = "".join(
            f'<div class="cue" id="g{g["i"]}" data-t="{g["s"]:.3f}" title="클릭하면 이 지점으로 이동">'
            f'<span class="ts">{mmss(g["s"])} → {mmss(g["e"])}</span>'
            f'<span>{esc(g["t"])}</span></div>'
            for g in gcues if g["scene"] == s["n"]) or \
            '<div class="cue"><span class="ts">—</span><span>자막 없음</span></div>'
        secs.append(f"""
<section class="scene" id="s{s['n']}" data-n="{s['n']}">
  <div class="sh"><b>씬 {s['n']:02d}</b><span>{esc(s['title'] or '(제목 없음)')}</span>
    <span class="badge">{s['dur']:.2f}초</span>
    <span class="badge">시작 {mmss(s['start'])}</span>
    <div class="tools">
      {'<button data-seek="%.3f">영상에서 보기</button>' % s['start'] if d['video'] else ''}
    </div></div>
  <div class="body">
    <div class="pane">
      <h4>씬 음성 <span class="n">{esc(Path(s['wav']).name) if s['wav'] else '없음'}
        · {s['wav_mb']:.1f} MB · {s['dur']:.2f}초</span></h4>
      {'<audio controls preload="none" src="%s"></audio>' % s['wav'] if s['wav']
       else '<div class="txt">wav 없음</div>'}
    </div>
    <div class="pane"><h4>발음 <span class="n">TTS 직전에 발음사전이 적용된다 · 자막에는 미적용</span></h4>
      <div class="pron">
        <div class="row"><span class="lbl">음성대본</span><div>{esc(s['narration'])}</div></div>
        {pron}
      </div></div>
    <div class="pane"><h4>자막 타임라인 <span class="n">{len(s['cues'])}큐 · 전역 시각 · 클릭하면 이동</span></h4>
      <div class="txt cues">{cue_rows}</div></div>
  </div>
</section>""")

    video = (f'<video controls preload="metadata" src="{d["video"]}"></video>'
             if d["video"] else "")
    live = ('<div class="live"><div class="cur" id="livecur">—</div>'
            '<div class="nxt" id="livenxt"></div></div>') if d["video"] else ""
    body = f"""
<p class="note">음성은 <b>씬 단위 wav</b>로 만들어져 번들에 담기고, 씬 경계마다 {CROSSFADE}초씩
  겹쳐 이어집니다. 씬 하나만 다시 합성해도 나머지는 그대로 쓰입니다.</p>
{video}{live}
<div class="tree">{chr(10).join(tree_lines)}</div>
{calc}
{''.join(secs)}"""
    cues_js = "window.__CUES__=" + json.dumps(
        [{"s": round(g["s"], 3), "e": round(g["e"], 3), "t": g["t"]} for g in gcues],
        ensure_ascii=False) + ";"
    return _shell(d, ws, "bundle", "", body, extra_js=cues_js + JS_BUNDLE_PAGE)


def main() -> int:
    lec = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    d = collect(lec)
    ws = scan_workspace(lec)
    (lec / "script.html").write_text(render_script(d, ws), encoding="utf-8")
    (lec / "bundle.html").write_text(render_bundle(d, ws, lec), encoding="utf-8")
    old = lec / "viewer.html"
    if old.exists():
        old.unlink()
    print(f"씬 {len(d['scenes'])}개 · 총 {d['total']/60:.2f}분 · 자막 "
          f"{sum(len(s['cues']) for s in d['scenes'])}큐")
    print(f"-> {lec / 'script.html'}   (최종 대본)")
    print(f"-> {lec / 'bundle.html'}   (번들 구성)")
    return 0


if __name__ == "__main__":
    sys.exit(main())


CSS_PRINT = """
/* ── 인쇄 (강사 확정용) ──────────────────────────────────────────────
   화면용 장치는 전부 감추고, 씬 하나가 한 덩어리로 넘어가게 한다.
   강사가 종이에 확인·메모하는 용도라 확인란과 여백을 인쇄에서만 붙인다. */
.only-print{display:none}
@media print{
  @page{size:A4;margin:14mm 12mm 16mm}
  html,body{background:#fff!important;color:#000!important;font-size:10.5pt}
  header,nav,#panel,#scrim,.pagetabs,.tools,.note,video,.live,
  button,select,textarea,audio,.step-act{display:none!important}
  .wrap{display:block!important}
  main{padding:0!important;max-width:none!important}
  .only-print{display:block}

  /* 씬 하나가 페이지 중간에서 잘리지 않게 */
  section.scene{break-inside:avoid;page-break-inside:avoid;
    box-shadow:none!important;border:1px solid #bbb!important;border-radius:6px!important;
    margin:0 0 6mm!important;background:#fff!important}
  .sh{border-bottom:1px solid #ddd!important;padding:3mm 4mm!important}
  .sh b{font-size:11.5pt}
  .badge{border:1px solid #ccc!important;background:#fff!important;color:#333!important}
  .body{padding:3mm 4mm 4mm!important;gap:3mm!important}

  /* 슬라이드는 작게, 대본이 주인공 */
  img.slide{max-width:74mm!important;border:1px solid #ddd!important;border-radius:3px!important}
  .cols{grid-template-columns:1fr 1fr!important;gap:4mm!important}
  .pane h4{color:#555!important;font-size:8.5pt!important;margin-bottom:1.5mm!important}
  .txt{background:#fff!important;border:1px solid #ddd!important;border-radius:3px!important;
    padding:2.5mm 3mm!important;font-size:10pt!important;line-height:1.72!important;
    color:#000!important}
  .txt.cues{background:#fff!important;font-size:9pt!important}

  /* 확인란 — 종이에서 강사가 체크하고 고칠 곳 */
  .chk{display:flex;gap:4mm;align-items:flex-start;
    margin-top:2.5mm;padding-top:2.5mm;border-top:1px dashed #ccc}
  .chk .box{flex:0 0 auto;font-size:9pt;color:#333;white-space:nowrap}
  .chk .box i{display:inline-block;width:3.4mm;height:3.4mm;
    border:1px solid #666;border-radius:1px;vertical-align:-0.4mm;margin-right:1.5mm}
  .chk .memo{flex:1 1 auto;border-bottom:1px solid #ddd;min-height:9mm}

  /* 표제 — 매 인쇄물의 첫 장에 한 번 */
  .print-head{margin:0 0 6mm;padding-bottom:3mm;border-bottom:2px solid #333}
  .print-head h2{margin:0;font-size:15pt}
  .print-head .meta{color:#444;font-size:9.5pt;margin-top:1.5mm}
  .print-head .sign{margin-top:4mm;font-size:9.5pt;color:#333}
  .print-head .sign span{display:inline-block;min-width:52mm;
    border-bottom:1px solid #999;margin-left:2mm}
}
"""

JS_PRINT = r"""
const pb = document.getElementById("printbtn");
if (pb) pb.onclick = () => window.print();
"""
