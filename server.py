# -*- coding: utf-8 -*-
"""교수설계 가이드 에이전트 — FastAPI 로컬 콘솔.

Streamlit 을 버리고 정적 프런트(static/)와 JSON API 로 갈아탄 이유:
  · 목록은 패널(위층), 작업은 바탕(아래층) — 진짜 오버레이가 필요하다.
    Streamlit 의 st.dialog 는 fragment 라 바탕을 유지한 상호작용이 안 된다.
  · 생성이 몇 분 걸린다. rerun 모델에서는 진행 중 화면을 잃는다.
  · 좌측 레일·카드 홈은 CSS 로 우겨넣는 게 아니라 그냥 마크업이어야 한다.

파이썬 코어(core/*)는 그대로 재사용한다 — 바뀐 건 껍데기뿐이다.

실행: run.bat  (uvicorn server:app --port 8701)
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import queue
import re
import threading
import traceback
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv(encoding="utf-8-sig")

from fastapi import Body, FastAPI, HTTPException, Query  # noqa: E402
from fastapi.responses import (  # noqa: E402
    FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles  # noqa: E402

from core import db  # noqa: E402
from core import palette  # noqa: E402
from core import deck_builder  # noqa: E402
from core import image_merge  # noqa: E402
from core import image_search  # noqa: E402
from core import llm as llm_mod  # noqa: E402
from core import pptx_font  # noqa: E402
from core import prompts  # noqa: E402
from core import user_settings as settings_mod  # noqa: E402
from core import video as video_mod  # noqa: E402
from core import workspace as ws  # noqa: E402
from core.pptx_export import outline_to_pptx  # noqa: E402
from core.viz import bloom_counts  # noqa: E402

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
ASSETS_DIR = ROOT / "assets"
TEMPLATE_PATH = ASSETS_DIR / "company_template.pptx"
LOGO_PATH = ASSETS_DIR / "logo.png"     # 로고1 — 우상단(기본)
LOGO2_PATH = ASSETS_DIR / "logo2.png"   # 로고2 — 좌상단(있을 때만)
LOGO_SLOTS = {1: LOGO_PATH, 2: LOGO2_PATH}

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

GEN_MAX_TOKENS = 16000
SLIDE_LIST_TOKENS = 3000
SLIDE_EXPAND_TOKENS = 24000
# (구) 1시간당 슬라이드 수. 실제 장수는 core/video.py 의 목표 시간 역산을 쓴다 —
# 영상 길이가 곧 학습 시간이고, 한 장에 머무는 시간(1.25분)이 장수를 정한다.
SLIDES_PER_HOUR = 20

WEEK_CHOICES = [8, 10, 13, 15, 16]
MODE_CHOICES = ["대면", "온라인(실시간)", "온라인(비동기·동영상)", "혼합(블렌디드)", "플립러닝"]

REFINE_TMPL = (
    "다음 요청대로 수정하여, 수정된 문서 전체를 다시 출력해 주세요. "
    "수정 시에도 학습목표 정렬 원칙(측정 가능 동사, 목표–평가–활동 인지수준 일치)을 유지하세요.\n\n요청: {req}"
)

db.init_db()
app = FastAPI(title="교수설계 가이드 에이전트", docs_url=None, redoc_url=None)


# ══ 공통 ═══════════════════════════════════════════════════════════════════
def template_arg() -> Optional[str]:
    return str(TEMPLATE_PATH) if TEMPLATE_PATH.exists() else None


def logo_arg() -> Optional[str]:
    return str(LOGO_PATH) if LOGO_PATH.exists() else None


def logo2_arg() -> Optional[str]:
    return str(LOGO2_PATH) if LOGO2_PATH.exists() else None


def settings() -> settings_mod.Settings:
    return settings_mod.load()


def provider(*, no_think: bool = False):
    s = settings()
    p = llm_mod.build_provider(s)
    if no_think:
        # 구조화(JSON) 작업은 추론 모델이 형식을 깨뜨린다.
        p.model = s.model.replace("-think", "")
    return p


def need_project(pid: int) -> Dict[str, Any]:
    p = db.load_project(pid)
    if not p:
        raise HTTPException(404, "프로젝트를 찾을 수 없습니다.")
    return p


def n_weeks(form: Dict[str, Any]) -> int:
    try:
        return int((form or {}).get("weeks", 15) or 15)
    except (TypeError, ValueError):
        return 15


def session_hours(form: Dict[str, Any]) -> int:
    try:
        return int((form or {}).get("hours", 2) or 2)
    except (TypeError, ValueError):
        return 2


def deck_stem(form: Dict[str, Any], week: int, kind: str = "슬라이드") -> str:
    title = ((form or {}).get("title") or "강의").strip() or "강의"
    return f"{title}_{week}주차_{kind}"


_WEEK_ROW = re.compile(r"(?m)^\s*\|?\s*(\d{1,2})\s*(?:주차?)?\s*\|(.+)$")


def week_titles(syllabus_md: str, weeks: int) -> Dict[int, str]:
    """강의계획서 주차 표에서 {주차: 주제}. 실패해도 빈 dict(목록은 그대로 동작)."""
    out: Dict[int, str] = {}
    for m in _WEEK_ROW.finditer(syllabus_md or ""):
        try:
            n = int(m.group(1))
        except ValueError:
            continue
        if not (1 <= n <= weeks) or n in out:
            continue
        cells = [c.strip().strip("*") for c in m.group(2).split("|")]
        cells = [c for c in cells if c and not re.fullmatch(r"[-—:\s]*", c)]
        if cells:
            out[n] = re.sub(r"\s+", " ", cells[0])[:60]
    return out


# ══ SSE ════════════════════════════════════════════════════════════════════
def sse(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


def stream_job(work: Callable[[Callable[[str, Any], None]], None]) -> StreamingResponse:
    """work(emit) 을 워커 스레드에서 돌리고 emit 을 SSE 로 흘린다.

    LLM 호출이 동기(openai SDK)라 스레드로 돌리고 큐로 받는다. 이렇게 하면
    브라우저가 창을 닫아도 워커는 끝까지 돌아 파일 저장이 완료된다.
    """
    q: "queue.Queue[Optional[bytes]]" = queue.Queue()

    def emit(event: str, data: Any = None) -> None:
        q.put(sse(event, data))

    def runner() -> None:
        try:
            work(emit)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            q.put(sse("error", {"message": f"{type(e).__name__}: {e}"}))
        finally:
            q.put(None)

    threading.Thread(target=runner, daemon=True).start()

    async def gen() -> Iterator[bytes]:
        loop = asyncio.get_running_loop()
        while True:
            chunk = await loop.run_in_executor(None, q.get)
            if chunk is None:
                break
            yield chunk

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive",
    })


def stream_llm(emit, system: str, messages: List[Dict], *, label: str,
               max_tokens: int) -> str:
    """LLM 스트림을 delta 이벤트로 흘리며 전체 텍스트를 모은다."""
    s = settings()
    p = llm_mod.build_provider(s)
    mt = max(max_tokens or 0, s.max_tokens)
    print(f"[{label}] 시작 · 모델={s.model} · max_tokens={mt}", flush=True)
    full, last = "", 0
    for delta in p.stream(system, messages, max_tokens=mt, temperature=s.temperature):
        full += delta
        emit("delta", delta)
        if len(full) - last >= 2000:
            last = len(full)
            print(f"[{label}] …{len(full):,}자", flush=True)
    print(f"[{label}] 완료 · {len(full):,}자", flush=True)
    return full


# ══ 설정 ═══════════════════════════════════════════════════════════════════
@app.get("/api/settings")
def api_settings_get():
    s = asdict(settings())
    fs = pptx_font.font_set(ASSETS_DIR)
    return {
        "settings": {**s, "api_key": s["api_key"], "unsplash_key": s["unsplash_key"]},
        "models": settings_mod.models_for(s["provider"]),
        "providers": settings_mod.PROVIDERS,
        "cli_available": llm_mod.cli_available(),
        "week_choices": WEEK_CHOICES,
        "mode_choices": MODE_CHOICES,
        "slides_per_hour": SLIDES_PER_HOUR,
        "font": {"family": fs.regular, "embedded": fs.embedded},
        "template": TEMPLATE_PATH.exists(),
        "logo": LOGO_PATH.exists(),
        "logo2": LOGO2_PATH.exists(),
        "palettes": palette.available(),
        "palette_default": palette.DEFAULT,
        "workspace": str(ws.ROOT),
    }


# ── 로고 ───────────────────────────────────────────────────────────────────
#   1 = 우상단(기본) · 2 = 좌상단. 없으면 그 자리는 그냥 빈다.
#   multipart 대신 data URL 로 받는다 — python-multipart 를 안 늘리려는 것이고,
#   로고는 수십 KB 라 JSON 으로 보내도 부담이 없다.
_LOGO_MIME = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}


@app.get("/api/logo/{slot}")
def api_logo_get(slot: int):
    p = LOGO_SLOTS.get(slot)
    if not p or not p.exists():
        raise HTTPException(404)
    # 브라우저가 옛 로고를 계속 보여 주면 바꾼 걸 확인할 수 없다.
    return FileResponse(str(p), headers={"Cache-Control": "no-store"})


@app.put("/api/logo/{slot}")
def api_logo_put(slot: int, body: Dict[str, Any] = Body(...)):
    p = LOGO_SLOTS.get(slot)
    if not p:
        raise HTTPException(400, "로고 자리는 1(우상단) 또는 2(좌상단) 입니다.")
    url = str(body.get("data_url") or "")
    m = re.match(r"^data:([^;]+);base64,(.+)$", url, re.S)
    if not m:
        raise HTTPException(400, "이미지를 읽지 못했습니다.")
    mime = m.group(1).lower()
    if mime not in _LOGO_MIME:
        raise HTTPException(400, f"PNG · JPG · WEBP 만 됩니다 (받은 형식: {mime})")
    import base64
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except Exception:  # noqa: BLE001
        raise HTTPException(400, "이미지가 깨졌습니다.")
    if len(raw) > 4 * 1024 * 1024:
        raise HTTPException(400, "로고가 4MB 를 넘습니다.")
    # 확장자와 무관하게 .png 로 저장한다 — deck_builder 가 경로 하나만 본다.
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(raw))
        im.load()
        w, h = im.size
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        im.convert("RGBA").save(p, "PNG")
    except ImportError:
        ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        w = h = 0
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"이미지를 열지 못했습니다: {e}")
    return {"ok": True, "slot": slot, "path": str(p), "w": w, "h": h,
            "message": f"로고{slot} 저장 ({w}x{h})" if w else f"로고{slot} 저장"}


@app.delete("/api/logo/{slot}")
def api_logo_del(slot: int):
    p = LOGO_SLOTS.get(slot)
    if not p:
        raise HTTPException(400, "로고 자리는 1 또는 2 입니다.")
    if p.exists():
        p.unlink()
    return {"ok": True, "slot": slot, "message": f"로고{slot} 제거"}


@app.put("/api/settings")
def api_settings_put(body: Dict[str, Any] = Body(...)):
    s = settings()
    before = s.provider
    for k in ("provider", "base_url", "api_key", "model", "unsplash_key"):
        if k in body:
            setattr(s, k, str(body[k] or ""))
    for k in ("max_tokens",):
        if k in body:
            try:
                setattr(s, k, int(body[k]))
            except (TypeError, ValueError):
                pass
    if s.provider not in settings_mod.PROVIDERS:
        s.provider = settings_mod.DEFAULT_PROVIDER
    # 프로바이더를 바꿨는데 모델을 같이 안 보냈으면 그 프로바이더의 기본 모델로 맞춘다.
    if s.provider != before and "model" not in body:
        s.model = settings_mod.default_model_for(s.provider)
    if s.model not in settings_mod.models_for(s.provider):
        s.model = settings_mod.default_model_for(s.provider)
    settings_mod.save(s)
    return {"ok": True}


@app.post("/api/settings/ping")
def api_settings_ping():
    ok, msg = llm_mod.build_provider(settings()).ping()
    return {"ok": ok, "message": msg}


# ══ 프로젝트(강좌) ══════════════════════════════════════════════════════════
@app.get("/api/projects")
def api_projects():
    out = []
    for row in db.list_projects():
        p = db.load_project(row["id"]) or {}
        form = p.get("form") or {}
        nw = n_weeks(form)
        stats = ws.all_status(row["id"], row["name"], nw)
        out.append({
            "id": row["id"], "name": row["name"], "updated_at": row["updated_at"],
            "weeks": nw,
            "has_syllabus": bool((p.get("syllabus_md") or "").strip()
                                 or ws.load_syllabus(row["id"], row["name"]).strip()),
            "done_doc": sum(1 for x in stats if x["doc"]),
            "done_ppt": sum(1 for x in stats if x["ppt"]),
            "done_deck": sum(1 for x in stats if x["deck"]),
        })
    return {"projects": out}


@app.post("/api/projects")
def api_project_create(body: Dict[str, Any] = Body(...)):
    name = (body.get("name") or "").strip() or "새 강의"
    pid = db.create_project(name)
    ws.save_project_meta(pid, name, {})
    return {"id": pid, "name": name}


@app.get("/api/orphans")
def api_orphans():
    """workspace 폴더에만 남은 강좌(DB 행 없음). 되살릴 수 있게 목록으로 노출한다.

    자동으로 되살리지 않는 이유: '목록에서만 제거(파일 보존)' 라는 삭제를 지원하므로,
    자동 복구하면 그 삭제가 불가능해진다. 복구는 사람이 고르게 한다.
    """
    have = {row["id"] for row in db.list_projects()}
    return {"orphans": [d for d in ws.list_project_dirs() if d["id"] not in have]}


@app.post("/api/orphans/{pid}/restore")
def api_orphan_restore(pid: int):
    found = next((d for d in ws.list_project_dirs() if d["id"] == pid), None)
    if not found:
        raise HTTPException(404, "그 id 의 강좌 폴더를 찾을 수 없습니다.")
    ok = db.restore_project(pid, found["name"], form=found["form"],
                            syllabus_md=ws.load_syllabus(pid, found["name"]))
    if not ok:
        raise HTTPException(409, "이미 목록에 있는 강좌입니다.")
    return {"ok": True, "id": pid, "name": found["name"]}


@app.delete("/api/orphans/{pid}")
def api_orphan_delete(pid: int):
    """고아 폴더를 완전히 지운다. DB 행이 있는 강좌는 건드리지 않는다."""
    if any(row["id"] == pid for row in db.list_projects()):
        raise HTTPException(409, "목록에 있는 강좌입니다. 강좌 삭제를 쓰세요.")
    found = next((d for d in ws.list_project_dirs() if d["id"] == pid), None)
    if not found:
        raise HTTPException(404, "그 id 의 강좌 폴더를 찾을 수 없습니다.")
    ws.delete_project_dir(pid, found["name"])
    return {"ok": True, "removed": found["dir"]}


@app.get("/api/projects/{pid}")
def api_project_get(pid: int):
    p = need_project(pid)
    form = p["form"] or {}
    nw = n_weeks(form)
    # 구 DB 1행 모델 산출물을 주차 폴더로 이관(1회, 이미 있으면 무시)
    try:
        if ws.migrate_project(pid, p["name"], form=form, syllabus_md=p["syllabus_md"],
                              script_week=p["script_week"] or 1,
                              script_doc_md=p["script_doc_md"],
                              script_ppt_md=p["script_ppt_md"],
                              script_doc_msgs=p["script_doc_msgs"],
                              script_ppt_msgs=p["script_ppt_msgs"]):
            print(f"[ws] 프로젝트 {pid} 산출물을 주차 폴더로 이관", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[ws] 마이그레이션 건너뜀: {e}", flush=True)

    syl = ws.load_syllabus(pid, p["name"]) or p["syllabus_md"]
    return {
        "id": pid, "name": p["name"], "form": form, "weeks": nw,
        "syllabus_md": syl,
        "bloom": bloom_counts(syl),
        "week_titles": {str(k): v for k, v in week_titles(syl, nw).items()},
        "dir": str(ws.project_dir(pid, p["name"], create=False)),
    }


@app.patch("/api/projects/{pid}")
def api_project_patch(pid: int, body: Dict[str, Any] = Body(...)):
    p = need_project(pid)
    if "name" in body:
        db.rename_project(pid, (body["name"] or "").strip() or "새 강의")
    if "form" in body and isinstance(body["form"], dict):
        db.save_project(pid, form=body["form"])
        ws.save_project_meta(pid, db.load_project(pid)["name"], body["form"])
    if "syllabus_md" in body:
        md = body["syllabus_md"] or ""
        db.save_project(pid, syllabus_md=md, syllabus_msgs=[
            {"role": "user", "content": "현재 강의계획서(직접 편집본)를 기준으로 이어서 작업합니다."},
            {"role": "assistant", "content": md},
        ])
        ws.save_syllabus(pid, p["name"], md)
    return {"ok": True}


@app.delete("/api/projects/{pid}")
def api_project_delete(pid: int, purge: bool = Query(False)):
    p = need_project(pid)
    if purge:
        ws.delete_project_dir(pid, p["name"])
    db.delete_project(pid)
    return {"ok": True, "purged": purge}


# ══ 주차 ═══════════════════════════════════════════════════════════════════
@app.get("/api/projects/{pid}/weeks")
def api_weeks(pid: int):
    p = need_project(pid)
    nw = n_weeks(p["form"] or {})
    syl = ws.load_syllabus(pid, p["name"]) or p["syllabus_md"]
    titles = week_titles(syl, nw)
    rows = []
    for st in ws.all_status(pid, p["name"], nw):
        rows.append({**st, "title": titles.get(st["week"], "")})
    return {"weeks": rows, "n_weeks": nw}


@app.get("/api/projects/{pid}/weeks/{week}")
def api_week_get(pid: int, week: int):
    p = need_project(pid)
    w = ws.load_week(pid, p["name"], week)
    idir = ws.assets_dir(pid, p["name"], week, "merge", create=False)
    # 플랜이 있으면 슬라이드 수로 잘라서 센다 — 버튼에 찍히는 숫자가 실제 배치될
    # 장수와 달라지면 안 된다(범위 밖 파일이 있으면 어긋난다).
    n_slides_plan = len(w["plan"]) if w["plan"] else None
    scanned = image_merge.scan(idir, n_slides=n_slides_plan) if idir.is_dir() else None
    plan = w["plan"] or []
    types: Dict[str, int] = {}
    for s in plan:
        if isinstance(s, dict):
            t = s.get("type", "bullets")
            types[t] = types.get(t, 0) + 1
    return {
        "week": week,
        "doc_md": w["doc_md"], "ppt_md": w["ppt_md"],
        "img_prompt": w["img_prompt"],
        "has_plan": bool(plan),
        "plan_slides": len(plan),
        "plan_types": types,
        # 인지수준 분포 — 목표와 자료가 정렬됐는지 교수자가 눈으로 확인하는 값.
        # 슬라이드에는 인쇄하지 않는다(학습자용 화면에 설계 태그를 노출하지 않는다).
        "plan_levels": deck_builder.level_counts(plan) if plan else {},
        "photo_slots": len(deck_builder.image_queries(plan)) if plan else 0,
        "photos": ws.count_photos(pid, p["name"], week),
        # 사진 자리는 있는데 이미지가 없는 슬라이드 — 빈 액자로 인쇄될 자리다.
        "empty_slots": _empty_slots(pid, p["name"], week, plan) if plan else [],
        "has_gap_prompt": ws.gap_prompt_path(pid, p["name"], week).is_file(),
        # 배색 — 이 주차가 쓸 값(해석 결과)과 고를 수 있는 목록.
        # ★ 'template' 이 아니라 'palette' 다. template 은 회사 PPTX 양식을 뜻한다.
        "palette": resolve_palette(pid, p, week),
        "palette_pinned": (ws.week_cfg(pid, p["name"], week).get("palette") or "").strip()
                          in palette.names(),
        "palettes": palette.available(),
        # 덱은 단계별 폴더에 흩어져 있다 — 어느 단계 산출물인지 함께 돌려준다.
        "decks": [
            {"name": f.name, "size": f.stat().st_size,
             "step": ws.step_label(st), "step_key": st}
            for st in ws.DECK_STEPS
            for f in reversed(ws.deck_versions(pid, p["name"], week, st))
        ],
        "images_dir": str(idir),
        "photos_dir": str(ws.assets_dir(pid, p["name"], week, "visual", create=False)),
        "week_dir": str(ws.week_dir(pid, p["name"], week, create=False)),
        "images_matched": len(scanned.matched) if scanned else 0,
        "n_slides": len(re.findall(r"(?m)^\s*#{2,3}\s*슬라이드", w["ppt_md"] or "")),
        "target_slides": video_mod.target_slides(p["form"] or {}),
        # ── 영상 ──
        **_video_fields(pid, p, week, plan),
    }


def _video_fields(pid: int, p: Dict[str, Any], week: int, plan: List[Dict]) -> Dict[str, Any]:
    """영상 단계가 화면에 필요한 값. 대본은 파일이 유일한 진실이라 매번 읽는다."""
    name = p["name"]
    form = p["form"] or {}
    sc = ws.load_script(pid, name, week)
    rows = sc.get("slides") or []
    chars = sum(len(r.get("narration") or "") for r in rows)
    vd = ws.video_dir(pid, name, week, create=False)
    pr = video_mod.read_progress(vd)
    return {
        "video_minutes": video_mod.video_minutes(form),
        "video_target_slides": video_mod.target_slides(form),
        "has_script": bool(rows),
        "script_slides": len(rows),
        "script_chars": chars,
        "script_est_min": round(video_mod.est_seconds("x" * chars) / 60, 1) if chars else 0,
        "script_short": sum(1 for r in (sc.get("report") or [])
                            if r.get("ratio") is not None and r["ratio"] < video_mod.MIN_RATIO),
        "script_updated": sc.get("updated_at") or "",
        "plan_slides_for_video": len(plan),
        "videos": [{"name": f.name, "size": f.stat().st_size}
                   for f in reversed(ws.video_versions(pid, name, week))],
        "video_dir": str(vd),
        # 인쇄 화면에 슬라이드를 곁들일 수 있는지. 0 이면 먼저 render 단계만 돌린다.
        "slide_pngs": len(list(sd.glob("*.png"))) if (sd := ws.video_sub(
            pid, name, week, ws.VIDEO_SLIDES, create=False)).is_dir() else 0,
        "engine_ok": video_mod.engine_ready()[0],
        "engine_why": video_mod.engine_ready()[1],
        "render_running": video_mod.running(vd),
        "render_died": video_mod.died(pr),
        "render_progress": pr,
        "render_summary": video_mod.summary(pr),
        "render_ratio": video_mod.overall_ratio(pr),
    }


@app.put("/api/projects/{pid}/weeks/{week}")
def api_week_put(pid: int, week: int, body: Dict[str, Any] = Body(...)):
    """직접 편집 저장. 저장한 본문을 기준으로 이후 AI 수정이 이어진다."""
    p = need_project(pid)
    kw: Dict[str, Any] = {}
    if "doc_md" in body:
        md = body["doc_md"] or ""
        kw["doc_md"] = md
        kw["doc_msgs"] = [
            {"role": "user", "content": "현재 문서(직접 편집본)를 기준으로 이어서 작업합니다."},
            {"role": "assistant", "content": md}]
    if "ppt_md" in body:
        md = body["ppt_md"] or ""
        kw["ppt_md"] = md
        kw["ppt_msgs"] = [
            {"role": "user", "content": "현재 문서(직접 편집본)를 기준으로 이어서 작업합니다."},
            {"role": "assistant", "content": md}]
    if kw:
        ws.save_week(pid, p["name"], week, **kw)
    return {"ok": True}


# ══ 생성 (SSE) ═════════════════════════════════════════════════════════════
def syllabus_user_msg(f: Dict[str, Any]) -> str:
    return (
        "다음 강의 정보로 강의계획서를 작성해 주세요.\n\n"
        f"과목명: {f.get('title') or '[입력 필요]'}\n"
        f"학문 분야: {f.get('field') or '-'}\n"
        f"수강 대상: {f.get('target') or '[입력 필요]'}\n"
        f"학점/시수: {f.get('credit') or '-'}\n"
        f"총 주차: {f.get('weeks')}주\n"
        f"강의 방식: {f.get('mode')}\n"
        f"주요 내용·주제: {f.get('topics') or '-'}\n"
        f"수강생 특성: {f.get('learner') or '-'}\n"
        f"평가 선호·수업 철학: {f.get('policy') or '-'}"
    )


def script_user_msg(week: int, fmt: str, note: str, syllabus_md: str, hours: int,
                    form: Optional[Dict[str, Any]] = None) -> str:
    kind = "학생용 교재(읽기 자료)" if fmt == "doc" else "PPT 슬라이드 개요"
    extra = f"\n[교수자 추가 요청] {note}\n" if (note or "").strip() else ""
    vol = ""
    if fmt == "ppt":
        vol = (f"\n이 차시는 약 {hours}시간 수업입니다. 슬라이드는 1시간당 약 {SLIDES_PER_HOUR}장 기준, "
               f"표지 제외 본문 약 {hours * SLIDES_PER_HOUR}장으로 작성하세요. 발표자 노트는 넣지 마세요.")
    return (
        f"아래는 확정된 강의계획서입니다. 이 계획서의 {week}주차에 대한 {kind}를 작성해 주세요. "
        f"반드시 계획서의 해당 주차 목표와 강좌 목표(G#)를 상속하세요.{extra}{vol}\n"
        f"=== 강의계획서 ===\n{syllabus_md}"
    )


@app.post("/api/gen/syllabus")
def api_gen_syllabus(body: Dict[str, Any] = Body(...)):
    pid = int(body["project_id"])
    p = need_project(pid)
    kind = body.get("kind") or "gen"          # gen | refine | check
    req = body.get("request") or ""
    form = body.get("form") if isinstance(body.get("form"), dict) else (p["form"] or {})

    def work(emit):
        nonlocal form
        cur = ws.load_syllabus(pid, p["name"]) or p["syllabus_md"]
        if kind == "gen":
            db.save_project(pid, form=form)
            ws.save_project_meta(pid, p["name"], form)
            msgs = [{"role": "user", "content": syllabus_user_msg(form)}]
            emit("status", {"message": "강의계획서 작성 중… (목표 설계 → 주차 분해 → 정렬 매트릭스)"})
            full = stream_llm(emit, prompts.SYS_SYLLABUS, msgs, label="강의계획서",
                              max_tokens=GEN_MAX_TOKENS)
            if full:
                db.save_project(pid, syllabus_md=full,
                                syllabus_msgs=msgs + [{"role": "assistant", "content": full}])
                ws.save_syllabus(pid, p["name"], full)
        elif kind == "check":
            emit("status", {"message": "정렬 점검 중… (Bloom 분포 · 목표–평가 정렬)"})
            msgs = [{"role": "user", "content": f"다음 산출물을 점검해 주세요.\n\n{cur}"}]
            rep = stream_llm(emit, prompts.SYS_CHECK_SYL, msgs, label="강의계획서 점검",
                             max_tokens=GEN_MAX_TOKENS)
            if rep:
                full = cur + f"\n\n---\n\n## 정렬 점검 보고\n\n{rep}"
                db.save_project(pid, syllabus_md=full)
                ws.save_syllabus(pid, p["name"], full)
        else:  # refine
            emit("status", {"message": "수정 반영 중…"})
            msgs = list(p["syllabus_msgs"] or [])
            if not msgs:
                msgs = [{"role": "user", "content": "현재 강의계획서를 기준으로 이어서 작업합니다."},
                        {"role": "assistant", "content": cur}]
            msgs.append({"role": "user", "content": REFINE_TMPL.format(req=req)})
            full = stream_llm(emit, prompts.SYS_SYLLABUS, msgs, label="강의계획서 수정",
                              max_tokens=GEN_MAX_TOKENS)
            if full:
                db.save_project(pid, syllabus_md=full,
                                syllabus_msgs=msgs + [{"role": "assistant", "content": full}])
                ws.save_syllabus(pid, p["name"], full)
        emit("done", {"ok": True})

    return stream_job(work)


@app.post("/api/gen/week")
def api_gen_week(body: Dict[str, Any] = Body(...)):
    """교재(doc) · 슬라이드 개요(ppt) 생성/수정/점검."""
    pid, week = int(body["project_id"]), int(body["week"])
    fmt = body.get("format") or "doc"            # doc | ppt
    kind = body.get("kind") or "gen"             # gen | refine | check
    note, req = body.get("note") or "", body.get("request") or ""
    p = need_project(pid)
    form = p["form"] or {}
    syl = ws.load_syllabus(pid, p["name"]) or p["syllabus_md"]
    if not syl.strip():
        raise HTTPException(400, "먼저 강의계획서를 생성하세요.")
    is_doc = (fmt == "doc")
    sys_gen = prompts.SYS_SCRIPT_DOC if is_doc else prompts.SYS_SCRIPT_PPT
    label = f"{week}주차 " + ("교재" if is_doc else "슬라이드 개요")

    def save(md: str, msgs: List[Dict]) -> None:
        kw = ({"doc_md": md, "doc_msgs": msgs} if is_doc else {"ppt_md": md, "ppt_msgs": msgs})
        ws.save_week(pid, p["name"], week, **kw)

    def work(emit):
        cur = ws.load_week(pid, p["name"], week)
        cur_md = cur["doc_md"] if is_doc else cur["ppt_md"]
        cur_msgs = cur["doc_msgs"] if is_doc else cur["ppt_msgs"]

        if kind == "check":
            emit("status", {"message": "정렬 점검 중…"})
            msgs = [{"role": "user", "content": f"다음 산출물을 점검해 주세요.\n\n{cur_md}"}]
            rep = stream_llm(emit, prompts.SYS_CHECK_SCR, msgs, label=f"{label} 점검",
                             max_tokens=GEN_MAX_TOKENS)
            if rep:
                save(cur_md + f"\n\n---\n\n## 정렬 점검 보고\n\n{rep}", cur_msgs)
            emit("done", {"ok": True})
            return

        if kind == "refine":
            emit("status", {"message": "수정 반영 중…"})
            msgs = list(cur_msgs) or [
                {"role": "user", "content": "현재 문서를 기준으로 이어서 작업합니다."},
                {"role": "assistant", "content": cur_md}]
            msgs.append({"role": "user", "content": REFINE_TMPL.format(req=req)})
            full = stream_llm(emit, sys_gen, msgs, label=f"{label} 수정",
                              max_tokens=GEN_MAX_TOKENS)
            if full:
                save(full, msgs + [{"role": "assistant", "content": full}])
            emit("done", {"ok": True})
            return

        # ── gen ──
        if is_doc:
            msgs = [{"role": "user",
                     "content": script_user_msg(week, "doc", note, syl, session_hours(form))}]
            emit("status", {"message": "교재 작성 중…"})
            full = stream_llm(emit, sys_gen, msgs, label=label, max_tokens=GEN_MAX_TOKENS)
            if full:
                save(full, msgs + [{"role": "assistant", "content": full}])
            emit("done", {"ok": True})
            return

        # 슬라이드 개요는 2단계(제목 목록 → 상세)로 개수를 보장한다
        n = video_mod.target_slides(form)
        emit("status", {"message": f"슬라이드 목록 구성 중… (목표 {n}장)"})
        list_user = (
            f"'{week}주차' 강의를 정확히 {n}장 슬라이드로 구성합니다. "
            f"도입(2~3장) · 선수지식(1~2장) · 개념 설명(대부분, 개념마다 정의·예시·비교·적용으로 여러 장 분절) "
            f"· 사례/활동 · 형성평가(1~2장) · 요약·예고(1~2장) 순서로, "
            f"슬라이드 {n}개의 '제목'만 1.부터 {n}.까지 번호 목록으로 출력하세요. "
            f"정확히 {n}줄, 제목 외 다른 말은 쓰지 마세요.\n\n=== 강의계획서 ===\n{syl}")
        try:
            titles = provider().generate(
                "너는 슬라이드 제목 목록만 출력한다. 머리말·설명 없이 '1. 제목' 형식 번호 목록만.",
                [{"role": "user", "content": list_user}],
                max_tokens=SLIDE_LIST_TOKENS, temperature=0.4)
        except Exception as e:  # noqa: BLE001
            print(f"[슬라이드 목록] 오류: {e}", flush=True)
            titles = ""
        emit("status", {"message": f"슬라이드 상세 작성 중… ({n}장)"})
        exp_user = (
            f"아래 슬라이드 제목 목록({n}장)의 **모든 {n}개** 슬라이드를 각각 상세 개요로 작성하세요. "
            f"반드시 {n}개의 '### 슬라이드 N — 제목' 블록을 만들고, 각 블록에 "
            f"- 레이아웃 제안 / - 핵심 메시지(1개) / - 본문 개요(불릿 3~5개)를 넣으세요. "
            f"발표자 노트는 넣지 마세요. 맨 앞에 차시 학습목표와 슬라이드 구성 개요 표도 포함하세요.\n\n"
            f"[슬라이드 제목 목록]\n{titles}\n\n[강의계획서]\n{syl}")
        msgs = [{"role": "user", "content": exp_user}]
        full = stream_llm(emit, sys_gen, msgs, label=label, max_tokens=SLIDE_EXPAND_TOKENS)
        if full:
            save(full, msgs + [{"role": "assistant", "content": full}])
        emit("done", {"ok": True})

    return stream_job(work)


# ══ 슬라이드 파이프라인 — 5단계 ═════════════════════════════════════════════
#
#   1 개요          /api/gen/week (format=ppt)   개요 md
#   2 초안 PPT      /api/slides/draft            플랜 JSON + 사진 없는 .pptx
#   3 사진원고 서칭/추가  /api/slides/visual    주제 사진 수집·보관 → 재빌드
#     ★ 화면에서 꺼 두었다(week-ppt.js 의 off). 라우트는 그대로 살아 있다.
#   4 씬 프롬프트 생성    /api/slides/prompts   슬라이드별 이미지 생성 프롬프트
#   5 이미지 합치기 및 최종 PPTX  /api/slides/merge   내 이미지 + 자동 사진 → 최종본
#
# 한 버튼이 전부 하던 것을 쪼갠 이유: 아트디렉터 LLM·사진 검색·빌드·프롬프트가
# 한 덩어리라 하나만 틀려도 전부(LLM 호출까지) 다시 돌려야 했다. 단계가 갈리면
# 사진만 다시 받거나 프롬프트만 다시 뽑을 수 있다.

def _art_gen_fn():
    """구조화(JSON) 작업용 비추론 모델 호출자."""
    pv = provider(no_think=True)
    print(f"[art] 모델={pv.model}", flush=True)

    def gen_fn(system, user, mt):
        return pv.generate(system, [{"role": "user", "content": user}],
                           max_tokens=mt, temperature=0.2)
    return gen_fn


def resolve_palette(pid: int, p: Dict[str, Any], week: int,
                    body: Optional[Dict[str, Any]] = None) -> str:
    """이 주차에 쓸 **배색** 이름. (회사 PPTX 양식과는 다른 것이다)

    주차별 값 > 전역 기본값(user_settings.palette) > palette.DEFAULT.
    ★ 주차별이 이기는 이유: 3주차를 새 배색으로 바꿨다고 2주차 재빌드까지
      새 배색으로 나오면 안 된다.
    body 에 palette 가 오면 그 값을 주차 설정에 저장한다(고른 즉시 기억).
    """
    name = p["name"]
    if body and body.get("palette") is not None:
        want = str(body["palette"] or "").strip()
        if want and want not in palette.names():
            raise HTTPException(400, f"없는 배색입니다: {want}")
        ws.save_week_cfg(pid, name, week, palette=want)
    picked = (ws.week_cfg(pid, name, week).get("palette") or "").strip()
    if picked and picked not in palette.names():
        # 템플릿이 지워졌다. 조용히 다른 색으로 빌드되면 왜 바뀌었는지 알 수 없으므로
        # 고정을 풀어 화면에 '기본값 사용' 으로 보이게 한다.
        ws.save_week_cfg(pid, name, week, palette="")
        picked = ""
    if not picked:
        picked = (settings().palette or "").strip()
    return picked if picked in palette.names() else palette.DEFAULT


def _build_and_save(pid: int, p: Dict[str, Any], week: int, step: str,
                    plan: List[Dict], images: Dict[int, bytes], *,
                    embed_font: bool, credits: str = "",
                    pal: Optional[str] = None) -> str:
    """빌드해서 **그 단계 폴더**에 다음 버전으로 저장. 단계마다 버전 계열이 따로다."""
    form = p["form"] or {}
    course = (form.get("title") or "강의").strip()
    title = deck_stem(form, week)
    data = deck_builder.build_deck(
        plan, template_path=template_arg(), images=images, deck_title=title,
        logo_path=logo_arg(), logo2_path=logo2_arg(),
        footer=f"{course} · {week}주차", embed_font=embed_font,
        template=pal or resolve_palette(pid, p, week))
    if not data:
        raise RuntimeError("슬라이드 빌드 실패(python-pptx 확인).")
    return ws.save_deck(pid, p["name"], week, step, data,
                        stem=title, credits=credits).name


def _need_plan(pid: int, name: str, week: int) -> List[Dict]:
    plan = ws.load_week(pid, name, week)["plan"]
    if not plan:
        raise HTTPException(400, "슬라이드플랜이 없습니다. 먼저 '2 초안 PPT' 를 실행하세요.")
    return plan


# ── 2 초안 PPT — 개요 → 레이아웃 배정 → 사진 없는 .pptx ─────────────────────
@app.post("/api/slides/draft")
def api_slides_draft(body: Dict[str, Any] = Body(...)):
    pid, week = int(body["project_id"]), int(body["week"])
    embed_font = bool(body.get("embed_font", True))
    p = need_project(pid)
    form = p["form"] or {}
    course = (form.get("title") or "강의").strip()
    # ★ 배색은 라우트 진입에서 해석한다. work() 안에서 부르면 '없는 배색' 이
    #   400 이 아니라 SSE error 로 삼켜져 조용한 실패가 된다.
    pal = resolve_palette(pid, p, week, body)

    def work(emit):
        outline = ws.load_week(pid, p["name"], week)["ppt_md"]
        if not outline.strip():
            emit("error", {"message": "먼저 '1 개요' 를 만드세요."})
            return
        title = deck_stem(form, week)
        emit("status", {"message": "슬라이드 구성 분석 중… (레이아웃 13종 배정)"})
        plan = deck_builder.plan_from_outline(
            _art_gen_fn(), outline, title, subtitle=f"{week}주차 · {course}", course=course)
        ws.save_week(pid, p["name"], week, plan=plan)

        emit("status", {"message": "초안 빌드 중… (사진 없이 레이아웃만)"})
        fname = _build_and_save(pid, p, week, "draft", plan, {}, embed_font=embed_font, pal=pal)
        types: Dict[str, int] = {}
        for s in plan:
            t = s.get("type", "bullets")
            types[t] = types.get(t, 0) + 1
        emit("done", {"ok": True, "slides": len(plan), "file": fname, "types": types,
                      "photo_slots": len(deck_builder.image_queries(plan))})

    return stream_job(work)


# ── 3 사진원고 서칭/추가 — 주제 사진 수집·보관 → 재빌드 ────────────────────────────
@app.post("/api/slides/visual")
def api_slides_visual(body: Dict[str, Any] = Body(...)):
    pid, week = int(body["project_id"]), int(body["week"])
    embed_font = bool(body.get("embed_font", True))
    refetch = bool(body.get("refetch", False))
    p = need_project(pid)
    plan = _need_plan(pid, p["name"], week)
    title = deck_stem(p["form"] or {}, week)
    pal = resolve_palette(pid, p, week, body)

    def work(emit):
        queries = deck_builder.image_queries(plan)
        if not queries:
            emit("error", {"message": "사진 자리가 있는 슬라이드가 없습니다(photo 타입 없음)."})
            return

        images: Dict[int, bytes] = {}
        # 이미 받아 둔 사진이 있으면 다시 받지 않는다(느리고 결과도 달라진다).
        kept = 0
        if not refetch:
            got = image_merge.scan(
                ws.assets_dir(pid, p["name"], week, "visual", create=False),
                n_slides=len(plan))
            images.update(got.images)
            kept = len(got.images)
            if kept:
                emit("status", {"message": f"보관된 사진 {kept}장 재사용"})

        todo = [(i, q) for i, q in queries.items() if i not in images]
        if todo:
            ukey = (settings().unsplash_key or "").strip()
            cache: Dict[str, Any] = {}
            for k, (idx, q) in enumerate(todo, 1):
                emit("status", {"message": f"주제 사진 수집 중… ({k}/{len(todo)}) — {q}",
                                "progress": k / len(todo)})
                data, credit = image_search.fetch(q, cache=cache, unsplash_key=ukey)
                if data:
                    images[idx] = data
                    plan[idx]["_credit"] = credit

        entries = [(i + 1, plan[i].get("_credit")) for i in sorted(images.keys())]
        credits = image_search.credits_text(f"{title} — 이미지 출처 (CC 라이선스)", entries)
        ws.save_photos(pid, p["name"], week, images, credits=credits)

        emit("status", {"message": "재빌드 중… (사진 배치 + 레이아웃 정돈)"})
        fname = _build_and_save(pid, p, week, "visual", plan, images,
                                embed_font=embed_font, pal=pal, credits=credits)
        emit("done", {"ok": True, "photos": len(images), "slots": len(queries),
                      "reused": kept, "file": fname})

    return stream_job(work)


# ── 4 씬 프롬프트 생성 — 슬라이드별 이미지 생성 프롬프트 ─────────────────────────
@app.post("/api/slides/prompts")
def api_slides_prompts(body: Dict[str, Any] = Body(...)):
    pid, week = int(body["project_id"]), int(body["week"])
    p = need_project(pid)
    plan = _need_plan(pid, p["name"], week)
    title = deck_stem(p["form"] or {}, week)

    # 3단계(사진 서칭)를 껐을 때는 자리 전부를 뽑아야 한다.
    #
    # ★ 안 그러면 조용히 빈 자리가 남는다: 03_비주얼/assets 에 예전 사진이 남아 있어도
    #   지금 덱은 2단계 초안(사진 없음)이다. 그 번호를 제외해 버리면 사진도 그림도
    #   없는 슬라이드가 되고, 4·5단계를 다 돌려도 아무 경고가 없다.
    #   초안을 다시 설계했다면 슬라이드 번호까지 어긋난다.
    all_slots = bool(body.get("all_slots"))

    def work(emit):
        # 자동 사진이 이미 붙은 슬롯은 그릴 필요가 없다 — 03_비주얼/assets 를 스캔해
        # 있는 번호를 제외하고, "자리는 있는데 사진을 못 찾은 것" 만 프롬프트로 뽑는다.
        have = set()
        pdir = ws.assets_dir(pid, p["name"], week, "visual", create=False)
        if not all_slots and pdir.is_dir():
            for f in pdir.iterdir():
                m = re.match(r"^(\d{1,3})", f.stem)
                if f.is_file() and m:
                    have.add(int(m.group(1)) - 1)      # 파일명은 1-based
        emit("status", {"message": "씬 프롬프트 작성 중…" + (
            " (사진 자리 전부)" if all_slots else f" (자동 사진 {len(have)}장은 제외)")})
        bundle = deck_builder.image_prompt_bundle(
            plan, title, generate_fn=_art_gen_fn(), have_photos=have)
        ws.save_week(pid, p["name"], week,
                     img_prompt=json.dumps(bundle, ensure_ascii=False, indent=2))
        msg = (f"그릴 이미지 {bundle['count']}장"
               + (f" · 자동 사진 {bundle['photos_found']}장은 제외" if have else ""))
        emit("done", {"ok": True, "count": bundle["count"],
                      "placeable": bundle["count"], "message": msg})

    return stream_job(work)


def _empty_slots(pid: int, name: str, week: int, plan: List[Dict]) -> List[int]:
    """사진 자리는 예약됐는데 이미지가 없는 슬라이드(1-based).

    05_합치기/assets(내 이미지)와 03_비주얼/assets(자동 사진) 둘 다 본다 —
    어느 쪽으로든 채워졌으면 빈 자리가 아니다.
    """
    have = set()
    for step in ("merge", "visual"):
        d = ws.assets_dir(pid, name, week, step, create=False)
        if not d.is_dir():
            continue
        for f in d.iterdir():
            m = re.match(r"^(\d{1,4})", f.stem)
            if f.is_file() and m:
                have.add(int(m.group(1)))
    return [n for n in deck_builder.photo_slot_slides(plan) if n not in have]


@app.post("/api/slides/prompts/gap")
def api_slides_prompts_gap(body: Dict[str, Any] = Body(...)):
    """빠진 자리만 프롬프트로 뽑아 **별도 JSON** 으로 낸다.

    전체를 다시 돌리면 이미 그린 25장을 또 그리게 된다. 부족분만 따로 내야
    그것만 돌릴 수 있다.
    """
    pid, week = int(body["project_id"]), int(body["week"])
    p = need_project(pid)
    name = p["name"]
    plan = _need_plan(pid, name, week)
    empty = _empty_slots(pid, name, week, plan)
    if not empty:
        ws.clear_gap_prompt(pid, name, week)
        return {"ok": True, "count": 0, "empty": [],
                "message": "빈 사진 자리가 없습니다. 부족분 파일도 지웠습니다."}

    def work(emit):
        emit("status", {"message": f"빠진 자리 {len(empty)}곳만 프롬프트 작성 중…"})
        # 빠진 번호만 남기고 나머지는 have_photos 로 제외한다 — 같은 코드를 쓰되
        # 대상만 좁힌다(프롬프트 문구가 본 파일과 달라지면 결과물이 안 어울린다).
        want = {n - 1 for n in empty}
        have = {i for i in range(len(plan)) if i not in want}
        bundle = deck_builder.image_prompt_bundle(
            plan, deck_stem(p["form"] or {}, week),
            generate_fn=_art_gen_fn(), have_photos=have)
        bundle["gap_of"] = ws.F_PROMPT
        bundle["slides"] = empty
        path = ws.save_gap_prompt(pid, name, week, bundle)
        emit("done", {"ok": True, "count": bundle["count"], "empty": empty,
                      "file": path.name,
                      "message": f"부족분 {bundle['count']}장 · {path.name}"})

    return stream_job(work)


# ── 5 이미지 합치기 및 최종 PPTX — 내 이미지 + 자동 사진 → 최종본 ────────────────────────
@app.post("/api/slides/merge")
def api_slides_merge(body: Dict[str, Any] = Body(...)):
    pid, week = int(body["project_id"]), int(body["week"])
    embed_font = bool(body.get("embed_font", True))
    p = need_project(pid)
    plan = _need_plan(pid, p["name"], week)
    pal = resolve_palette(pid, p, week, body)

    idir = ws.assets_dir(pid, p["name"], week, "merge")
    mine = image_merge.scan(idir, n_slides=len(plan))
    auto = image_merge.scan(ws.assets_dir(pid, p["name"], week, "visual", create=False),
                            n_slides=len(plan))
    if not mine.images and not auto.images:
        return {"ok": False, "report": image_merge.report(mine), "placed": 0,
                "images_dir": str(idir)}

    # ★ 자동 사진을 바탕에 깔고 내 이미지로 덮는다. 내 것만 넣으면 자동 사진이
    #   전부 사라진 덱이 나온다(실제로 그랬다).
    combined = {**auto.images, **mine.images}
    plan2, used, skipped = deck_builder.apply_images(plan, combined)
    try:
        fname = _build_and_save(pid, p, week, "merge", plan2, used, embed_font=embed_font, pal=pal)
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    # ★ 이미지가 안 온 사진 자리는 **빈 액자로 인쇄된다.** 조용히 넘어가면
    #   82장을 다 넘겨보기 전에는 모른다(3주차 7번·76번이 그렇게 나갔다).
    empty = [n for n in deck_builder.photo_slot_slides(plan2) if (n - 1) not in used]
    return {"ok": True, "placed": len(used), "mine": len(mine.images),
            "auto": len(auto.images), "skipped": skipped, "empty": empty,
            "report": image_merge.report(mine), "file": fname,
            "images_dir": str(idir)}


# ══ 영상 파이프라인 — 대본 → 렌더 ═══════════════════════════════════════════
#   대본은 이 서버가 만든다(LLM 호출). 렌더(슬라이드 PNG·음성·자막·합성)는 엔진
#   프로젝트의 별도 프로세스가 한다 — onnxruntime·PowerPoint COM 을 이 서버에
#   끌어들이지 않고, 서버를 닫아도 렌더가 이어지게 하기 위해서다.

NARRATION_TOKENS = 16000
NARRATION_BATCH = 4          # 슬라이드당 ~1,100자면 4장이 출력 ~4,400자. 10장은 잘린다
NARRATION_REFILL = 2         # 부족분 보강 재요청 횟수


def _narration_json(raw: str) -> Dict[int, str]:
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        raise ValueError("응답에서 JSON 을 찾지 못했습니다.")
    doc = json.loads(m.group(0))
    return {int(x["index"]): str(x.get("narration") or "").strip()
            for x in (doc.get("slides") or []) if x.get("index") is not None}


def _ask_narration(system: str, user: str, label: str) -> Dict[int, str]:
    """JSON 이 깨지면 한 번 더 조른다. 그래도 안 되면 빈 dict — 파이프라인은 멈추지 않는다."""
    pv = provider(no_think=True)
    for attempt in (1, 2):
        try:
            return _narration_json(pv.generate(
                system, [{"role": "user", "content": user}],
                max_tokens=NARRATION_TOKENS, temperature=0.4))
        except Exception as e:  # noqa: BLE001
            print(f"[{label}] 파싱 실패({attempt}/2): {e}", flush=True)
            if attempt == 2:
                return {}
            user += "\n\n반드시 지정된 JSON 형식으로만 응답하라."
    return {}


@app.post("/api/video/script")
def api_video_script(body: Dict[str, Any] = Body(...)):
    """슬라이드플랜 → 나레이션 대본. 목표 분량을 채우고 부족분만 보강한다."""
    pid, week = int(body["project_id"]), int(body["week"])
    p = need_project(pid)
    name, form = p["name"], (p["form"] or {})
    minutes = float(body.get("minutes") or video_mod.video_minutes(form))
    regen = bool(body.get("regen"))

    def work(emit):
        plan = _need_plan(pid, name, week)
        prev = ws.load_script(pid, name, week)
        keep = {} if regen else {int(r["index"]): (r.get("narration") or "")
                                 for r in (prev.get("slides") or [])}
        targets = video_mod.target_chars(plan, minutes)
        course = (form.get("title") or "강의").strip()
        head = (f"강의: {course} {week}주차\n"
                f"목표 영상 길이: {minutes:.0f}분 · 슬라이드 {len(plan)}장\n")

        out: Dict[int, str] = {i: t for i, t in keep.items() if t.strip()}
        todo = [i for i in range(1, len(plan) + 1) if not (out.get(i) or "").strip()]
        emit("status", {"message": f"대본 {len(todo)}장 생성 (목표 {minutes:.0f}분)"})

        batches = [todo[i:i + NARRATION_BATCH] for i in range(0, len(todo), NARRATION_BATCH)]
        tail = ""
        for bi, group in enumerate(batches, start=1):
            emit("status", {"message": f"대본 {bi}/{len(batches)} 배치",
                            "progress": (bi - 1) / max(len(batches), 1)})
            blocks = [f"{video_mod.slide_brief(i, plan[i-1])}\n목표 {targets[i]}자" for i in group]
            user = (head + (f'직전 슬라이드의 마지막 문장: "{tail}"\n' if tail else "")
                    + "\n각 슬라이드의 목표 글자수를 반드시 채워라.\n\n" + "\n\n".join(blocks))
            got = _ask_narration(prompts.SYS_NARRATION, user, f"대본 {bi}")
            for i in group:
                out[i] = (got.get(i) or "").strip()
            if (last := out.get(group[-1], "")):
                tail = last.rstrip().split(". ")[-1][:80]

        # 보강 — 목표의 85% 미만인 것만. 넘치는 것은 사람이 줄인다.
        for rnd in range(1, NARRATION_REFILL + 1):
            short = [i for i in range(1, len(plan) + 1)
                     if len(out.get(i, "")) < targets[i] * video_mod.MIN_RATIO]
            if not short:
                break
            emit("status", {"message": f"분량 보강 {rnd}회 · {len(short)}장"})
            for k in range(0, len(short), NARRATION_BATCH):
                group = short[k:k + NARRATION_BATCH]
                blocks = []
                for i in group:
                    cur = out.get(i, "")
                    blocks.append(
                        f"{video_mod.slide_brief(i, plan[i-1])}\n"
                        f"현재 {len(cur)}자 → 목표 {targets[i]}자 (약 {targets[i]-len(cur)}자 부족)\n"
                        f"현재 대본:\n{cur}")
                user = (head + "아래 대본들을 목표 분량까지 보강하라. 기존 내용을 유지하고 "
                        "정의·이유·구분·예시를 덧붙여 늘린다. 같은 말 반복 금지.\n\n"
                        + "\n\n".join(blocks))
                got = _ask_narration(prompts.SYS_NARRATION, user, f"보강 {rnd}")
                for i in group:
                    new = (got.get(i) or "").strip()
                    if len(new) > len(out.get(i, "")):   # 더 짧아지면 원본을 지킨다
                        out[i] = new

        report = video_mod.build_report(plan, out, targets)
        doc = {"minutes": minutes, "report": report,
               "updated_at": datetime.now().isoformat(timespec="seconds"),
               "slides": [{"index": i, "type": s.get("type") or "bullets",
                           "title": s.get("title") or "", "narration": out.get(i, "")}
                          for i, s in enumerate(plan, start=1)]}
        path = ws.save_script(pid, name, week, doc)
        msg = video_mod.report_summary(report, minutes)
        print(f"[대본] {msg}", flush=True)
        emit("done", {"ok": True, "message": msg, "file": str(path),
                      "slides": len(plan), "chars": sum(r["chars"] for r in report),
                      "short": sum(1 for r in report
                                   if r.get("ratio") is not None
                                   and r["ratio"] < video_mod.MIN_RATIO)})

    return stream_job(work)


@app.put("/api/video/script/{pid}/{week}")
def api_video_script_put(pid: int, week: int, body: Dict[str, Any] = Body(...)):
    """씬 하나(또는 여러 개) 대본 수정. Claude Code 창에서 고친 것도 같은 파일이다."""
    p = need_project(pid)
    doc = ws.load_script(pid, p["name"], week)
    if not doc.get("slides"):
        raise HTTPException(400, "대본이 없습니다. 먼저 대본을 생성하세요.")
    edits = {int(k): str(v or "") for k, v in (body.get("slides") or {}).items()}
    changed = 0
    for row in doc["slides"]:
        i = int(row["index"])
        if i in edits and edits[i] != (row.get("narration") or ""):
            row["narration"] = edits[i]
            changed += 1
    if changed:
        for r in (doc.get("report") or []):
            i = int(r["index"])
            if i in edits:
                r["chars"] = len(edits[i])
                r["ratio"] = round(r["chars"] / r["target"], 2) if r.get("target") else None
                r["est_seconds"] = round(video_mod.est_seconds(edits[i]), 1)
        doc["updated_at"] = datetime.now().isoformat(timespec="seconds")
        ws.save_script(pid, p["name"], week, doc)
    return {"ok": True, "changed": changed}


@app.get("/api/video/script/{pid}/{week}")
def api_video_script_get(pid: int, week: int):
    p = need_project(pid)
    doc = ws.load_script(pid, p["name"], week)
    return {"ok": True, "script": doc,
            "path": str(ws.script_path(pid, p["name"], week))}


@app.get("/api/video/script/{pid}/{week}/print")
def api_video_script_print(pid: int, week: int, slides: int = Query(1)):
    """대본 인쇄용 HTML. 영상과 분리 — 대본만 있으면 바로 뽑을 수 있다.

    슬라이드는 50mm 로 작게 왼쪽에 넣는다(흐름 확인용). 이미지는 **data URI 로 굽는다** —
    바깥 워크스페이스의 파일이라 /static 으로 못 주고, 인쇄 시점에 요청이 늦어
    빈 칸으로 인쇄되는 사고를 막으려면 문서 안에 들어 있어야 한다.
    """
    p = need_project(pid)
    name = p["name"]
    doc = ws.load_script(pid, name, week)
    if not (doc.get("slides")):
        raise HTTPException(400, "나레이션 대본이 없습니다. 먼저 대본을 생성하세요.")

    imgs: Dict[int, str] = {}
    if slides:
        sdir = ws.video_sub(pid, name, week, ws.VIDEO_SLIDES, create=False)
        if sdir.is_dir():
            imgs = _slide_data_uris(sdir, [int(r["index"]) for r in doc["slides"]])

    # 번들이 있으면 wav 실제 길이로 시간대를 적는다 — 없으면 글자수 추정(문서에 표시됨)
    bdir = ws.video_sub(pid, name, week, ws.VIDEO_BUNDLE, create=False)
    bundle = next(iter(sorted(bdir.glob("ch*"))), None) if bdir.is_dir() else None

    return HTMLResponse(video_mod.script_print_html(
        course=(p["form"] or {}).get("title") or "강의", week=week, doc=doc,
        minutes=video_mod.video_minutes(p["form"] or {}),
        slide_img=imgs, bundle=bundle))


def _slide_data_uris(sdir: Path, want: List[int], px: int = 640) -> Dict[int, str]:
    """슬라이드 PNG → 축소 JPEG data URI. 82장 원본을 그대로 굽으면 문서가 16MB 가 된다."""
    import base64
    import io as _io
    try:
        from PIL import Image
    except ImportError:
        return {}
    files = {}
    for f in sdir.glob("*.png"):
        m = re.match(r"^(\d{1,4})", f.stem)
        if m:
            files[int(m.group(1))] = f
    out: Dict[int, str] = {}
    for n in want:
        f = files.get(n)
        if not f:
            continue
        try:
            im = Image.open(f).convert("RGB")
            if im.width > px:
                im = im.resize((px, round(im.height * px / im.width)), Image.LANCZOS)
            buf = _io.BytesIO()
            im.save(buf, "JPEG", quality=72, optimize=True)
            out[n] = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        except Exception:      # noqa: BLE001 — 한 장 실패로 인쇄를 막지 않는다
            continue
    return out


@app.post("/api/video/render")
def api_video_render(body: Dict[str, Any] = Body(...)):
    """엔진에 렌더를 맡긴다. 즉시 반환하고 진행률은 주차 조회로 폴링한다."""
    pid, week = int(body["project_id"]), int(body["week"])
    p = need_project(pid)
    name = p["name"]
    vd = ws.video_dir(pid, name, week, create=True)
    if video_mod.running(vd):
        raise HTTPException(409, "이미 렌더가 진행 중입니다.")

    deck = None
    for st in ws.DECK_STEPS:
        vs = ws.deck_versions(pid, name, week, st)
        if vs:
            deck = vs[-1]
            break
    if deck is None:
        raise HTTPException(400, "완성된 덱(.pptx)이 없습니다. 슬라이드 단계를 먼저 끝내세요.")
    if not ws.script_path(pid, name, week).is_file():
        raise HTTPException(400, "나레이션 대본이 없습니다. 먼저 대본을 생성하세요.")

    stages = body.get("stages") or ["render", "bundle", "tts", "compose", "viewer"]
    try:
        r = video_mod.start(
            vd, chapter=max(1, min(999, int(week))),
            deck=str(deck), script=str(ws.script_path(pid, name, week)),
            slides_dir=str(ws.video_sub(pid, name, week, ws.VIDEO_SLIDES)),
            bundle_root=str(ws.video_sub(pid, name, week, ws.VIDEO_BUNDLE)),
            out_dir=str(ws.video_sub(pid, name, week, ws.VIDEO_OUT)),
            out_name=f"영상_v{ws.next_video_version(pid, name, week)}.mp4",
            voice=body.get("voice") or "F2", speed=float(body.get("speed") or 1.02),
            limit=int(body.get("limit") or 0) or None,
            kenburns=body.get("kenburns") or "off",
            burn_subs=bool(body.get("burn_subs", False)),
            soft_subs=bool(body.get("soft_subs", True)),
            stages=stages, force=body.get("force") or [],
            title=f"{week}주차 · {(p['form'] or {}).get('title') or '강의'}")
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, **r}


@app.get("/api/video/running")
def api_video_running(project_id: int = Query(...)):
    """이 강좌에서 **지금 돌고 있는 렌더**. 어느 화면에 있어도 보이게 하려고 둔다.

    렌더는 1시간이 넘어서 사람이 그 화면을 지키고 있지 않는다. 다른 일을 하다
    돌아오는 게 정상이고, 그때 '어디까지 갔나' 를 물어볼 데가 있어야 한다.

    주차 폴더의 progress.json 만 읽는다 — 15주차라도 파일 15개라 가볍다.
    """
    p = need_project(project_id)
    name = p["name"]
    out: List[Dict[str, Any]] = []
    for wk in range(1, n_weeks(p["form"] or {}) + 1):
        vd = ws.video_dir(project_id, name, wk, create=False)
        if not (vd / ws.F_PROGRESS).is_file():
            continue
        pr = video_mod.read_progress(vd)
        if not pr:
            continue
        running, died = video_mod.running(vd), video_mod.died(pr)
        if not running and not died:
            continue          # 끝난 렌더는 알릴 것이 없다 — 완성 영상이 곧 결과다
        out.append({
            "week": wk, "running": running, "died": died,
            "stage": pr.get("stage") or "", "ratio": video_mod.overall_ratio(pr),
            "summary": video_mod.summary(pr),
            "message": pr.get("message") or "",
            "done": pr.get("done") or 0, "total": pr.get("total") or 0,
            "updated_at": pr.get("updated_at") or "",
        })
    return {"jobs": out}


@app.post("/api/video/cancel")
def api_video_cancel(body: Dict[str, Any] = Body(...)):
    pid, week = int(body["project_id"]), int(body["week"])
    p = need_project(pid)
    vd = ws.video_dir(pid, p["name"], week, create=False)
    return {"ok": video_mod.cancel(vd)}


@app.post("/api/open-folder")
def api_open_folder(body: Dict[str, Any] = Body(...)):
    """탐색기로 폴더 열기(로컬 앱 전용). 실패해도 경로는 돌려준다.

    what: images(기본, 05_합치기/assets) | photos(03_비주얼/assets) |
          week(주차 루트) | video(06_영상)
    """
    pid, week = int(body["project_id"]), int(body["week"])
    what = body.get("what") or "images"
    p = need_project(pid)
    if what == "week":
        d = ws.week_dir(pid, p["name"], week)
    elif what == "photos":
        d = ws.assets_dir(pid, p["name"], week, "visual")
    elif what == "video":
        d = ws.video_dir(pid, p["name"], week)
    else:
        d = ws.assets_dir(pid, p["name"], week, "merge")
    try:
        if hasattr(os, "startfile"):
            os.startfile(str(d))  # noqa: S606 — Windows
            return {"ok": True, "path": str(d)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "path": str(d), "message": str(e)}
    return {"ok": False, "path": str(d), "message": "이 OS 에서는 자동으로 열 수 없습니다."}


# ══ 다운로드 ═══════════════════════════════════════════════════════════════
def _dl(data: bytes, filename: str, mime: str) -> Response:
    return Response(content=data, media_type=mime, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


def md_to_doc_bytes(md_text: str) -> bytes:
    import markdown as md_lib
    body = md_lib.markdown(md_text or "", extensions=["tables", "fenced_code"])
    html = (
        "<html xmlns:o='urn:schemas-microsoft-com:office:office' "
        "xmlns:w='urn:schemas-microsoft-com:office:word'><head><meta charset='utf-8'>"
        "<style>body{font-family:'Malgun Gothic',sans-serif;font-size:11pt;line-height:1.6}"
        "table{border-collapse:collapse;width:100%}th,td{border:1px solid #999;padding:5pt;font-size:10pt}"
        "th{background:#eaf3fc}h1{font-size:16pt}h2{font-size:13pt;color:#0e4e96}h3{font-size:11.5pt}</style>"
        f"</head><body>{body}</body></html>")
    return ("﻿" + html).encode("utf-8")


@app.get("/api/dl/syllabus/{pid}.{ext}")
def dl_syllabus(pid: int, ext: str):
    p = need_project(pid)
    md = ws.load_syllabus(pid, p["name"]) or p["syllabus_md"]
    name = f"{(p['form'] or {}).get('title') or '강의'}_강의계획서"
    if ext == "md":
        return _dl(md.encode("utf-8"), name + ".md", "text/markdown")
    if ext == "doc":
        return _dl(md_to_doc_bytes(md), name + ".doc", "application/msword")
    raise HTTPException(404)


@app.get("/api/dl/week/{pid}/{week}/{what}.{ext}")
def dl_week(pid: int, week: int, what: str, ext: str):
    p = need_project(pid)
    w = ws.load_week(pid, p["name"], week)
    form = p["form"] or {}
    is_ppt = (what == "ppt")
    md = w["ppt_md"] if is_ppt else w["doc_md"]
    name = deck_stem(form, week, "PPT개요" if is_ppt else "교재")
    if ext == "md":
        return _dl(md.encode("utf-8"), name + ".md", "text/markdown")
    if ext == "doc":
        return _dl(md_to_doc_bytes(md), name + ".doc", "application/msword")
    if ext == "pptx" and is_ppt:      # 개요 PPTX(회사 양식 placeholder)
        data = outline_to_pptx(md, deck_title=name, template_path=template_arg())
        if not data:
            raise HTTPException(500, "개요 PPTX 생성 실패")
        return _dl(data, name + ".pptx", PPTX_MIME)
    if ext == "json" and what == "imgprompt":
        return _dl((w["img_prompt"] or "").encode("utf-8"),
                   deck_stem(form, week) + "_이미지프롬프트.json", "application/json")
    if ext == "json" and what == "imgprompt-gap":
        g = ws.load_gap_prompt(pid, p["name"], week)
        if not g:
            raise HTTPException(404, "부족분 프롬프트가 없습니다.")
        return _dl(json.dumps(g, ensure_ascii=False, indent=2).encode("utf-8"),
                   deck_stem(form, week) + "_이미지프롬프트_부족분.json",
                   "application/json")
    raise HTTPException(404)


@app.get("/api/dl/deck/{pid}/{week}/{name}")
def dl_deck(pid: int, week: int, name: str):
    p = need_project(pid)
    f = ws.find_deck(pid, p["name"], week, name)
    if not f:
        raise HTTPException(404)
    return FileResponse(str(f), media_type=PPTX_MIME, filename=name)


@app.get("/api/dl/video/{pid}/{week}/{name}")
def dl_video(pid: int, week: int, name: str):
    p = need_project(pid)
    f = ws.find_video(pid, p["name"], week, name)
    if not f:
        raise HTTPException(404)
    return FileResponse(str(f), media_type="video/mp4", filename=name)


@app.get("/api/dl/credits/{pid}/{week}")
def dl_credits(pid: int, week: int):
    p = need_project(pid)
    f = ws.latest_credits(pid, p["name"], week)
    if not f:
        raise HTTPException(404)
    return _dl(f.read_bytes(), f"{deck_stem(p['form'] or {}, week)}_이미지출처.txt",
               "text/plain; charset=utf-8")


# ══ 정적 파일 ═══════════════════════════════════════════════════════════════
@app.get("/")
def index():
    return FileResponse(str(STATIC / "index.html"))


@app.get("/api/health")
def health():
    return {"ok": True}


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.on_event("startup")
def _open_browser() -> None:
    """서버가 실제로 듣기 시작한 뒤에 브라우저를 연다.

    run.bat 에서 `start ""` 로 먼저 열면 부팅(1~3초)을 앞질러
    ERR_CONNECTION_REFUSED 페이지가 뜬다 — 실제로 겪은 문제.
    """
    if os.environ.get("IDA_OPEN_BROWSER") != "1":
        return
    port = os.environ.get("IDA_PORT", "8701")
    try:
        import webbrowser
        threading.Timer(0.6, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    except Exception as e:  # noqa: BLE001
        print(f"[run] 브라우저 자동 열기 실패(무시): {e}", flush=True)
