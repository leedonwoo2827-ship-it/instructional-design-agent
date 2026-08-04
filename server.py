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
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional
from urllib.parse import quote

from dotenv import load_dotenv

load_dotenv(encoding="utf-8-sig")

from fastapi import Body, FastAPI, HTTPException, Query  # noqa: E402
from fastapi.responses import (  # noqa: E402
    FileResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse,
)
from fastapi.staticfiles import StaticFiles  # noqa: E402

from core import db  # noqa: E402
from core import deck_builder  # noqa: E402
from core import image_merge  # noqa: E402
from core import image_search  # noqa: E402
from core import llm as llm_mod  # noqa: E402
from core import pptx_font  # noqa: E402
from core import prompts  # noqa: E402
from core import user_settings as settings_mod  # noqa: E402
from core import workspace as ws  # noqa: E402
from core.pptx_export import outline_to_pptx  # noqa: E402
from core.viz import bloom_counts  # noqa: E402

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
ASSETS_DIR = ROOT / "assets"
TEMPLATE_PATH = ASSETS_DIR / "company_template.pptx"
LOGO_PATH = ASSETS_DIR / "logo.png"

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

GEN_MAX_TOKENS = 16000
SLIDE_LIST_TOKENS = 3000
SLIDE_EXPAND_TOKENS = 24000
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
        "models": settings_mod.MODELS,
        "week_choices": WEEK_CHOICES,
        "mode_choices": MODE_CHOICES,
        "slides_per_hour": SLIDES_PER_HOUR,
        "font": {"family": fs.regular, "embedded": fs.embedded},
        "template": TEMPLATE_PATH.exists(),
        "logo": LOGO_PATH.exists(),
        "workspace": str(ws.ROOT),
    }


@app.put("/api/settings")
def api_settings_put(body: Dict[str, Any] = Body(...)):
    s = settings()
    for k in ("base_url", "api_key", "model", "unsplash_key"):
        if k in body:
            setattr(s, k, str(body[k] or ""))
    for k in ("max_tokens",):
        if k in body:
            try:
                setattr(s, k, int(body[k]))
            except (TypeError, ValueError):
                pass
    if s.model not in settings_mod.MODELS:
        s.model = settings_mod.DEFAULT_MODEL
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
        "target_slides": session_hours(p["form"] or {}) * SLIDES_PER_HOUR,
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


def script_user_msg(week: int, fmt: str, note: str, syllabus_md: str, hours: int) -> str:
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
        n = session_hours(form) * SLIDES_PER_HOUR
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
#   3 비주얼 정돈   /api/slides/visual           주제 사진 수집·보관 → 재빌드
#   4 씬 프롬프트   /api/slides/prompts          슬라이드별 이미지 생성 프롬프트
#   5 이미지 합치기 /api/slides/merge            내 이미지 + 자동 사진 → 최종본
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


def _build_and_save(pid: int, p: Dict[str, Any], week: int, step: str,
                    plan: List[Dict], images: Dict[int, bytes], *,
                    embed_font: bool, credits: str = "") -> str:
    """빌드해서 **그 단계 폴더**에 다음 버전으로 저장. 단계마다 버전 계열이 따로다."""
    form = p["form"] or {}
    course = (form.get("title") or "강의").strip()
    title = deck_stem(form, week)
    data = deck_builder.build_deck(
        plan, template_path=template_arg(), images=images, deck_title=title,
        logo_path=logo_arg(), footer=f"{course} · {week}주차", embed_font=embed_font)
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
        fname = _build_and_save(pid, p, week, "draft", plan, {}, embed_font=embed_font)
        types: Dict[str, int] = {}
        for s in plan:
            t = s.get("type", "bullets")
            types[t] = types.get(t, 0) + 1
        emit("done", {"ok": True, "slides": len(plan), "file": fname, "types": types,
                      "photo_slots": len(deck_builder.image_queries(plan))})

    return stream_job(work)


# ── 3 비주얼 정돈 — 주제 사진 수집·보관 → 재빌드 ────────────────────────────
@app.post("/api/slides/visual")
def api_slides_visual(body: Dict[str, Any] = Body(...)):
    pid, week = int(body["project_id"]), int(body["week"])
    embed_font = bool(body.get("embed_font", True))
    refetch = bool(body.get("refetch", False))
    p = need_project(pid)
    plan = _need_plan(pid, p["name"], week)
    title = deck_stem(p["form"] or {}, week)

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
                                embed_font=embed_font, credits=credits)
        emit("done", {"ok": True, "photos": len(images), "slots": len(queries),
                      "reused": kept, "file": fname})

    return stream_job(work)


# ── 4 씬 프롬프트 — 슬라이드별 이미지 생성 프롬프트 ─────────────────────────
@app.post("/api/slides/prompts")
def api_slides_prompts(body: Dict[str, Any] = Body(...)):
    pid, week = int(body["project_id"]), int(body["week"])
    p = need_project(pid)
    plan = _need_plan(pid, p["name"], week)
    title = deck_stem(p["form"] or {}, week)

    def work(emit):
        emit("status", {"message": f"슬라이드 {len(plan)}장의 씬 프롬프트 작성 중…"})
        bundle = deck_builder.image_prompt_bundle(plan, title, generate_fn=_art_gen_fn())
        ws.save_week(pid, p["name"], week,
                     img_prompt=json.dumps(bundle, ensure_ascii=False, indent=2))
        placed = sum(1 for x in bundle["prompts"] if x["place"])
        emit("done", {"ok": True, "count": bundle["count"], "placeable": placed})

    return stream_job(work)


# ── 5 이미지 합치기 — 내 이미지 + 자동 사진 → 최종본 ────────────────────────
@app.post("/api/slides/merge")
def api_slides_merge(body: Dict[str, Any] = Body(...)):
    pid, week = int(body["project_id"]), int(body["week"])
    embed_font = bool(body.get("embed_font", True))
    p = need_project(pid)
    plan = _need_plan(pid, p["name"], week)

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
        fname = _build_and_save(pid, p, week, "merge", plan2, used, embed_font=embed_font)
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    return {"ok": True, "placed": len(used), "mine": len(mine.images),
            "auto": len(auto.images), "skipped": skipped,
            "report": image_merge.report(mine), "file": fname,
            "images_dir": str(idir)}


@app.post("/api/open-folder")
def api_open_folder(body: Dict[str, Any] = Body(...)):
    """탐색기로 폴더 열기(로컬 앱 전용). 실패해도 경로는 돌려준다.

    what: images(기본, 05_합치기/assets) | photos(03_비주얼/assets) | week(주차 루트)
    """
    pid, week = int(body["project_id"]), int(body["week"])
    what = body.get("what") or "images"
    p = need_project(pid)
    if what == "week":
        d = ws.week_dir(pid, p["name"], week)
    elif what == "photos":
        d = ws.assets_dir(pid, p["name"], week, "visual")
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
    raise HTTPException(404)


@app.get("/api/dl/deck/{pid}/{week}/{name}")
def dl_deck(pid: int, week: int, name: str):
    p = need_project(pid)
    f = ws.find_deck(pid, p["name"], week, name)
    if not f:
        raise HTTPException(404)
    return FileResponse(str(f), media_type=PPTX_MIME, filename=name)


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
