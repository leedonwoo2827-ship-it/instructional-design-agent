# -*- coding: utf-8 -*-
"""주차별 산출물 아카이브 — 파일시스템이 source of truth.

DB(core/db.py)는 강좌 목록·강의 정보(form)만 들고 있고, 강의계획서와 주차별
산출물은 전부 여기 폴더 구조에 남는다. 주차를 오가도 아무것도 덮어써지지 않고,
덱은 빌드할 때마다 v1·v2·v3… 로 전부 보존된다.

폴더는 **파이프라인 단계와 1:1** 이다. 어느 단계의 산출물인지 경로만 봐도 알 수
있어야 하고, 한 단계를 다시 돌려도 다른 단계의 결과가 섞이지 않는다.

    workspace/
      01_교육방법및교육공학/          ← 강좌
        project.json                  강좌 메타(이름·주차수·form)
        00_강의계획서/                ← 강좌 루트 산출물
          강의계획서.md
        03/                           ← 3주차
          00_교재/       교재.md · 대화.json
          01_개요/       슬라이드개요.md · 대화.json
          02_초안/       슬라이드플랜.json · 슬라이드_v1.pptx …
          03_비주얼/     assets/(수집 사진) · 이미지출처.txt · 슬라이드_v1.pptx …
          04_씬프롬프트/ 이미지프롬프트.json
          05_합치기/     assets/(내가 만든 이미지) · 슬라이드_v1.pptx …
        01/ 02/ … 15/

단계마다 버전 계열이 따로다. '02_초안/슬라이드_v2.pptx' 는 초안을 두 번 설계했다는
뜻이고, '05_합치기/슬라이드_v3.pptx' 는 이미지를 세 번 합쳤다는 뜻이다.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(os.environ.get("IDA_WORKSPACE")
            or (Path(__file__).resolve().parent.parent / "workspace"))

SYLLABUS_DIR = "00_강의계획서"
SYLLABUS_MD = "강의계획서.md"
ASSETS = "assets"

# 단계 키 → (폴더명, 사람이 읽는 이름)
STEPS: Dict[str, Tuple[str, str]] = {
    "doc":     ("00_교재", "교재"),
    "outline": ("01_개요", "개요"),
    "draft":   ("02_초안", "초안 PPT"),
    "visual":  ("03_비주얼", "비주얼 정돈"),
    "prompts": ("04_씬프롬프트", "씬 프롬프트"),
    "merge":   ("05_합치기", "이미지 합치기"),
    "video":   ("06_영상", "영상"),
}
# 덱(.pptx)이 나오는 단계 — 최신순으로 볼 때의 우선순위이기도 하다.
# ★ video 는 .pptx 를 만들지 않으므로 여기 넣지 않는다(덱 버전 계산이 깨진다).
DECK_STEPS = ("merge", "visual", "draft")

F_DOC = "교재.md"
F_OUTLINE = "슬라이드개요.md"
F_PLAN = "슬라이드플랜.json"
F_PROMPT = "이미지프롬프트.json"
F_MSGS = "대화.json"
F_CREDITS = "이미지출처.txt"
# 영상 — 나레이션 대본이 유일한 진실이다. 앱과 Claude Code 창이 같은 파일을 고친다.
F_SCRIPT = "나레이션.json"
F_JOB = "job.json"
F_PROGRESS = "progress.json"
VIDEO_SLIDES = "슬라이드"      # 06_영상/슬라이드/001.png  (PowerPoint 로 뽑은 PNG)
VIDEO_BUNDLE = "번들"          # 06_영상/번들/chNN/…       (chodangi 번들)
VIDEO_OUT = "완성"             # 06_영상/완성/영상_v1.mp4

_IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")
_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def slug(name: str, limit: int = 40) -> str:
    """폴더명으로 안전한 이름. 한글은 그대로 둔다(사람이 폴더를 직접 여니까)."""
    s = _BAD.sub("", (name or "").strip())
    s = re.sub(r"\s+", "", s).strip(". ")
    return (s or "강의")[:limit]


# ── 경로 ──────────────────────────────────────────────────────────────────
def project_dir(pid: int, name: str, *, create: bool = True) -> Path:
    """강좌 폴더. 같은 pid 폴더가 이미 있으면(이름이 바뀌었어도) 그것을 쓴다."""
    ROOT.mkdir(parents=True, exist_ok=True)
    prefix = f"{int(pid):02d}_"
    if ROOT.exists():
        for d in sorted(ROOT.iterdir()):
            if d.is_dir() and d.name.startswith(prefix):
                return d
    p = ROOT / f"{prefix}{slug(name)}"
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def week_dir(pid: int, name: str, week: int, *, create: bool = True) -> Path:
    p = project_dir(pid, name, create=create) / f"{int(week):02d}"
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def step_dir(pid: int, name: str, week: int, step: str, *, create: bool = True) -> Path:
    folder = STEPS[step][0]
    p = week_dir(pid, name, week, create=create) / folder
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def assets_dir(pid: int, name: str, week: int, step: str, *, create: bool = True) -> Path:
    """단계별 에셋 폴더. visual=수집 사진, merge=내가 만들어 넣는 이미지."""
    p = step_dir(pid, name, week, step, create=create) / ASSETS
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def step_label(step: str) -> str:
    return STEPS[step][1]


# ── 읽기/쓰기 헬퍼 ─────────────────────────────────────────────────────────
def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""


def _write_text(p: Path, text: str) -> None:
    """빈 내용으로 기존 산출물을 지우지 않는다(실수로 날리는 사고 방지)."""
    if not (text or "").strip():
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _read_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _write_json(p: Path, obj) -> None:
    if obj in (None, "", [], {}):
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


# ── 강좌 메타·강의계획서 ───────────────────────────────────────────────────
def save_project_meta(pid: int, name: str, form: Dict[str, Any]) -> None:
    d = project_dir(pid, name)
    _write_json(d / "project.json", {
        "id": int(pid), "name": name,
        "weeks": int((form or {}).get("weeks", 15) or 15),
        "form": form or {}, "updated_at": _now(),
    })


def save_syllabus(pid: int, name: str, md: str) -> None:
    _write_text(project_dir(pid, name) / SYLLABUS_DIR / SYLLABUS_MD, md)


def load_syllabus(pid: int, name: str) -> str:
    return _read_text(project_dir(pid, name, create=False) / SYLLABUS_DIR / SYLLABUS_MD)


# ── 주차 산출물 ────────────────────────────────────────────────────────────
def save_week(pid: int, name: str, week: int, *,
              doc_md: Optional[str] = None, ppt_md: Optional[str] = None,
              plan: Optional[list] = None, img_prompt: Optional[str] = None,
              doc_msgs: Optional[list] = None,
              ppt_msgs: Optional[list] = None) -> None:
    """주어진 항목만 저장. None 은 '건드리지 않음', 빈 값은 무시."""
    if doc_md is not None:
        _write_text(step_dir(pid, name, week, "doc") / F_DOC, doc_md)
    if doc_msgs is not None:
        _write_json(step_dir(pid, name, week, "doc") / F_MSGS, doc_msgs)
    if ppt_md is not None:
        _write_text(step_dir(pid, name, week, "outline") / F_OUTLINE, ppt_md)
    if ppt_msgs is not None:
        _write_json(step_dir(pid, name, week, "outline") / F_MSGS, ppt_msgs)
    if plan is not None:
        _write_json(step_dir(pid, name, week, "draft") / F_PLAN, _clean_plan(plan))
    if img_prompt is not None and str(img_prompt).strip():
        _write_text(step_dir(pid, name, week, "prompts") / F_PROMPT, img_prompt)


# ── 영상 ───────────────────────────────────────────────────────────────────
def video_dir(pid: int, name: str, week: int, *, create: bool = True) -> Path:
    return step_dir(pid, name, week, "video", create=create)


def video_sub(pid: int, name: str, week: int, which: str, *, create: bool = True) -> Path:
    d = video_dir(pid, name, week, create=create) / which
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def script_path(pid: int, name: str, week: int, *, create: bool = False) -> Path:
    return video_dir(pid, name, week, create=create) / F_SCRIPT


def load_script(pid: int, name: str, week: int) -> Dict[str, Any]:
    return _read_json(script_path(pid, name, week), {}) or {}


def save_script(pid: int, name: str, week: int, doc: Dict[str, Any]) -> Path:
    """나레이션 대본 저장 — **내용이 같으면 쓰지 않고, 다르면 .bak 을 남긴다.**

    이 파일은 앱과 Claude Code 창이 함께 쓴다. 그냥 덮어쓰면 창에서 다듬은 내용을
    날린다. 그래서 무조건 쓰기가 아니라 비교 후 쓰기다.
    """
    p = script_path(pid, name, week, create=True)
    new = json.dumps(doc, ensure_ascii=False, indent=2)
    if p.is_file():
        old = _read_text(p)
        if old == new:
            return p
        (p.parent / (F_SCRIPT + ".bak")).write_text(old, encoding="utf-8")
    tmp = p.with_suffix(".tmp")
    tmp.write_text(new, encoding="utf-8")
    tmp.replace(p)
    return p


def video_versions(pid: int, name: str, week: int) -> List[Path]:
    """완성 영상 목록(오름차순). 덱과 달리 .mp4 라 DECK_STEPS 와 섞지 않는다."""
    d = video_sub(pid, name, week, VIDEO_OUT, create=False)
    if not d.is_dir():
        return []
    out = []
    for p in d.glob("*.mp4"):
        m = re.search(r"_v(\d+)\.mp4$", p.name)
        out.append((int(m.group(1)) if m else 0, p))
    return [p for _, p in sorted(out)]


def next_video_version(pid: int, name: str, week: int) -> int:
    vs = video_versions(pid, name, week)
    if not vs:
        return 1
    m = re.search(r"_v(\d+)\.mp4$", vs[-1].name)
    return (int(m.group(1)) if m else 0) + 1


def find_video(pid: int, name: str, week: int, filename: str) -> Optional[Path]:
    """다운로드용 — 경로 탈출을 막는다(deck 쪽과 같은 규칙)."""
    d = video_sub(pid, name, week, VIDEO_OUT, create=False)
    p = (d / filename).resolve()
    try:
        p.relative_to(d.resolve())
    except ValueError:
        return None
    return p if p.is_file() else None


def _clean_plan(plan: list) -> list:
    """저장용 정리 — 런타임 내부 키(_idx·_photo_ord 등)는 뺀다."""
    out = []
    for s in plan or []:
        if isinstance(s, dict):
            out.append({k: v for k, v in s.items()
                        if not (k.startswith("_") and k != "_credit_short")})
        else:
            out.append(s)
    return out


def load_week(pid: int, name: str, week: int) -> Dict[str, Any]:
    """주차 산출물 읽기. 새 단계 폴더를 먼저 보고, 없으면 평면 구조를 읽는다.

    ★ 이관 여부와 무관하게 읽혀야 한다. 예전에는 마이그레이션이 특정 엔드포인트
      (GET /api/projects/{id})에서만 돌아서, 클라이언트가 /weeks/N 을 먼저 부르면
      실제로 있는 9천 자 교재를 '빈 문서' 로 읽었다. 이관은 정리 작업일 뿐이고
      읽기가 그것에 의존하면 안 된다.
    """
    sd = lambda step: step_dir(pid, name, week, step, create=False)  # noqa: E731
    wd = week_dir(pid, name, week, create=False)
    old_msgs = _read_json(wd / "05_대화.json", {}) or {}

    def text(new: Path, old_name: str) -> str:
        return _read_text(new) or _read_text(wd / old_name)

    return {
        "doc_md": text(sd("doc") / F_DOC, "01_교재.md"),
        "ppt_md": text(sd("outline") / F_OUTLINE, "02_슬라이드개요.md"),
        "plan": (_read_json(sd("draft") / F_PLAN, None)
                 or _read_json(wd / "03_슬라이드플랜.json", None)),
        "img_prompt": text(sd("prompts") / F_PROMPT, "04_이미지프롬프트.json"),
        "doc_msgs": (_read_json(sd("doc") / F_MSGS, []) or old_msgs.get("doc") or []),
        "ppt_msgs": (_read_json(sd("outline") / F_MSGS, []) or old_msgs.get("ppt") or []),
    }


# ── 덱 버전 (단계별로 계열이 따로) ─────────────────────────────────────────
_VER_RE = re.compile(r"_v(\d+)\.pptx$", re.I)


def deck_versions(pid: int, name: str, week: int,
                  step: Optional[str] = None) -> List[Path]:
    """한 단계의 덱 버전(오름차순). step=None 이면 덱이 나오는 모든 단계를 합친다."""
    steps = [step] if step else list(DECK_STEPS)
    out: List[Path] = []
    for st in steps:
        d = step_dir(pid, name, week, st, create=False)
        if not d.is_dir():
            continue
        files = [f for f in d.iterdir() if _VER_RE.search(f.name)]
        out += sorted(files, key=lambda f: int(_VER_RE.search(f.name).group(1)))
    return out


def next_deck_version(pid: int, name: str, week: int, step: str) -> int:
    vs = deck_versions(pid, name, week, step)
    if not vs:
        return 1
    return max(int(_VER_RE.search(f.name).group(1)) for f in vs) + 1


def save_deck(pid: int, name: str, week: int, step: str, data: bytes, *,
              stem: str = "슬라이드", credits: str = "") -> Path:
    """덱을 그 단계 폴더의 다음 버전으로 저장. 기존 버전은 절대 지우지 않는다."""
    v = next_deck_version(pid, name, week, step)
    d = step_dir(pid, name, week, step)
    p = d / f"{slug(stem, 60)}_v{v}.pptx"
    p.write_bytes(data)
    if credits.strip():
        (d / F_CREDITS).write_text(credits, encoding="utf-8")
    return p


def find_deck(pid: int, name: str, week: int, filename: str) -> Optional[Path]:
    """파일명으로 덱을 찾는다(단계 폴더를 훑는다). 경로 탈출은 막는다."""
    if "/" in filename or "\\" in filename or ".." in filename:
        return None
    for st in DECK_STEPS:
        f = step_dir(pid, name, week, st, create=False) / filename
        if f.is_file():
            return f
    return None


def latest_credits(pid: int, name: str, week: int) -> Optional[Path]:
    for st in DECK_STEPS:
        f = step_dir(pid, name, week, st, create=False) / F_CREDITS
        if f.is_file():
            return f
    return None


# ── 자동 수집 사진 (03_비주얼/assets) ──────────────────────────────────────
def save_photos(pid: int, name: str, week: int, images: Dict[int, bytes], *,
                credits: str = "") -> int:
    """수집한 사진을 03_비주얼/assets/NNN.jpg 로 보관(키는 0-based 슬라이드 인덱스).

    파일로 남기는 이유: 이걸 안 남기면 '이미지 합치기' 로 재빌드할 때 자동 사진을
    다시 받아야 하고(느리고 결과도 달라진다), 빼먹으면 자동 사진이 통째로 사라진
    덱이 나온다 — 실제로 그런 버그가 있었다.
    파일명 규칙은 05_합치기/assets 와 같아서 같은 스캐너로 읽는다.
    """
    d = assets_dir(pid, name, week, "visual")
    for f in d.glob("*"):
        if f.is_file():
            f.unlink()
    n = 0
    for idx, blob in (images or {}).items():
        ext = ".png" if bytes(blob[:8]).startswith(b"\x89PNG") else ".jpg"
        (d / f"{int(idx) + 1:03d}{ext}").write_bytes(blob)
        n += 1
    if credits.strip():
        (step_dir(pid, name, week, "visual") / F_CREDITS
         ).write_text(credits, encoding="utf-8")
    return n


def _count_images(d: Path) -> int:
    if not d.is_dir():
        return 0
    return sum(1 for f in d.iterdir() if f.is_file() and f.suffix.lower() in _IMG_EXT)


def count_photos(pid: int, name: str, week: int) -> int:
    return _count_images(assets_dir(pid, name, week, "visual", create=False))


def count_my_images(pid: int, name: str, week: int) -> int:
    return _count_images(assets_dir(pid, name, week, "merge", create=False))


# ── 상태(주차 목록용) ──────────────────────────────────────────────────────
def week_status(pid: int, name: str, week: int) -> Dict[str, Any]:
    """주차 진행 상태. 아직 마이그레이션되지 않은 평면 구조도 함께 센다.

    옛 파일을 세지 않으면, 이관 전에는 실제로 있는 교재·개요가 '없음' 으로 보인다
    (고아 폴더 목록에서 교재 0 으로 나오는데 복구하니 9천 자가 있었다).
    """
    sd = lambda step: step_dir(pid, name, week, step, create=False)  # noqa: E731
    wd = week_dir(pid, name, week, create=False)
    old = lambda fn: (wd / fn).is_file()  # noqa: E731
    return {
        "week": int(week),
        "doc": (sd("doc") / F_DOC).is_file() or old("01_교재.md"),
        "ppt": (sd("outline") / F_OUTLINE).is_file() or old("02_슬라이드개요.md"),
        "plan": (sd("draft") / F_PLAN).is_file() or old("03_슬라이드플랜.json"),
        "prompt": (sd("prompts") / F_PROMPT).is_file() or old("04_이미지프롬프트.json"),
        "photos": count_photos(pid, name, week) or _count_images(wd / "photos"),
        "deck": len(deck_versions(pid, name, week)) or _count_decks(wd / "out"),
        "images": count_my_images(pid, name, week) or _count_images(wd / "images"),
        "script": (sd("video") / F_SCRIPT).is_file(),
        "video": len(video_versions(pid, name, week)),
    }


def _count_decks(d: Path) -> int:
    if not d.is_dir():
        return 0
    return sum(1 for f in d.iterdir() if f.is_file() and _VER_RE.search(f.name))


def all_status(pid: int, name: str, n_weeks: int = 15) -> List[Dict[str, Any]]:
    return [week_status(pid, name, w) for w in range(1, int(n_weeks) + 1)]


# ── 마이그레이션 ───────────────────────────────────────────────────────────
# 이전 평면 구조 → 단계 폴더 구조. 파일명이 그대로 남아 있으면 옮긴다.
_OLD_FILES = {
    "01_교재.md": ("doc", F_DOC),
    "02_슬라이드개요.md": ("outline", F_OUTLINE),
    "03_슬라이드플랜.json": ("draft", F_PLAN),
    "04_이미지프롬프트.json": ("prompts", F_PROMPT),
}


def migrate_layout(pid: int, name: str, n_weeks: int = 15) -> int:
    """평면 구조(01_교재.md · images/ · out/)를 단계 폴더로 옮긴다. 옮긴 항목 수."""
    moved = 0
    for week in range(1, int(n_weeks) + 1):
        wd = week_dir(pid, name, week, create=False)
        if not wd.is_dir():
            continue
        for old, (step, newname) in _OLD_FILES.items():
            src = wd / old
            if src.is_file():
                dst = step_dir(pid, name, week, step) / newname
                if not dst.exists():
                    shutil.move(str(src), str(dst))
                    moved += 1
        # 대화 기록은 {doc, ppt} 한 파일에 있었다 → 단계별로 쪼갠다
        old_msgs = wd / "05_대화.json"
        if old_msgs.is_file():
            cur = _read_json(old_msgs, {}) or {}
            if cur.get("doc"):
                _write_json(step_dir(pid, name, week, "doc") / F_MSGS, cur["doc"])
            if cur.get("ppt"):
                _write_json(step_dir(pid, name, week, "outline") / F_MSGS, cur["ppt"])
            old_msgs.unlink()
            moved += 1
        # images/ → 05_합치기/assets, photos/ → 03_비주얼/assets
        for old_dir, step in (("images", "merge"), ("photos", "visual")):
            src = wd / old_dir
            if src.is_dir():
                dst = assets_dir(pid, name, week, step)
                for f in src.iterdir():
                    if f.is_file() and not (dst / f.name).exists():
                        shutil.move(str(f), str(dst / f.name))
                        moved += 1
                try:
                    src.rmdir()
                except OSError:
                    pass
        # out/*.pptx → 어느 단계인지 알 수 없다. 초안 계열로 보내 기록을 지키지 않는다.
        old_out = wd / "out"
        if old_out.is_dir():
            dst = step_dir(pid, name, week, "draft")
            for f in sorted(old_out.iterdir()):
                if f.is_file() and not (dst / f.name).exists():
                    shutil.move(str(f), str(dst / f.name))
                    moved += 1
            try:
                old_out.rmdir()
            except OSError:
                pass
    return moved


def migrate_project(pid: int, name: str, *, form: Dict[str, Any],
                    syllabus_md: str, script_week: int,
                    script_doc_md: str, script_ppt_md: str,
                    script_doc_msgs: list, script_ppt_msgs: list) -> bool:
    """DB 1행 모델에 남아 있던 산출물을 폴더로 옮긴다. 이미 있으면 건드리지 않는다."""
    wrote = False
    d = project_dir(pid, name)
    if not (d / "project.json").is_file():
        save_project_meta(pid, name, form)
        wrote = True
    if syllabus_md.strip() and not (d / SYLLABUS_DIR / SYLLABUS_MD).is_file():
        save_syllabus(pid, name, syllabus_md)
        wrote = True

    # 평면 구조 → 단계 폴더 (이전 버전으로 만든 폴더가 있으면)
    nw = int((form or {}).get("weeks", 15) or 15)
    if migrate_layout(pid, name, nw):
        wrote = True

    wk = int(script_week or 1)
    if script_doc_md.strip() and not (
            step_dir(pid, name, wk, "doc", create=False) / F_DOC).is_file():
        save_week(pid, name, wk, doc_md=script_doc_md, doc_msgs=script_doc_msgs)
        wrote = True
    if script_ppt_md.strip() and not (
            step_dir(pid, name, wk, "outline", create=False) / F_OUTLINE).is_file():
        save_week(pid, name, wk, ppt_md=script_ppt_md, ppt_msgs=script_ppt_msgs)
        wrote = True
    return wrote


def list_project_dirs() -> List[Dict[str, Any]]:
    """workspace 안의 강좌 폴더 목록(project.json 기준). DB 와 대조해 고아를 찾는다.

    파일이 source of truth 이므로, DB 행이 없어도 폴더는 그대로 쓸 수 있어야 한다.
    """
    out: List[Dict[str, Any]] = []
    if not ROOT.is_dir():
        return out
    for d in sorted(ROOT.iterdir()):
        if not d.is_dir():
            continue
        m = re.match(r"^(\d+)_", d.name)
        if not m:
            continue
        meta = _read_json(d / "project.json", {}) or {}
        pid = int(meta.get("id") or m.group(1))
        nw = int(meta.get("weeks", 15) or 15)
        name = meta.get("name") or d.name[len(m.group(0)):]
        stats = [week_status(pid, name, w) for w in range(1, nw + 1)]
        out.append({
            "id": pid, "name": name, "weeks": nw, "dir": str(d),
            "form": meta.get("form") or {},
            "has_syllabus": (d / SYLLABUS_DIR / SYLLABUS_MD).is_file(),
            "done_doc": sum(1 for s in stats if s["doc"]),
            "done_ppt": sum(1 for s in stats if s["ppt"]),
            "done_deck": sum(1 for s in stats if s["deck"]),
        })
    return out


def delete_project_dir(pid: int, name: str) -> None:
    d = project_dir(pid, name, create=False)
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
