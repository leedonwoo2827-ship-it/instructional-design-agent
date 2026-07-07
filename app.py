# -*- coding: utf-8 -*-
"""교수설계 가이드 에이전트 — Streamlit + Ubion LiteLLM 프록시.

ABCD 학습목표 · Bloom 정렬 · 백워드 설계(WHERETO) · Mayer 멀티미디어 원리로
강의계획서 → 차시 원고/PPT 개요를 생성·점검한다.

호출 경로: Streamlit → openai SDK → 사내 LiteLLM 프록시(/v1/chat/completions).
URL·API 키·모델은 사이드바(설정)에서 입력하며 data/user_settings.json 에 저장된다.
"""
from __future__ import annotations

import markdown as md_lib
import streamlit as st
from dotenv import load_dotenv

# utf-8-sig: 메모장으로 저장한 .env 의 BOM 허용
load_dotenv(encoding="utf-8-sig")

from core import llm as llm_mod  # noqa: E402
from core import prompts  # noqa: E402
from core import user_settings as settings_mod  # noqa: E402

st.set_page_config(
    page_title="교수설계 가이드 에이전트",
    page_icon="🧑‍🏫",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# 원본 _context/교수설계-에이전트.html 의 브랜드 디자인을 이식
_CSS = """
<style>
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css');
:root{--brand:#3b4ec8;--brand2:#2c3aa0;--brand-soft:#eef0fb;--accent:#0ea5a4;
  --line:#e4e6ea;--ink:#1a1d23;--ink2:#5b6472;--bg:#f4f5f7;}
html,body,.stApp,[class*="css"]{font-family:'Pretendard',-apple-system,'Malgun Gothic',sans-serif;}
.stApp{background:var(--bg);}
[data-testid="stHeader"]{background:transparent;}
.block-container{max-width:1120px;padding-top:1.1rem;padding-bottom:3rem;}

/* 헤더 카드 */
.ida-header{display:flex;align-items:center;gap:14px;background:#fff;border:1px solid var(--line);
  border-radius:14px;padding:14px 18px;margin-bottom:14px;
  box-shadow:0 1px 3px rgba(20,24,40,.07),0 4px 16px rgba(20,24,40,.05);}
.ida-logo{width:40px;height:40px;border-radius:10px;flex-shrink:0;
  background:linear-gradient(135deg,var(--brand),#7c5cd6);display:flex;align-items:center;
  justify-content:center;color:#fff;font-weight:800;font-size:18px;}
.ida-title{font-weight:800;font-size:18px;color:var(--ink);line-height:1.25;}
.ida-sub{font-size:12.5px;color:var(--ink2);}

/* 버튼 */
.stButton>button,.stDownloadButton>button,.stFormSubmitButton>button{
  border-radius:9px;font-weight:600;border:1px solid var(--line);transition:.15s;}
.stButton>button:hover,.stDownloadButton>button:hover{border-color:var(--brand);color:var(--brand);}
.stButton>button[kind="primary"],.stFormSubmitButton>button[kind="primary"]{
  background:var(--brand);border-color:var(--brand);color:#fff;}
.stButton>button[kind="primary"]:hover,.stFormSubmitButton>button[kind="primary"]:hover{
  background:var(--brand2);border-color:var(--brand2);color:#fff;}

/* 탭 = 스텝 알약 */
[data-baseweb="tab-list"]{gap:8px;border-bottom:none;}
[data-baseweb="tab"]{background:#fff;border:1px solid var(--line)!important;border-radius:10px;
  padding:9px 16px;font-weight:700;}
[data-baseweb="tab"][aria-selected="true"]{border-color:var(--brand)!important;color:var(--brand);
  box-shadow:0 1px 3px rgba(20,24,40,.08);}
[data-baseweb="tab-highlight"],[data-baseweb="tab-border"]{display:none!important;}

/* expander = 카드 */
[data-testid="stExpander"]{border:1px solid var(--line);border-radius:12px;background:#fff;
  box-shadow:0 1px 3px rgba(20,24,40,.06);margin-bottom:14px;}
[data-testid="stExpander"] summary{font-weight:700;color:var(--ink);}
[data-testid="stExpander"] summary:hover{color:var(--brand);}

/* 입력 */
.stTextInput input,.stTextArea textarea{border-radius:8px;}
.stTextInput input:focus,.stTextArea textarea:focus{border-color:var(--brand);box-shadow:none;}

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

REFINE_TMPL = (
    "다음 요청대로 수정하여, 수정된 문서 전체를 다시 출력해 주세요. "
    "수정 시에도 학습목표 정렬 원칙(측정 가능 동사, 목표–평가–활동 인지수준 일치)을 유지하세요.\n\n요청: {req}"
)


# ---------------------------------------------------------------------------
# 세션 상태
# ---------------------------------------------------------------------------
ss = st.session_state
ss.setdefault("settings", settings_mod.load())
ss.setdefault("syllabus_md", "")
ss.setdefault("syllabus_msgs", [])
ss.setdefault("script_md", "")
ss.setdefault("script_msgs", [])
ss.setdefault("script_week", 1)
ss.setdefault("fmt", "doc")
ss.setdefault("ping_status", None)
ss.setdefault("form", {})
# 접기/펴기 초기 상태: 저장된 키가 이미 있으면 접힌 채로 시작
ss.setdefault("had_key", bool((ss.settings.api_key or "").strip()))


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------
def ensure_ready() -> bool:
    if not (ss.settings.api_key or "").strip():
        st.warning("사이드바 ⚙️ 설정에서 API 키를 입력하고 **저장**하세요.")
        return False
    return True


def stream_into(placeholder, system: str, messages: list) -> str:
    """LiteLLM 스트림을 placeholder 에 실시간 렌더. 최종 전체 텍스트 반환."""
    provider = llm_mod.build_provider(ss.settings)
    full = ""
    try:
        for delta in provider.stream(
            system, messages, max_tokens=ss.settings.max_tokens, temperature=ss.settings.temperature
        ):
            full += delta
            placeholder.markdown(full + " ▌")
        placeholder.markdown(full)
    except Exception as e:  # noqa: BLE001
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
    kind = "문서형 차시 원고(강의안+대본)" if fmt == "doc" else "PPT 슬라이드 개요"
    extra = f"\n[교수자 추가 요청] {note}\n" if note.strip() else ""
    return (
        f"아래는 확정된 강의계획서입니다. 이 계획서의 {week}주차에 대한 {kind}를 작성해 주세요. "
        f"반드시 계획서의 해당 주차 목표와 강좌 목표(G#)를 상속하세요.{extra}\n"
        f"=== 강의계획서 ===\n{syllabus_md}"
    )


def out_name(kind: str) -> str:
    title = (ss.form.get("title") or "강의").strip() or "강의"
    if kind == "syllabus":
        return f"{title}_강의계획서"
    label = "원고" if ss.fmt == "doc" else "PPT개요"
    return f"{title}_{ss.script_week}주차_{label}"


def download_row(md_text: str, kind: str):
    c1, c2 = st.columns(2)
    c1.download_button("⬇️ .md 저장", md_text, file_name=out_name(kind) + ".md",
                       mime="text/markdown", use_container_width=True)
    c2.download_button("⬇️ .doc 저장 (한글/워드)", md_to_doc_bytes(md_text),
                       file_name=out_name(kind) + ".doc", mime="application/msword",
                       use_container_width=True)


# ---------------------------------------------------------------------------
# 헤더 (원본 HTML 디자인 이식)
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
# 연결 설정 — 접기/펴기 (키가 없으면 펼친 채로 시작)
# ---------------------------------------------------------------------------
_key_ok = bool((ss.settings.api_key or "").strip())
_status = "✓ 연결 준비됨" if _key_ok else "API 키를 입력하세요"
with st.expander(f"⚙️ 연결 설정 — {_status}", expanded=not ss.had_key):
    s = ss.settings
    c = st.columns([2, 2, 1.4])
    s.base_url = c[0].text_input("LiteLLM URL", value=s.base_url, help="사내 프록시 주소")
    s.api_key = c[1].text_input("API 키", value=s.api_key, type="password",
                                help="사내 대시보드(/ui/)에서 발급한 sk- 키")
    model_ids = list(settings_mod.MODELS.keys())
    s.model = c[2].selectbox(
        "모델", model_ids,
        index=model_ids.index(s.model) if s.model in model_ids else 0,
        format_func=lambda m: settings_mod.MODELS[m],
    )
    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("연결 테스트", use_container_width=True):
        with st.spinner("확인 중…"):
            ss.ping_status = llm_mod.build_provider(s).ping()
    if b2.button("💾 저장", use_container_width=True, type="primary"):
        settings_mod.save(s)
        ss.had_key = bool((s.api_key or "").strip())
        st.success("저장되었습니다.")
        st.rerun()
    if ss.ping_status:
        ok, msg = ss.ping_status
        (st.success if ok else st.error)(msg)
    st.caption("URL·키는 `data/user_settings.json` 에 저장되며 GitHub 에 올라가지 않습니다.")

tab1, tab2, tab3 = st.tabs(["1️⃣ 강의 정보 입력", "2️⃣ 강의계획서", "3️⃣ 원고"])


# ---------------------------------------------------------------------------
# 탭 1 — 강의 정보 입력 → 강의계획서 생성
# ---------------------------------------------------------------------------
with tab1:
    with st.form("lecture_form"):
        c = st.columns(2)
        title = c[0].text_input("과목명 *", value=ss.form.get("title", ""), placeholder="예: 교육공학의 이해")
        field = c[1].text_input("학문 분야", value=ss.form.get("field", ""), placeholder="예: 교육학")
        c = st.columns(2)
        target = c[0].text_input("수강 대상 *", value=ss.form.get("target", ""), placeholder="예: 학부 2학년 / 대학원")
        credit = c[1].text_input("학점 / 시수", value=ss.form.get("credit", ""), placeholder="예: 3학점, 주 3시간")
        c = st.columns(2)
        weeks = c[0].selectbox("총 주차", WEEK_CHOICES, index=WEEK_CHOICES.index(ss.form.get("weeks", 15))
                               if ss.form.get("weeks", 15) in WEEK_CHOICES else 3)
        mode = c[1].selectbox("강의 방식", MODE_CHOICES,
                              index=MODE_CHOICES.index(ss.form.get("mode", "대면"))
                              if ss.form.get("mode", "대면") in MODE_CHOICES else 0)
        topics = st.text_area("주요 내용 · 다루고 싶은 주제 *", value=ss.form.get("topics", ""),
                              placeholder="예: 교수설계 이론, ADDIE 모형, 학습목표 설계, 매체 활용, 에듀테크 동향 등")
        learner = st.text_input("수강생 특성 (선수지식 · 이질성 등)", value=ss.form.get("learner", ""),
                                placeholder="예: 전공 기초 이수, 일부 현직 교사 포함")
        policy = st.text_area("평가 선호 · 수업 철학 (선택)", value=ss.form.get("policy", ""),
                              placeholder="예: 과정 중심 평가 40%, 토론 중심 운영, 생성형 AI 조건부 허용")
        st.info("💡 입력 정보는 **학습자 도달점 중심(ABCD)** 으로 학습목표를 설계하고 **목표–주차–평가를 정렬**하는 데 쓰입니다.")
        submitted = st.form_submit_button("강의계획서 생성 →", type="primary", use_container_width=True)

    if submitted:
        if not title.strip() or not topics.strip():
            st.warning("과목명과 주요 내용은 필수 입력입니다.")
        elif ensure_ready():
            ss.form = dict(title=title, field=field, target=target, credit=credit,
                           weeks=weeks, mode=mode, topics=topics, learner=learner, policy=policy)
            ss.syllabus_msgs = [{"role": "user", "content": syllabus_user_msg(ss.form)}]
            st.markdown("##### 강의계획서 생성 중… (목표 설계 → 주차 분해 → 정렬 매트릭스)")
            ph = st.empty()
            full = stream_into(ph, prompts.SYS_SYLLABUS, ss.syllabus_msgs)
            if full:
                ss.syllabus_md = full
                ss.syllabus_msgs.append({"role": "assistant", "content": full})
                st.success("완료! **‘2️⃣ 강의계획서’** 탭에서 확인·수정·점검할 수 있습니다.")


# ---------------------------------------------------------------------------
# 탭 2 — 강의계획서 표시 / 수정 / 점검
# ---------------------------------------------------------------------------
with tab2:
    if not ss.syllabus_md:
        st.info("먼저 **‘1️⃣ 강의 정보 입력’** 탭에서 강의계획서를 생성하세요.")
    else:
        download_row(ss.syllabus_md, "syllabus")
        with st.expander("📋 원문(마크다운) 복사"):
            st.code(ss.syllabus_md, language="markdown")
        refine = st.text_input("✏️ 수정 요청", key="syl_refine",
                               placeholder="예: 7주차 목표를 '분석' 수준으로 높여줘 / 평가 비중에서 성찰과제를 늘려줘")
        a1, a2 = st.columns(2)
        do_refine = a1.button("수정 반영", key="syl_refine_btn", use_container_width=True)
        do_check = a2.button("🔍 정렬 · 인지수준 점검", key="syl_check_btn", use_container_width=True)
        st.divider()
        ph = st.empty()

        if do_refine and refine.strip() and ensure_ready():
            ss.syllabus_msgs.append({"role": "user", "content": REFINE_TMPL.format(req=refine)})
            full = stream_into(ph, prompts.SYS_SYLLABUS, ss.syllabus_msgs)
            if full:
                ss.syllabus_md = full
                ss.syllabus_msgs.append({"role": "assistant", "content": full})
            st.rerun()
        elif do_check and ensure_ready():
            msgs = [{"role": "user", "content": f"다음 산출물을 점검해 주세요.\n\n{ss.syllabus_md}"}]
            report = stream_into(ph, prompts.SYS_CHECK_SYL, msgs)
            if report:
                ss.syllabus_md += f"\n\n---\n\n## 🔍 정렬 점검 보고\n\n{report}"
            st.rerun()
        else:
            ph.markdown(ss.syllabus_md)


# ---------------------------------------------------------------------------
# 탭 3 — 원고 생성 / 수정 / 점검
# ---------------------------------------------------------------------------
with tab3:
    if not ss.syllabus_md:
        st.info("원고는 강의계획서의 주차 목표를 **상속**하여 작성됩니다. 먼저 강의계획서를 생성하세요(정렬 원칙).")
    else:
        fmt_label = st.radio("원고 형태", ["📄 문서형 원고", "🖥 PPT 개요식"],
                             index=0 if ss.fmt == "doc" else 1, horizontal=True)
        ss.fmt = "doc" if fmt_label.startswith("📄") else "ppt"
        if ss.fmt == "doc":
            st.caption("강의 전달용 대본 — 도입(Hook)·활동 계열·형성평가 지점 포함, WHERETO 사후 점검.")
        else:
            st.caption("Mayer 원리 기반 슬라이드 아웃라인 — 한 슬라이드 한 메시지·시각자료·발표자 노트.")

        n_weeks = int(ss.form.get("weeks", 15))
        week_opts = list(range(1, n_weeks + 1))
        ss.script_week = st.selectbox("대상 주차", week_opts,
                                      index=week_opts.index(ss.script_week) if ss.script_week in week_opts else 0,
                                      format_func=lambda w: f"{w}주차")
        note = st.text_area("해당 차시 요청사항 (선택)", key="script_note",
                            placeholder="예: 사례 중심으로, 조별 토론 20분 포함, 동영상 강의용 등")
        gen = st.button("원고 생성 →", type="primary", use_container_width=True)

        st.divider()

        if ss.script_md:
            download_row(ss.script_md, "script")
            with st.expander("📋 원문(마크다운) 복사"):
                st.code(ss.script_md, language="markdown")
            sref = st.text_input("✏️ 수정 요청", key="scr_refine",
                                 placeholder="예: 토론 시간을 늘리고 형성평가를 하나 더 넣어줘")
            b1, b2 = st.columns(2)
            do_sref = b1.button("수정 반영", key="scr_refine_btn", use_container_width=True)
            do_scheck = b2.button("🔍 목표 정렬 · WHERETO 점검", key="scr_check_btn", use_container_width=True)
        else:
            do_sref = do_scheck = False
            sref = ""

        ph = st.empty()
        sys_script = prompts.SYS_SCRIPT_DOC if ss.fmt == "doc" else prompts.SYS_SCRIPT_PPT

        if gen and ensure_ready():
            user = script_user_msg(ss.script_week, ss.fmt, note, ss.syllabus_md)
            ss.script_msgs = [{"role": "user", "content": user}]
            full = stream_into(ph, sys_script, ss.script_msgs)
            if full:
                ss.script_md = full
                ss.script_msgs.append({"role": "assistant", "content": full})
            st.rerun()
        elif do_sref and sref.strip() and ensure_ready():
            ss.script_msgs.append({"role": "user", "content": REFINE_TMPL.format(req=sref)})
            full = stream_into(ph, sys_script, ss.script_msgs)
            if full:
                ss.script_md = full
                ss.script_msgs.append({"role": "assistant", "content": full})
            st.rerun()
        elif do_scheck and ensure_ready():
            msgs = [{"role": "user", "content": f"다음 산출물을 점검해 주세요.\n\n{ss.script_md}"}]
            report = stream_into(ph, prompts.SYS_CHECK_SCR, msgs)
            if report:
                ss.script_md += f"\n\n---\n\n## 🔍 정렬 점검 보고\n\n{report}"
            st.rerun()
        elif ss.script_md:
            ph.markdown(ss.script_md)
