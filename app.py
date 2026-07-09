# -*- coding: utf-8 -*-
"""교수설계 가이드 에이전트 — Streamlit + Ubion LiteLLM 프록시.

원본 _context/교수설계-에이전트.html 의 화면 구성(상단 STEP 바 · 좌측 입력 / 우측 출력
2단 레이아웃)과 브랜드 디자인을 이식했다.

호출 경로: Streamlit → openai SDK → 사내 LiteLLM 프록시(/v1/chat/completions).
URL·API 키·모델은 상단 '연결 설정'(접기/펴기)에서 입력하며 data/user_settings.json 에 저장.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import markdown as md_lib
import streamlit as st
from dotenv import load_dotenv

load_dotenv(encoding="utf-8-sig")  # 메모장 저장 .env 의 BOM 허용

from core import db  # noqa: E402
from core import llm as llm_mod  # noqa: E402
from core import prompts  # noqa: E402
from core import user_settings as settings_mod  # noqa: E402
from core.pptx_export import outline_to_pptx  # noqa: E402
from core.viz import (  # noqa: E402
    ICON_DOC, ICON_INFO, ICON_SLIDE, bloom_chart_html, bloom_counts,
)

# 회사 PPT 양식(.pptx) — 있으면 PPTX 생성 시 테마·마스터를 상속. (커밋 제외)
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
TEMPLATE_PATH = ASSETS_DIR / "company_template.pptx"


def template_arg():
    return str(TEMPLATE_PATH) if TEMPLATE_PATH.exists() else None

st.set_page_config(
    page_title="교수설계 가이드 에이전트",
    page_icon="교",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_resource
def _init_db():
    db.init_db()
    return True


_init_db()

# 밝은 에디토리얼 디자인 (Figma design.md 기반: 흰 캔버스·헤어라인·알약 버튼·파스텔·그림자 최소)
_CSS = """
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css');
:root{
  --canvas:#ffffff; --bg:#fbfbfd; --ink:#141518; --ink2:#5b6472;
  --line:#ececee; --line-soft:#f2f2f4;
  --brand:#3b4ec8; --brand2:#2c3aa0; --brand-soft:#eef0fb;
  --lilac:#efeaff; --lime:#eef6dd; --cream:#fbf3e6; --mint:#e6f6ef;
  --ok:#0e9f6e; --warn:#b45309; --err:#cc4117;
}
html,body,.stApp,[class*="css"]{font-family:'Pretendard',-apple-system,'Malgun Gothic',sans-serif;}
.stApp{background:var(--bg);}
[data-testid="stHeader"]{background:transparent;}
/* 사이드바 넓게 — 접힘(aria-expanded=false) 시 완전히 숨겨 삐짐 방지 */
[data-testid="stSidebar"]{min-width:460px;max-width:460px;}
[data-testid="stSidebar"][aria-expanded="false"]{min-width:460px;max-width:460px;margin-left:-460px;}
.block-container{max-width:1240px;padding-top:1rem;padding-bottom:4rem;}

/* 헤더 — 밝은 에디토리얼(그라데이션 로고 제거) */
.ida-header{background:var(--canvas);border:1px solid var(--line);border-radius:20px;
  padding:18px 22px;margin-bottom:14px;box-shadow:none;}
.ida-eyebrow{font-family:'SF Mono','JetBrains Mono',Consolas,monospace;font-size:11px;
  letter-spacing:.16em;text-transform:uppercase;color:var(--ink2);margin-bottom:5px;}
.ida-title{font-weight:800;font-size:22px;letter-spacing:-0.03em;color:var(--ink);line-height:1.1;}
.ida-sub{font-size:13px;color:var(--ink2);margin-top:4px;}
.ida-panel-title{font-weight:700;font-size:14.5px;color:var(--ink);margin:2px 0 10px;
  letter-spacing:-0.01em;display:flex;align-items:center;}

/* 카드·컨테이너·expander — 헤어라인, 그림자 없음 */
[data-testid="stVerticalBlockBorderWrapper"]{border-radius:20px;border-color:var(--line)!important;}
[data-testid="stExpander"]{border:1px solid var(--line);border-radius:16px;background:var(--canvas);
  box-shadow:none;margin-bottom:12px;}
[data-testid="stExpander"] summary{font-weight:700;color:var(--ink);}
[data-testid="stExpander"] summary:hover{color:var(--brand);}

/* 버튼 — 알약(pill) */
.stButton>button,.stDownloadButton>button,.stFormSubmitButton>button{
  border-radius:999px;font-weight:600;border:1px solid var(--line);background:var(--canvas);
  color:var(--ink);transition:.15s;}
