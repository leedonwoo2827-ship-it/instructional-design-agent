# -*- coding: utf-8 -*-
"""교수설계 가이드 에이전트 — Streamlit + Ubion LiteLLM 프록시.

원본 _context/교수설계-에이전트.html 의 화면 구성(상단 STEP 바 · 좌측 입력 / 우측 출력
2단 레이아웃)과 브랜드 디자인을 이식했다.

호출 경로: Streamlit → openai SDK → 사내 LiteLLM 프록시(/v1/chat/completions).
URL·API 키·모델은 상단 '연결 설정'(접기/펴기)에서 입력하며 data/user_settings.json 에 저장.
"""
from __future__ import annotations

import time

import markdown as md_lib
import streamlit as st
from dotenv import load_dotenv

load_dotenv(encoding="utf-8-sig")  # 메모장 저장 .env 의 BOM 허용

from core import llm as llm_mod  # noqa: E402
from core import prompts  # noqa: E402
from core import user_settings as settings_mod  # noqa: E402
from core.pptx_export import outline_to_pptx  # noqa: E402
from core.viz import (  # noqa: E402
    ICON_DOC, ICON_INFO, ICON_SLIDE, bloom_chart_html, bloom_counts,
)

st.set_page_config(
    page_title="교수설계 가이드 에이전트",
    page_icon="교",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# 원본 HTML 의 브랜드 디자인 이식
_CSS = """
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css');
:root{--brand:#3b4ec8;--brand2:#2c3aa0;--brand-soft:#eef0fb;--accent:#0ea5a4;
  --line:#e4e6ea;--ink:#1a1d23;--ink2:#5b6472;--bg:#f4f5f7;
  --ok:#0e9f6e;--warn:#b45309;--err:#cc4117;}
html,body,.stApp,[class*="css"]{font-family:'Pretendard',-apple-system,'Malgun Gothic',sans-serif;}
.stApp{background:var(--bg);}
[data-testid="stHeader"]{background:transparent;}
.block-container{max-width:1280px;padding-top:1.1rem;padding-bottom:3rem;}

/* 헤더 카드 */
.ida-header{display:flex;align-items:center;gap:14px;background:#fff;border:1px solid var(--line);
  border-radius:14px;padding:14px 18px;margin-bottom:12px;
  box-shadow:0 1px 3px rgba(20,24,40,.07),0 4px 16px rgba(20,24,40,.05);}
.ida-logo{width:40px;height:40px;border-radius:10px;flex-shrink:0;
  background:linear-gradient(135deg,var(--brand),#7c5cd6);display:flex;align-items:center;
  justify-content:center;color:#fff;font-weight:800;font-size:18px;}
.ida-title{font-weight:800;font-size:18px;color:var(--ink);line-height:1.25;}
.ida-sub{font-size:12.5px;color:var(--ink2);}
.ida-panel-title{font-weight:700;font-size:14.5px;color:var(--ink);margin:2px 0 8px;
  letter-spacing:-0.01em;display:flex;align-items:center;}
.ida-title{letter-spacing:-0.02em;}
[data-testid="stMarkdownContainer"] h1,[data-testid="stMarkdownContainer"] h2{letter-spacing:-0.015em;}
/* STEP 바 버튼: 활성=채움, 비활성=아웃라인 */
div[data-testid="stHorizontalBlock"] .stButton>button{font-weight:700;}

/* 버튼 */
.stButton>button,.stDownloadButton>button,.stFormSubmitButton>button{
  border-radius:9px;font-weight:600;border:1px solid var(--line);transition:.15s;}
.stButton>button:hover,.stDownloadButton>button:hover{border-color:var(--brand);color:var(--brand);}
.stButton>button[kind="primary"],.stFormSubmitButton>button[kind="primary"]{
  background:var(--brand);border-color:var(--brand);color:#fff;}
.stButton>button[kind="primary"]:hover,.stFormSubmitButton>button[kind="primary"]:hover{
  background:var(--brand2);border-color:var(--brand2);color:#fff;}

/* expander = 카드 */
[data-testid="stExpander"]{border:1px solid var(--line);border-radius:12px;background:#fff;
  box-shadow:0 1px 3px rgba(20,24,40,.06);margin-bottom:12px;}
[data-testid="stExpander"] summary{font-weight:700;color:var(--ink);}
[data-testid="stExpander"] summary:hover{color:var(--brand);}

/* 입력 */
.stTextInput input,.stTextArea textarea{border-radius:8px;}
.stTextInput input:focus,.stTextArea textarea:focus{border-color:var(--brand);box-shadow:none;}

/* 우측 출력 카드 */
[data-testid="stVerticalBlockBorderWrapper"]{border-radius:12px;}

/* 산출물 마크다운 */
[data-testid="stMarkdownContainer"] h1{font-size:22px;border-bottom:2px solid var(--brand-soft);padding-bottom:8px;}
[data-testid="stMarkdownContainer"] h2{font-size:17px;color:var(--brand2);margin-top:22px;}
[data-testid="stMarkdownContainer"] h3{font-size:15px;}
[data-testid="stMarkdownContainer"] table{border-collapse:collapse;width:100%;font-size:13px;margin:12px 0;}
[data-testid="stMarkdownContainer"] th{background:var(--brand-soft);color:var(--brand2);font-weight:700;text-align:left;}
[data-testid="stMarkdownContainer"] th,[data-testid="stMarkdownContainer"] td{border:1px solid var(--line);padding:7px 10px;}
[data-testid="stMarkdownContainer"] tr:nth-child(even) td{background:#fafbfd;}
[data-testid="stMarkdownContainer"] blockquote{border-left:3px solid var(--brand);background:var(--brand-soft);
  padding:8px 14px;border-radius:0 8px 8px 0;}
[data-testid="stMarkdownContainer"] code{background:#eef0f4;border-radius:4px;padding:1px 5px;}
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


# ---------------------------------------------------------------------------
# 세션 상태
# ---------------------------------------------------------------------------
ss = st.session_state
ss.setdefault("settings", settings_mod.load())
ss.setdefault("step", 1)
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


def stream_into(placeholder, system: str, messages: list, label: str = "생성") -> str:
    provider = llm_mod.build_provider(ss.settings)
    full = ""
    placeholder.markdown("**작성 중…** 잠시만 기다려 주세요. 내용이 이 영역에 실시간으로 나타납니다. ▌")
    t0 = time.time()
    last = 0
    print(f"[{label}] 시작 · 모델={ss.settings.model}", flush=True)
    try:
        for delta in provider.stream(system, messages,
                                     max_tokens=ss.settings.max_tokens,
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


def script_user_msg(week: int, fmt: str, note: str, syllabus_md: str) -> str:
    kind = "학생용 교재(읽기 자료)" if fmt == "doc" else "PPT 슬라이드 개요"
    extra = f"\n[교수자 추가 요청] {note}\n" if note.strip() else ""
    return (
        f"아래는 확정된 강의계획서입니다. 이 계획서의 {week}주차에 대한 {kind}를 작성해 주세요. "
        f"반드시 계획서의 해당 주차 목표와 강좌 목표(G#)를 상속하세요.{extra}\n"
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
    sys_gen = prompts.SYS_SCRIPT_DOC if is_doc else prompts.SYS_SCRIPT_PPT
    md_key = "script_doc_md" if is_doc else "script_ppt_md"
    msgs_key = "script_doc_msgs" if is_doc else "script_ppt_msgs"
    wk = f"{ss.script_week}주차 " + ("교재" if is_doc else "PPT 개요")
    if kind == "check":
        cur = ss[md_key]
        msgs = [{"role": "user", "content": f"다음 산출물을 점검해 주세요.\n\n{cur}"}]
        rep = stream_into(placeholder, prompts.SYS_CHECK_SCR, msgs, label=f"{wk} 점검")
        if rep:
            ss[md_key] = cur + f"\n\n---\n\n## 정렬 점검 보고\n\n{rep}"
    else:  # gen | refine
        full = stream_into(placeholder, sys_gen, ss[msgs_key], label=wk)
        if full:
            ss[md_key] = full
            ss[msgs_key].append({"role": "assistant", "content": full})


# ---------------------------------------------------------------------------
# 헤더 (원본 HTML 디자인)
# ---------------------------------------------------------------------------
st.markdown(
    '<div class="ida-header">'
    '<div class="ida-logo">교</div>'
    '<div><div class="ida-title">교수설계 가이드 에이전트</div>'
    '<div class="ida-sub">ABCD 학습목표 · Bloom 정렬 · 백워드 설계(WHERETO) · Mayer 멀티미디어 원리 기반</div>'
    '</div></div>',
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# 연결 설정 — 접기/펴기
# ---------------------------------------------------------------------------
_key_ok = bool((ss.settings.api_key or "").strip())
_status = "연결 준비됨 ✓" if _key_ok else "API 키를 입력하세요"
with st.expander(f"연결 설정 — {_status}", expanded=not ss.had_key):
    s = ss.settings
    c = st.columns([2, 2, 1.4])
    s.base_url = c[0].text_input("LiteLLM URL", value=s.base_url, help="사내 프록시 주소")
    s.api_key = c[1].text_input("API 키", value=s.api_key, type="password",
                                help="사내 대시보드(/ui/)에서 발급한 sk- 키")
    model_ids = list(settings_mod.MODELS.keys())
    s.model = c[2].selectbox("모델", model_ids,
                             index=model_ids.index(s.model) if s.model in model_ids else 0,
                             format_func=lambda m: settings_mod.MODELS[m])
    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("연결 테스트", use_container_width=True):
        with st.spinner("확인 중…"):
            ss.ping_status = llm_mod.build_provider(s).ping()
    if b2.button("저장", use_container_width=True, type="primary"):
        settings_mod.save(s)
        ss.had_key = bool((s.api_key or "").strip())
        st.success("저장되었습니다.")
        st.rerun()
    if ss.ping_status:
        ok, msg = ss.ping_status
        (st.success if ok else st.error)(msg)
    st.caption("URL·키는 data/user_settings.json 에 저장되며 GitHub 에 올라가지 않습니다.")


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
left, right = st.columns([0.37, 0.63], gap="large")


# ---------------------------------------------------------------------------
# 좌측 — 입력 패널 (STEP 별)
# ---------------------------------------------------------------------------
with left:
    with st.container(border=True):
        if ss.step == 1:
            st.markdown(f'<div class="ida-panel-title">{ICON_INFO}강의 기본 정보 · STEP 1</div>', unsafe_allow_html=True)
            with st.form("lecture_form"):
                cc = st.columns(2)
                title = cc[0].text_input("과목명 *", value=ss.form.get("title", ""), placeholder="예: 교육공학의 이해")
                field = cc[1].text_input("학문 분야", value=ss.form.get("field", ""), placeholder="예: 교육학")
                cc = st.columns(2)
                target = cc[0].text_input("수강 대상 *", value=ss.form.get("target", ""), placeholder="예: 학부 2학년")
                credit = cc[1].text_input("학점 / 시수", value=ss.form.get("credit", ""), placeholder="예: 3학점, 주 3시간")
                cc = st.columns(2)
                weeks = cc[0].selectbox("총 주차", WEEK_CHOICES,
                                        index=WEEK_CHOICES.index(ss.form.get("weeks", 15))
                                        if ss.form.get("weeks", 15) in WEEK_CHOICES else 3)
                mode = cc[1].selectbox("강의 방식", MODE_CHOICES,
                                       index=MODE_CHOICES.index(ss.form.get("mode", "대면"))
                                       if ss.form.get("mode", "대면") in MODE_CHOICES else 0)
                topics = st.text_area("주요 내용 · 다루고 싶은 주제 *", value=ss.form.get("topics", ""),
                                      placeholder="예: 교수설계 이론, ADDIE 모형, 학습목표 설계, 매체 활용 등")
                learner = st.text_input("수강생 특성 (선수지식 · 이질성 등)", value=ss.form.get("learner", ""),
                                        placeholder="예: 전공 기초 이수, 일부 현직 교사 포함")
                policy = st.text_area("평가 선호 · 수업 철학 (선택)", value=ss.form.get("policy", ""),
                                      placeholder="예: 과정 중심 평가 40%, 토론 중심 운영, 생성형 AI 조건부 허용")
                st.caption("입력 정보는 학습자 도달점 중심(ABCD)으로 학습목표를 설계하고 목표–주차–평가를 정렬하는 데 쓰입니다.")
                submitted = st.form_submit_button("강의계획서 생성 →", type="primary", use_container_width=True)
            if submitted:
                if not title.strip() or not topics.strip():
                    st.warning("과목명과 주요 내용은 필수 입력입니다.")
                elif ensure_ready():
                    ss.form = dict(title=title, field=field, target=target, credit=credit,
                                   weeks=weeks, mode=mode, topics=topics, learner=learner, policy=policy)
                    ss.syllabus_msgs = [{"role": "user", "content": syllabus_user_msg(ss.form)}]
                    ss.step = 2
                    ss._pending = {"kind": "gen", "doc": "syllabus"}
                    st.rerun()

        elif ss.step == 2:
            st.markdown(f'<div class="ida-panel-title">{ICON_INFO}강의계획서 설계 기준 · STEP 2</div>', unsafe_allow_html=True)
            st.markdown(
                "- **측정 가능한 학습목표** — ABCD 모델, Bloom 개정분류 동사. 한 목표 한 동사.\n"
                "- **목표 분해** — 강좌 목표를 주차(모듈) 목표로 분해.\n"
                "- **정렬 매트릭스** — 주차·평가가 어느 강좌 목표를 지지하는지 추적.\n"
                "- **인지수준 분포** — 상·하위 수준 쏠림 점검.\n"
                "- **어조·근거** — 통상과 다른 운영은 근거 명시."
            )
            if st.button("정렬 · 인지수준 점검", use_container_width=True):
                if ss.syllabus_md and ensure_ready():
                    ss._pending = {"kind": "check", "doc": "syllabus"}
                    st.rerun()
                elif not ss.syllabus_md:
                    st.warning("먼저 강의계획서를 생성하세요.")
            if st.button("원고 작성으로 이동 →", type="primary", use_container_width=True,
                         disabled=not ss.syllabus_md):
                ss.step = 3
                st.rerun()

        else:  # STEP 3
            st.markdown(f'<div class="ida-panel-title">{ICON_SLIDE}산출물 생성 · STEP 3</div>', unsafe_allow_html=True)
            if not ss.syllabus_md:
                st.info("산출물은 강의계획서의 주차 목표를 상속합니다. 먼저 강의계획서를 생성하세요.")
            else:
                st.caption("선택한 주차에 대해 **학생용 교재**와 **PPT 개요**를 함께 생성합니다. (각각 수정·점검·저장 가능)")
                n_weeks = int(ss.form.get("weeks", 15))
                week_opts = list(range(1, n_weeks + 1))
                ss.script_week = st.selectbox("대상 주차", week_opts,
                                              index=week_opts.index(ss.script_week)
                                              if ss.script_week in week_opts else 0,
                                              format_func=lambda w: f"{w}주차")
                note = st.text_area("해당 차시 요청사항 (선택)", key="script_note",
                                    placeholder="예: 사례 중심으로, 조별 토론 20분 포함, 동영상 강의용 등")
                if st.button("교재 + PPT 개요 생성 →", type="primary", use_container_width=True):
                    if ensure_ready():
                        ss.script_doc_msgs = [{"role": "user",
                                               "content": script_user_msg(ss.script_week, "doc", note, ss.syllabus_md)}]
                        ss.script_ppt_msgs = [{"role": "user",
                                               "content": script_user_msg(ss.script_week, "ppt", note, ss.syllabus_md)}]
                        ss._pending = {"kind": "gen_both", "doc": "script"}
                        st.rerun()


# ---------------------------------------------------------------------------
# 우측 — 출력 패널
# ---------------------------------------------------------------------------
with right:
    if ss.step != 3:
        # ===== 강의계획서 (STEP 1·2) =====
        with st.container(border=True):
            hc = st.columns([3, 1.1, 1.5])
            hc[0].markdown(f'<div class="ida-panel-title">{ICON_DOC}강의계획서</div>', unsafe_allow_html=True)
            if ss.syllabus_md:
                hc[1].download_button("MD", ss.syllabus_md, file_name=out_name("syllabus") + ".md",
                                      mime="text/markdown", use_container_width=True)
                hc[2].download_button("DOC 저장", md_to_doc_bytes(ss.syllabus_md),
                                      file_name=out_name("syllabus") + ".doc",
                                      mime="application/msword", use_container_width=True)
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
                st.rerun()
            elif ss.syllabus_md:
                out_ph.markdown(ss.syllabus_md)
            else:
                out_ph.info("좌측에서 강의 정보를 입력하고 강의계획서를 생성하세요.")

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
    else:
        # ===== 교재 + PPT 개요 (STEP 3) =====
        def _section(doc_key, md_key, icon, name, is_ppt):
            cur = ss[md_key]
            cols = [3, 1, 1, 1.2] if is_ppt else [3, 1.1, 1.5]
            hc = st.columns(cols)
            hc[0].markdown(f'<div class="ida-panel-title">{icon}{name}</div>', unsafe_allow_html=True)
            fn = out_name("PPT개요" if is_ppt else "교재")
            if cur:
                hc[1].download_button("MD", cur, file_name=fn + ".md", mime="text/markdown",
                                      key=f"md_{doc_key}", use_container_width=True)
                hc[2].download_button("DOC", md_to_doc_bytes(cur), file_name=fn + ".doc",
                                      mime="application/msword", key=f"doc_{doc_key}", use_container_width=True)
                if is_ppt:
                    pptx = outline_to_pptx(cur, deck_title=fn)
                    if pptx:
                        hc[3].download_button(
                            "PPTX", pptx, file_name=fn + ".pptx",
                            mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                            key=f"pptx_{doc_key}", use_container_width=True)
            return st.empty(), st.container()

        with st.container(border=True):
            doc_ph, doc_ctrl = _section("script_doc", "script_doc_md", ICON_DOC, "교재", False)
            st.divider()
            ppt_ph, ppt_ctrl = _section("script_ppt", "script_ppt_md", ICON_SLIDE, "PPT 개요", True)

            pdoc = pending.get("doc") if pending else None
            if pending and pending["kind"] == "gen_both":
                with st.spinner(f"{ss.script_week}주차 교재 작성 중…"):
                    run_pending({"kind": "gen", "doc": "script_doc"}, doc_ph)
                with st.spinner(f"{ss.script_week}주차 PPT 개요 작성 중…"):
                    run_pending({"kind": "gen", "doc": "script_ppt"}, ppt_ph)
                st.rerun()
            elif pending and pdoc in ("script_doc", "script_ppt"):
                tph = doc_ph if pdoc == "script_doc" else ppt_ph
                _m = {"refine": "수정 반영 중…", "check": "정렬 점검 중…"}.get(pending["kind"], "작성 중…")
                with st.spinner(_m):
                    run_pending(pending, tph)
                st.rerun()
            else:
                for ph, ctrl, md_key, msgs_key, doc_key, empty_hint in [
                    (doc_ph, doc_ctrl, "script_doc_md", "script_doc_msgs", "script_doc",
                     "좌측에서 '교재 + PPT 개요 생성'을 누르면 교재가 여기에 표시됩니다."),
                    (ppt_ph, ppt_ctrl, "script_ppt_md", "script_ppt_msgs", "script_ppt",
                     "PPT 개요가 여기에 표시됩니다. (PPTX 다운로드 지원)"),
                ]:
                    if ss[md_key]:
                        ph.markdown(ss[md_key])
                        with ctrl:
                            rc = st.columns([3, 1, 1.3])
                            req = rc[0].text_input("수정", key=f"refine_{doc_key}", label_visibility="collapsed",
                                                   placeholder="수정 요청")
                            if rc[1].button("수정", key=f"refbtn_{doc_key}", use_container_width=True):
                                if req.strip() and ensure_ready():
                                    ss[msgs_key].append({"role": "user", "content": REFINE_TMPL.format(req=req)})
                                    ss._pending = {"kind": "refine", "doc": doc_key}
                                    st.rerun()
                            if rc[2].button("정렬 점검", key=f"chk_{doc_key}", use_container_width=True):
                                if ensure_ready():
                                    ss._pending = {"kind": "check", "doc": doc_key}
                                    st.rerun()
                    else:
                        ph.info(empty_hint)