.stButton>button:hover,.stDownloadButton>button:hover{border-color:var(--brand);color:var(--brand);}
.stButton>button[kind="primary"],.stFormSubmitButton>button[kind="primary"],
.stFormSubmitButton>button[kind="primaryFormSubmit"]{
  background:var(--brand);border-color:var(--brand);color:#fff;}
.stButton>button[kind="primary"]:hover,.stFormSubmitButton>button[kind="primary"]:hover,
.stFormSubmitButton>button[kind="primaryFormSubmit"]:hover{
  background:var(--brand2);border-color:var(--brand2);color:#fff;}

/* 입력 */
.stTextInput input,.stTextArea textarea{border-radius:10px;border-color:var(--line);background:var(--canvas);}
.stTextInput input:focus,.stTextArea textarea:focus{border-color:var(--brand);box-shadow:none;}

/* 안내/정보 박스 — 파스텔 블록(Figma 시그니처) */
[data-testid="stAlertContainer"]{border-radius:16px;border:none;}

/* 교재/PPT 탭 — 알약 */
[data-baseweb="tab-list"]{gap:8px;border-bottom:none;}
[data-baseweb="tab"]{background:var(--canvas);border:1px solid var(--line)!important;border-radius:999px;
  padding:6px 16px;font-weight:600;}
[data-baseweb="tab"][aria-selected="true"]{background:var(--brand);color:#fff;border-color:var(--brand)!important;}
[data-baseweb="tab-highlight"],[data-baseweb="tab-border"]{display:none!important;}

/* 산출물 마크다운 */
[data-testid="stMarkdownContainer"] h1{font-size:24px;letter-spacing:-0.02em;border-bottom:1px solid var(--line);padding-bottom:8px;}
[data-testid="stMarkdownContainer"] h2{font-size:18px;letter-spacing:-0.015em;color:var(--ink);margin-top:24px;}
[data-testid="stMarkdownContainer"] h3{font-size:15px;}
[data-testid="stMarkdownContainer"] table{border-collapse:collapse;width:100%;font-size:13px;margin:12px 0;}
[data-testid="stMarkdownContainer"] th{background:var(--lilac);color:var(--ink);font-weight:700;text-align:left;}
[data-testid="stMarkdownContainer"] th,[data-testid="stMarkdownContainer"] td{border:1px solid var(--line);padding:8px 11px;}
[data-testid="stMarkdownContainer"] tr:nth-child(even) td{background:#fafafa;}
[data-testid="stMarkdownContainer"] blockquote{border-left:3px solid var(--brand);background:var(--brand-soft);
  padding:8px 14px;border-radius:0 10px 10px 0;}
[data-testid="stMarkdownContainer"] code{background:#f2f2f4;border-radius:5px;padding:1px 5px;}
</style>
"""
st.markdown(_CSS, unsafe_allow_html=True)

WEEK_CHOICES = [8, 10, 13, 15, 16]
MODE_CHOICES = ["대면", "온라인(실시간)", "온라인(비동기·동영상)", "혼합(블렌디드)", "플립러닝"]
STEP_META = [
    (1, "강의 정보 입력", "과목 · 대상 · 운영 방식"),
    (2, "강의계획서", "목표–평가–주차 정렬"),
    (3, "원고", "교재 / PPT 개요"),
]
REFINE_TMPL = (
    "다음 요청대로 수정하여, 수정된 문서 전체를 다시 출력해 주세요. "
    "수정 시에도 학습목표 정렬 원칙(측정 가능 동사, 목표–평가–활동 인지수준 일치)을 유지하세요.\n\n요청: {req}"
)

# 교재/PPT(장문·다수 슬라이드)는 넉넉한 토큰으로 생성(추론 모델 잘림 방지). 설정값보다 크면 그 값 사용.
GEN_MAX_TOKENS = 16000
SLIDE_LIST_TOKENS = 3000      # 슬라이드 제목 목록(1단계)
SLIDE_EXPAND_TOKENS = 24000   # 슬라이드 상세(2단계, 다수 슬라이드)
SLIDES_PER_HOUR = 20          # 1시간당 슬라이드 수


# ---------------------------------------------------------------------------
# 세션 상태
# ---------------------------------------------------------------------------
ss = st.session_state
ss.setdefault("settings", settings_mod.load())
ss.setdefault("step", 1)
ss.setdefault("project_id", None)     # 현재 프로젝트(강의) DB id
ss.setdefault("syllabus_md", "")
ss.setdefault("syllabus_msgs", [])
ss.setdefault("script_week", 1)
ss.setdefault("script_doc_md", "")   # 교재
ss.setdefault("script_ppt_md", "")   # PPT 개요
ss.setdefault("script_doc_msgs", [])
ss.setdefault("script_ppt_msgs", [])
ss.setdefault("ping_status", None)
ss.setdefault("form", {})
ss.setdefault("had_key", bool((ss.settings.api_key or "").strip()))


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------
def ensure_ready() -> bool:
    if not (ss.settings.api_key or "").strip():
        st.warning("상단 '연결 설정'에서 API 키를 입력하고 저장하세요.")
        return False
    return True


def clear_artifacts() -> None:
    ss.form = {}
    ss.syllabus_md, ss.syllabus_msgs = "", []
    ss.script_doc_md, ss.script_ppt_md = "", ""
    ss.script_doc_msgs, ss.script_ppt_msgs = [], []
    ss.script_week, ss.step = 1, 1


def load_project_into_session(pid: int) -> None:
    p = db.load_project(pid)
    if not p:
        return
    ss.project_id = p["id"]
    ss.form = p["form"] or {}
    ss.syllabus_md, ss.syllabus_msgs = p["syllabus_md"], p["syllabus_msgs"]
    ss.script_week = p["script_week"] or 1
    ss.script_doc_md, ss.script_doc_msgs = p["script_doc_md"], p["script_doc_msgs"]
    ss.script_ppt_md, ss.script_ppt_msgs = p["script_ppt_md"], p["script_ppt_msgs"]


def persist() -> None:
    """현재 세션 산출물을 DB에 저장(프로젝트 없으면 과목명으로 자동 생성). 이름은 건드리지 않음."""
    if not ss.project_id:
        name = (ss.form.get("title") or "").strip() or "새 강의"
        ss.project_id = db.create_project(name)
    db.save_project(
        ss.project_id,
        form=ss.form,
        syllabus_md=ss.syllabus_md, syllabus_msgs=ss.syllabus_msgs,
        script_week=ss.script_week,
        script_doc_md=ss.script_doc_md, script_doc_msgs=ss.script_doc_msgs,
        script_ppt_md=ss.script_ppt_md, script_ppt_msgs=ss.script_ppt_msgs,
    )


def stream_into(placeholder, system: str, messages: list, label: str = "생성",
                max_tokens: int = None) -> str:
    provider = llm_mod.build_provider(ss.settings)
    mt = max(max_tokens or 0, ss.settings.max_tokens)
    full = ""
    placeholder.markdown("**작성 중…** 잠시만 기다려 주세요. 내용이 이 영역에 실시간으로 나타납니다. ▌")
    t0 = time.time()
    last = 0
    print(f"[{label}] 시작 · 모델={ss.settings.model} · max_tokens={mt}", flush=True)
    try:
        for delta in provider.stream(system, messages,
                                     max_tokens=mt,
                                     temperature=ss.settings.temperature):
            full += delta
            placeholder.markdown(full + " ▌")
            if len(full) - last >= 400:
                last = len(full)
                print(f"[{label}] …{len(full):,}자 생성 중 ({time.time() - t0:.0f}s)", flush=True)
        placeholder.markdown(full)
        print(f"[{label}] 완료 · {len(full):,}자 · {time.time() - t0:.1f}s", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[{label}] 오류: {type(e).__name__}: {e}", flush=True)
        st.error(f"생성 실패: {type(e).__name__}: {e}\n\n사내망 연결과 API 키를 확인하세요.")
        if full:
            placeholder.markdown(full)
    return full


def md_to_doc_bytes(md_text: str) -> bytes:
    body = md_lib.markdown(md_text or "", extensions=["tables", "fenced_code"])
    html = (
        "<html xmlns:o='urn:schemas-microsoft-com:office:office' "
        "xmlns:w='urn:schemas-microsoft-com:office:word'><head><meta charset='utf-8'>"
        "<style>body{font-family:'Malgun Gothic',sans-serif;font-size:11pt;line-height:1.6}"
        "table{border-collapse:collapse;width:100%}th,td{border:1px solid #999;padding:5pt;font-size:10pt}"
        "th{background:#eef}h1{font-size:16pt}h2{font-size:13pt;color:#2c3aa0}h3{font-size:11.5pt}</style>"
        f"</head><body>{body}</body></html>"
    )
    return ("﻿" + html).encode("utf-8")


def syllabus_user_msg(f: dict) -> str:
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


def session_hours() -> int:
    try:
        return int(ss.form.get("hours", 2) or 2)
    except (TypeError, ValueError):
        return 2


def script_user_msg(week: int, fmt: str, note: str, syllabus_md: str) -> str:
    kind = "학생용 교재(읽기 자료)" if fmt == "doc" else "PPT 슬라이드 개요"
    extra = f"\n[교수자 추가 요청] {note}\n" if note.strip() else ""
    vol = ""
    if fmt == "ppt":
        h = session_hours()
        vol = (f"\n이 차시는 약 {h}시간 수업입니다. 슬라이드는 1시간당 약 {SLIDES_PER_HOUR}장 기준, "
               f"표지 제외 본문 약 {h * SLIDES_PER_HOUR}장으로 작성하세요. 발표자 노트는 넣지 마세요.")
    return (
        f"아래는 확정된 강의계획서입니다. 이 계획서의 {week}주차에 대한 {kind}를 작성해 주세요. "
        f"반드시 계획서의 해당 주차 목표와 강좌 목표(G#)를 상속하세요.{extra}{vol}\n"
        f"=== 강의계획서 ===\n{syllabus_md}"
    )


def out_name(kind: str) -> str:
    """kind: 'syllabus' | '교재' | 'PPT개요'"""
    title = (ss.form.get("title") or "강의").strip() or "강의"
    if kind == "syllabus":
        return f"{title}_강의계획서"
    return f"{title}_{ss.script_week}주차_{kind}"


def run_pending(pending: dict, placeholder) -> None:
    """예약된 생성/수정/점검을 우측 placeholder 에 실행. doc: syllabus | script_doc | script_ppt."""
    kind, doc = pending["kind"], pending["doc"]
    if doc == "syllabus":
        if kind == "check":
            msgs = [{"role": "user", "content": f"다음 산출물을 점검해 주세요.\n\n{ss.syllabus_md}"}]
            rep = stream_into(placeholder, prompts.SYS_CHECK_SYL, msgs, label="강의계획서 점검")
            if rep:
                ss.syllabus_md += f"\n\n---\n\n## 정렬 점검 보고\n\n{rep}"
        else:
            full = stream_into(placeholder, prompts.SYS_SYLLABUS, ss.syllabus_msgs, label="강의계획서")
            if full:
                ss.syllabus_md = full
                ss.syllabus_msgs.append({"role": "assistant", "content": full})
        return

    # script_doc(교재) | script_ppt(PPT 개요)
    is_doc = doc == "script_doc"
    md_key = "script_doc_md" if is_doc else "script_ppt_md"
    msgs_key = "script_doc_msgs" if is_doc else "script_ppt_msgs"
    wk = f"{ss.script_week}주차 " + ("교재" if is_doc else "PPT 개요")

    # ── PPT 생성: 2단계(제목 목록 → 상세)로 슬라이드 개수 보장 ──
    if (not is_doc) and kind == "gen":
        n = session_hours() * SLIDES_PER_HOUR
        provider = llm_mod.build_provider(ss.settings)
        placeholder.markdown(f"**슬라이드 목록 구성 중…** (목표 {n}장)")
        list_user = (
            f"'{ss.script_week}주차' 강의를 정확히 {n}장 슬라이드로 구성합니다. "
            f"도입(2~3장) · 선수지식(1~2장) · 개념 설명(대부분, 개념마다 정의·예시·비교·적용으로 여러 장 분절) "
            f"· 사례/활동 · 형성평가(1~2장) · 요약·예고(1~2장) 순서로, "
            f"슬라이드 {n}개의 '제목'만 1.부터 {n}.까지 번호 목록으로 출력하세요. "
            f"정확히 {n}줄, 제목 외 다른 말은 쓰지 마세요.\n\n=== 강의계획서 ===\n{ss.syllabus_md}"
        )
        try:
            titles = provider.generate(
                "너는 슬라이드 제목 목록만 출력한다. 머리말·설명 없이 '1. 제목' 형식 번호 목록만.",
                [{"role": "user", "content": list_user}],
                max_tokens=SLIDE_LIST_TOKENS, temperature=0.4)
        except Exception as e:  # noqa: BLE001
            print(f"[슬라이드 목록] 오류: {e}", flush=True)
            titles = ""
        exp_user = (
            f"아래 슬라이드 제목 목록({n}장)의 **모든 {n}개** 슬라이드를 각각 상세 개요로 작성하세요. "
            f"반드시 {n}개의 '### 슬라이드 N — 제목' 블록을 만들고, 각 블록에 "
            f"- 레이아웃 제안(섹션 표지/2단/콘텐츠) / - 핵심 메시지(1개) / - 본문 개요(불릿 3~5개)를 넣으세요. "
            f"발표자 노트는 넣지 마세요. 맨 앞에 차시 학습목표와 슬라이드 구성 개요 표도 포함하세요.\n\n"
            f"[슬라이드 제목 목록]\n{titles}\n\n[강의계획서]\n{ss.syllabus_md}"
        )
        msgs = [{"role": "user", "content": exp_user}]
        full = stream_into(placeholder, prompts.SYS_SCRIPT_PPT, msgs, label=wk, max_tokens=SLIDE_EXPAND_TOKENS)
        if full:
            ss[md_key] = full
            ss[msgs_key] = msgs + [{"role": "assistant", "content": full}]
        return

    sys_gen = prompts.SYS_SCRIPT_DOC if is_doc else prompts.SYS_SCRIPT_PPT
    if kind == "check":
        cur = ss[md_key]
        msgs = [{"role": "user", "content": f"다음 산출물을 점검해 주세요.\n\n{cur}"}]
        rep = stream_into(placeholder, prompts.SYS_CHECK_SCR, msgs, label=f"{wk} 점검")
        if rep:
            ss[md_key] = cur + f"\n\n---\n\n## 정렬 점검 보고\n\n{rep}"
    else:  # 교재 gen | refine(교재·PPT)
        full = stream_into(placeholder, sys_gen, ss[msgs_key], label=wk, max_tokens=GEN_MAX_TOKENS)
        if full:
            ss[md_key] = full
            ss[msgs_key].append({"role": "assistant", "content": full})


def render_syllabus_panel() -> None:
    """강의계획서 출력 패널(다운로드·차트·스트리밍·수정·직접편집). STEP1/2 공용."""
    with st.container(border=True):
        hc = st.columns([3, 1.1, 1.5])
        hc[0].markdown(f'<div class="ida-panel-title">{ICON_DOC}강의계획서</div>', unsafe_allow_html=True)
        if ss.syllabus_md:
            hc[1].download_button("MD", ss.syllabus_md, file_name=out_name("syllabus") + ".md",
                                  mime="text/markdown", use_container_width=True, key="dl_syl_md")
            hc[2].download_button("DOC 저장", md_to_doc_bytes(ss.syllabus_md),
                                  file_name=out_name("syllabus") + ".doc",
                                  mime="application/msword", use_container_width=True, key="dl_syl_doc")
        if ss.syllabus_md:
            st.caption(f"분량 {len(ss.syllabus_md):,}자")
        if ss.syllabus_md and not pending:
            _chart = bloom_chart_html(bloom_counts(ss.syllabus_md))
            if _chart:
                st.markdown(_chart, unsafe_allow_html=True)

        out_ph = st.empty()
        if pending and pending.get("doc") == "syllabus":
            _m = "수정 반영 중…" if pending["kind"] == "refine" else (
                "정렬 점검 중… (Bloom 분포 · 목표–평가 정렬)" if pending["kind"] == "check"
                else "강의계획서 작성 중… (목표 설계 → 주차 분해 → 정렬 매트릭스)")
            with st.spinner(_m):
                run_pending(pending, out_ph)
            persist()
            st.rerun()
        elif ss.syllabus_md:
            out_ph.markdown(ss.syllabus_md)
        else:
            out_ph.info("좌측 사이드바 '강의 기본 정보'를 입력하고 강의계획서를 생성하세요.")

        if ss.syllabus_md and not pending:
            st.divider()
            rc = st.columns([4, 1])
            req = rc[0].text_input("수정 요청", key="refine_syllabus", label_visibility="collapsed",
                                   placeholder="수정 요청 — 예: 7주차 목표를 '분석' 수준으로 높여줘")
            if rc[1].button("수정 요청", key="refbtn_syllabus", use_container_width=True):
                if req.strip() and ensure_ready():
                    ss.syllabus_msgs.append({"role": "user", "content": REFINE_TMPL.format(req=req)})
                    ss._pending = {"kind": "refine", "doc": "syllabus"}
                    st.rerun()
            with st.expander("직접 편집 (마크다운)"):
                _ed = st.text_area("강의계획서 직접 편집", value=ss.syllabus_md, height=420,
                                   key="edit_syllabus", label_visibility="collapsed")
                if st.button("편집 저장", key="savedit_syllabus", use_container_width=True):
                    ss.syllabus_md = _ed
                    ss.syllabus_msgs = [
                        {"role": "user", "content": "현재 강의계획서(직접 편집본)를 기준으로 이어서 작업합니다."},
                        {"role": "assistant", "content": _ed},
                    ]
                    persist()
                    st.success("편집 내용을 저장했습니다.")
                    st.rerun()


# ---------------------------------------------------------------------------
# 사이드바 — 강의 프로젝트 · 강의 기본 정보 · 연결 설정
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(f'<div class="ida-panel-title">{ICON_DOC}강의 프로젝트</div>', unsafe_allow_html=True)
    _projects = db.list_projects()
    _labels = ["＋ 새 프로젝트"] + [f'{p["name"]} · {p["updated_at"][5:16]}' for p in _projects]
    _ids = [None] + [p["id"] for p in _projects]
    _cur = _ids.index(ss.project_id) if ss.project_id in _ids else 0
    _sel = st.selectbox("프로젝트 열기", range(len(_labels)), index=_cur,
                        format_func=lambda i: _labels[i], label_visibility="collapsed")
    if _ids[_sel] != ss.project_id:
        if _ids[_sel] is None:
            clear_artifacts()
            ss.project_id = None
        else:
            load_project_into_session(_ids[_sel])
        st.rerun()

    if ss.project_id is None:
        _nm = st.text_input("새 프로젝트 이름", value="", placeholder="예: 교육방법 및 교육공학")
        if st.button("새로 만들기", use_container_width=True, type="primary"):
            clear_artifacts()
            ss.project_id = db.create_project(_nm.strip() or "새 강의")
            st.rerun()
    else:
        _p = next((p for p in _projects if p["id"] == ss.project_id), None)
        st.caption(f"현재 프로젝트: {_p['name'] if _p else '(저장 전)'}")
        with st.expander("이름 변경 · 삭제"):
            _rn = st.text_input("새 이름", value=(_p["name"] if _p else ""), key="rename_input")
            rc1, rc2 = st.columns(2)
            if rc1.button("이름 저장", use_container_width=True):
                db.rename_project(ss.project_id, _rn.strip() or "새 강의")
                st.rerun()
            if rc2.button("삭제", use_container_width=True):
                db.delete_project(ss.project_id)
                ss.project_id = None
                clear_artifacts()
                st.rerun()
    st.divider()

    # ── 강의 기본 정보 (입력) — 2열 배치 ──
    with st.expander("강의 기본 정보", expanded=not ss.syllabus_md):
        with st.form("lecture_form"):
            fc = st.columns(2)
            f_title = fc[0].text_input("과목명 *", value=ss.form.get("title", ""), placeholder="예: 교육공학의 이해")
            f_field = fc[1].text_input("학문 분야", value=ss.form.get("field", ""), placeholder="예: 교육학")
            fc = st.columns(2)
            f_target = fc[0].text_input("수강 대상 *", value=ss.form.get("target", ""), placeholder="예: 학부 2학년")
            f_credit = fc[1].text_input("학점 / 시수", value=ss.form.get("credit", ""), placeholder="예: 3학점, 주 3시간")
            fc = st.columns(2)
            f_weeks = fc[0].selectbox("총 주차", WEEK_CHOICES,
                                      index=WEEK_CHOICES.index(ss.form.get("weeks", 15))
                                      if ss.form.get("weeks", 15) in WEEK_CHOICES else 3)
            f_mode = fc[1].selectbox("강의 방식", MODE_CHOICES,
                                     index=MODE_CHOICES.index(ss.form.get("mode", "대면"))
                                     if ss.form.get("mode", "대면") in MODE_CHOICES else 0)
            f_hours = st.number_input(f"차시 수업 시간(시간) — 슬라이드는 시간당 약 {SLIDES_PER_HOUR}장",
                                      min_value=1, max_value=6, step=1,
                                      value=int(ss.form.get("hours", 2) or 2))
            f_topics = st.text_area("주요 내용 · 주제 *", value=ss.form.get("topics", ""),
                                    placeholder="예: 교수설계 이론, ADDIE 모형, 학습목표 설계, 매체 활용 등")
            fc = st.columns(2)
            f_learner = fc[0].text_input("수강생 특성", value=ss.form.get("learner", ""),
                                         placeholder="예: 전공 기초 이수, 일부 현직 교사")
            f_policy = fc[1].text_input("평가 선호·수업 철학", value=ss.form.get("policy", ""),
                                        placeholder="예: 과정 중심 40%, 토론 중심")
            submitted = st.form_submit_button("강의계획서 생성 →", type="primary", use_container_width=True)
        if submitted:
            if not f_title.strip() or not f_topics.strip():
                st.warning("과목명과 주요 내용은 필수입니다.")
            elif ensure_ready():
                ss.form = dict(title=f_title, field=f_field, target=f_target, credit=f_credit,
                               weeks=f_weeks, mode=f_mode, hours=f_hours,
                               topics=f_topics, learner=f_learner, policy=f_policy)
                ss.syllabus_msgs = [{"role": "user", "content": syllabus_user_msg(ss.form)}]
                ss.step = 2
                ss._pending = {"kind": "gen", "doc": "syllabus"}
                st.rerun()

    # ── 연결 설정 ──
    _key_ok = bool((ss.settings.api_key or "").strip())
    with st.expander(f"연결 설정 — {'연결됨 ✓' if _key_ok else 'API 키 입력'}", expanded=not ss.had_key):
        s = ss.settings
        s.base_url = st.text_input("LiteLLM URL", value=s.base_url)
        s.api_key = st.text_input("API 키", value=s.api_key, type="password",
                                  help="사내 대시보드(/ui/)에서 발급한 sk- 키")
        _mids = list(settings_mod.MODELS.keys())
        s.model = st.selectbox("모델", _mids, index=_mids.index(s.model) if s.model in _mids else 0,
                               format_func=lambda m: settings_mod.MODELS[m])
        bc1, bc2 = st.columns(2)
        if bc1.button("연결 테스트", use_container_width=True):
            with st.spinner("확인 중…"):
                ss.ping_status = llm_mod.build_provider(s).ping()
        if bc2.button("저장", use_container_width=True, type="primary"):
            settings_mod.save(s)
            ss.had_key = bool((s.api_key or "").strip())
            st.rerun()
        if ss.ping_status:
            ok, msg = ss.ping_status
            (st.success if ok else st.error)(msg)

    st.caption("산출물은 자동 저장됩니다 (data/app.db · 로컬, GitHub 미포함).")


# ---------------------------------------------------------------------------
# 헤더
# ---------------------------------------------------------------------------
st.markdown(
    '<div class="ida-header">'
    '<div class="ida-eyebrow">AI · 교수설계 스튜디오</div>'
    '<div class="ida-title">교수설계 가이드 에이전트</div>'
    '<div class="ida-sub">ABCD 학습목표 · Bloom 정렬 · 백워드 설계(WHERETO) · Mayer 멀티미디어 원리 기반</div>'
    '</div>',
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# STEP 바
# ---------------------------------------------------------------------------
sb = st.columns(3)
for idx, (n, tit, desc) in enumerate(STEP_META):
    with sb[idx]:
        active = ss.step == n
        if st.button(f"{n}   {tit}", key=f"stepbtn{n}", use_container_width=True,
                     type="primary" if active else "secondary"):
            ss.step = n
            st.rerun()
        st.caption(desc)

# 좌측 버튼이 예약한 작업 (생성/수정/점검) — 우측에서 소비
pending = ss.get("_pending")
if "_pending" in ss:
    del ss["_pending"]

st.write("")

# ===========================================================================
# STEP 3 — 전체 폭: 상단 컨트롤 + 교재/PPT 탭 (PPTX 버튼이 상단에서 바로 보임)
# ===========================================================================
if ss.step == 3:
    with st.container(border=True):
        st.markdown(f'<div class="ida-panel-title">{ICON_SLIDE}산출물 생성 · STEP 3</div>', unsafe_allow_html=True)
        if not ss.syllabus_md:
            st.info("산출물은 강의계획서의 주차 목표를 상속합니다. 먼저 STEP 1~2에서 강의계획서를 생성하세요.")
        else:
            st.caption("선택한 주차에 대해 **학생용 교재**와 **PPT 개요**를 함께 생성합니다. 아래 탭에서 각각 확인·수정·점검·저장하세요.")
            n_weeks = int(ss.form.get("weeks", 15))
            week_opts = list(range(1, n_weeks + 1))
            cc = st.columns([1.3, 3.4, 1.9])
            ss.script_week = cc[0].selectbox("대상 주차", week_opts,
                                             index=week_opts.index(ss.script_week)
                                             if ss.script_week in week_opts else 0,
                                             format_func=lambda w: f"{w}주차")
            note = cc[1].text_input("해당 차시 요청사항 (선택)", key="script_note",
                                    placeholder="예: 사례 중심으로, 조별 토론 20분 포함, 동영상 강의용 등")
            cc[2].markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
            if cc[2].button("교재 + PPT 개요 생성 →", type="primary", use_container_width=True):
                if ensure_ready():
                    ss.script_doc_msgs = [{"role": "user",
                                           "content": script_user_msg(ss.script_week, "doc", note, ss.syllabus_md)}]
                    ss.script_ppt_msgs = [{"role": "user",
                                           "content": script_user_msg(ss.script_week, "ppt", note, ss.syllabus_md)}]
                    ss._pending = {"kind": "gen_both", "doc": "script"}
                    st.rerun()

            _tpl_on = TEMPLATE_PATH.exists()
            with st.expander(f"PPT 회사 양식(.pptx) — {'적용됨 ✓' if _tpl_on else '기본 양식 사용 중'}"):
                st.caption("회사 양식 .pptx 를 올리면 PPTX 저장 시 그 테마·마스터·폰트·레이아웃을 상속합니다. "
                           "(Anthropic pptx 스킬의 템플릿 기반 방식) 양식 파일은 로컬 assets/ 에만 저장되고 GitHub 에 올라가지 않습니다.")
                up = st.file_uploader("회사 양식 업로드 (.pptx / .potx)", type=["pptx", "potx"], key="tpl_up")
                if up is not None:
                    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
                    TEMPLATE_PATH.write_bytes(up.getvalue())
                    st.success("회사 양식이 적용되었습니다. 이후 생성/저장되는 PPTX 에 반영됩니다.")
                if _tpl_on and st.button("양식 제거(기본으로 되돌리기)", key="tpl_rm"):
                    TEMPLATE_PATH.unlink(missing_ok=True)
                    st.rerun()

    if ss.syllabus_md:
        def render_script_tab(md_key, doc_key, is_ppt):
            """탭 내부: 상단 다운로드 버튼 → 본문/컨트롤용 placeholder 반환."""
            cur = ss[md_key]
            fn = out_name("PPT개요" if is_ppt else "교재")
            if cur:
                dc = st.columns([1, 1, 1, 6]) if is_ppt else st.columns([1, 1, 7])
                dc[0].download_button("MD", cur, file_name=fn + ".md", mime="text/markdown",
                                      key=f"md_{doc_key}", use_container_width=True)
                dc[1].download_button("DOC", md_to_doc_bytes(cur), file_name=fn + ".doc",
                                      mime="application/msword", key=f"doc_{doc_key}", use_container_width=True)
                if is_ppt:
                    pptx = outline_to_pptx(cur, deck_title=fn, template_path=template_arg())
                    if pptx:
                        dc[2].download_button(
                            "PPTX", pptx, file_name=fn + ".pptx",
                            mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                            key=f"pptx_{doc_key}", use_container_width=True)
                # 분량 지표 (대학강의 분량 판별용 · 참고 목표는 잠정)
                if is_ppt:
                    n_slides = len(re.findall(r"(?m)^\s*#{2,3}\s*슬라이드", cur))
                    st.caption(f"슬라이드 {n_slides}장 · {len(cur):,}자  ·  목표 약 {session_hours() * SLIDES_PER_HOUR}장({session_hours()}시간)")
                else:
                    st.caption(f"교재 {len(cur):,}자 · 약 {len(cur) / 1800:.1f}쪽(A4)  ·  참고 목표 7쪽↑")
            return st.empty(), st.container()

        tab_doc, tab_ppt = st.tabs(["교재", "PPT 개요"])
        with tab_doc:
            doc_ph, doc_ctrl = render_script_tab("script_doc_md", "script_doc", False)
        with tab_ppt:
            ppt_ph, ppt_ctrl = render_script_tab("script_ppt_md", "script_ppt", True)

        pdoc = pending.get("doc") if pending else None
        if pending and pending["kind"] == "gen_both":
            with st.spinner(f"{ss.script_week}주차 교재 작성 중…"):
                run_pending({"kind": "gen", "doc": "script_doc"}, doc_ph)
            with st.spinner(f"{ss.script_week}주차 PPT 개요 작성 중…"):
                run_pending({"kind": "gen", "doc": "script_ppt"}, ppt_ph)
            persist()
            st.rerun()
        elif pending and pdoc in ("script_doc", "script_ppt"):
            tph = doc_ph if pdoc == "script_doc" else ppt_ph
            _m = {"refine": "수정 반영 중…", "check": "정렬 점검 중…"}.get(pending["kind"], "작성 중…")
            with st.spinner(_m):
                run_pending(pending, tph)
            persist()
            st.rerun()
        else:
            for ph, ctrl, md_key, msgs_key, doc_key, hint in [
                (doc_ph, doc_ctrl, "script_doc_md", "script_doc_msgs", "script_doc",
                 "위 '교재 + PPT 개요 생성'을 누르면 교재가 여기에 표시됩니다."),
                (ppt_ph, ppt_ctrl, "script_ppt_md", "script_ppt_msgs", "script_ppt",
                 "PPT 개요가 여기에 표시됩니다. (MD·DOC·PPTX 저장 지원)"),
            ]:
                if ss[md_key]:
                    ph.markdown(ss[md_key])
                    with ctrl:
                        rc = st.columns([4, 1, 1.3])
                        req = rc[0].text_input("수정", key=f"refine_{doc_key}", label_visibility="collapsed",
                                               placeholder="수정 요청 — 예: 예시를 더 추가해줘 / 분량을 줄여줘")
                        if rc[1].button("수정", key=f"refbtn_{doc_key}", use_container_width=True):
                            if req.strip() and ensure_ready():
                                ss[msgs_key].append({"role": "user", "content": REFINE_TMPL.format(req=req)})
                                ss._pending = {"kind": "refine", "doc": doc_key}
                                st.rerun()
                        if rc[2].button("정렬 점검", key=f"chk_{doc_key}", use_container_width=True):
                            if ensure_ready():
                                ss._pending = {"kind": "check", "doc": doc_key}
                                st.rerun()
                        with st.expander("직접 편집 (마크다운)"):
                            _ed = st.text_area("직접 편집", value=ss[md_key], height=380,
                                               key=f"edit_{doc_key}", label_visibility="collapsed")
                            if st.button("편집 저장", key=f"savedit_{doc_key}", use_container_width=True):
                                ss[md_key] = _ed
                                ss[msgs_key] = [
                                    {"role": "user", "content": "현재 문서(직접 편집본)를 기준으로 이어서 작업합니다."},
                                    {"role": "assistant", "content": _ed},
                                ]
                                persist()
                                st.success("편집 내용을 저장했습니다.")
                                st.rerun()
                else:
                    ph.info(hint)

# ===========================================================================
# STEP 1 — 강의계획서 전체 폭 (입력은 사이드바)
# ===========================================================================
elif ss.step == 1:
    render_syllabus_panel()

# ===========================================================================
# STEP 2 — 상단 컨트롤 + 전체 폭 강의계획서
# ===========================================================================
else:
    with st.container(border=True):
        st.markdown(f'<div class="ida-panel-title">{ICON_INFO}강의계획서 점검 · STEP 2</div>', unsafe_allow_html=True)
        st.caption("① 정렬 점검: 강의계획서가 실무형 기준을 지켰는지 AI가 검토해 수정 제안을 문서 하단에 덧붙입니다.")
        _checked = "정렬 점검 보고" in (ss.syllabus_md or "")  # 점검 완료 시 강조를 ②로 이동
        _busy = bool(pending)  # 생성/점검/수정 진행 중이면 버튼 비활성화
        bc = st.columns([1.8, 2.6, 3.2])
        if bc[0].button("① 정렬 점검 실행", use_container_width=True,
                        type=("secondary" if _checked else "primary"),
                        disabled=_busy or not ss.syllabus_md):
            if ss.syllabus_md and ensure_ready():
                ss._pending = {"kind": "check", "doc": "syllabus"}
                st.rerun()
            elif not ss.syllabus_md:
                st.warning("먼저 강의계획서를 생성하세요.")
        if bc[1].button("② 산출물(교재·PPT) 작성으로 이동 →", use_container_width=True,
                        type=("primary" if _checked else "secondary"),
                        disabled=_busy or not ss.syllabus_md):
            ss.step = 3
            st.rerun()
    render_syllabus_panel()
