# -*- coding: utf-8 -*-
"""디자인 슬라이드 빌더 — 슬라이드플랜(JSON) → 디자인된 .pptx 바이트.

애프터 덱(_guideline/after-...이미지·레이아웃정리.pptx)에서 실측한 네이비+앰버 디자인
시스템을 코드로 재현한다. 회사 템플릿을 베이스로 열어(테마·마스터·로고 상속) 예시
슬라이드를 비우고, 모든 슬라이드를 Blank 레이아웃에 도형으로 직접 배치한다.

타입: cover / section / photo / process / cards / compare / table / bullets
python-pptx 미설치 시 build_deck 은 None 을 돌려준다(그레이스풀).
"""
from __future__ import annotations

import io
import json
import os
import re
from typing import Callable, Dict, List, Optional

# ── 디자인 토큰(애프터 실측) ─────────────────────────────────────────────
FONT = "맑은 고딕"
SLIDE_W, SLIDE_H = 13.33, 7.5
MARGIN = 0.6
CONTENT_W = 12.13  # 전폭 콘텐츠

_HEX = {
    "navy": (0x1E, 0x27, 0x61),
    "navy_dk": (0x1B, 0x22, 0x52),
    "navy_dk2": (0x22, 0x2B, 0x63),
    "amber": (0xF2, 0xA9, 0x00),
    "chip": (0xEE, 0xF2, 0xFB),
    "teal": (0x2E, 0x7D, 0x8A),
    "white": (0xFF, 0xFF, 0xFF),
    "grey": (0x44, 0x49, 0x57),
    "sub": (0xCA, 0xDC, 0xFC),
    # 도형 구분용 — 서로 뚜렷이 구별되며 흰 글씨가 읽히는 중간톤
    "blue": (0x2F, 0x6F, 0xD6),
    "green": (0x2F, 0x7D, 0x4F),
    "plum": (0x6B, 0x4E, 0x8E),
}

# 프로세스 노드 / 카드 뱃지 색 로테이션 — 인접 노드가 확실히 구분되도록
_NODE_ROT = ("navy", "teal", "blue", "plum", "green")
_BADGE_ROT = ("navy", "teal", "blue", "plum")


def _rgb(name):
    from pptx.dml.color import RGBColor
    return RGBColor(*_HEX[name])


# ── 저수준 헬퍼 ───────────────────────────────────────────────────────────
def _remove_all_slides(prs) -> None:
    from pptx.oxml.ns import qn
    part = prs.part
    id_lst = prs.slides._sldIdLst
    for sid in list(id_lst):
        rid = sid.get(qn("r:id"))
        if rid and rid in part.rels:
            try:
                part.drop_rel(rid)
            except Exception:  # noqa: BLE001
                pass
        id_lst.remove(sid)


def _blank_layout(prs):
    for lay in prs.slide_layouts:
        nm = (lay.name or "").lower()
        if "blank" in nm or "빈" in (lay.name or ""):
            return lay
    return min(prs.slide_layouts, key=lambda l: len(l.placeholders))


def _strip_placeholders(slide) -> None:
    """레이아웃에서 상속된 빈 플레이스홀더 제거(마스터 로고·장식은 유지)."""
    for ph in list(slide.placeholders):
        try:
            ph._element.getparent().remove(ph._element)
        except Exception:  # noqa: BLE001
            pass


def _no_deco(shape):
    """도형 기본 그림자·테두리 제거."""
    try:
        shape.line.fill.background()
    except Exception:  # noqa: BLE001
        pass
    try:
        shape.shadow.inherit = False
    except Exception:  # noqa: BLE001
        pass


def _rrect(slide, x, y, w, h, fill=None, radius=None, line=None, line_w=None):
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches, Pt
    from pptx.oxml.ns import qn
    shp = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE,
                                 Inches(x), Inches(y), Inches(w), Inches(h))
    _no_deco(shp)
    if fill is not None:
        shp.fill.solid(); shp.fill.fore_color.rgb = _rgb(fill)
    else:
        shp.fill.background()
    if line is not None:
        shp.line.color.rgb = _rgb(line)
        shp.line.width = Pt(line_w or 1.0)
    if radius is not None:
        geom = shp._element.spPr.find(qn("a:prstGeom"))
        if geom is not None:
            av = geom.find(qn("a:avLst"))
            if av is None:
                av = geom.makeelement(qn("a:avLst"), {}); geom.append(av)
            for gd in list(av):
                av.remove(gd)
            gd = av.makeelement(qn("a:gd"), {"name": "adj", "fmla": f"val {int(radius*100000)}"})
            av.append(gd)
    return shp


def _oval(slide, x, y, d, fill):
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches
    shp = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y), Inches(d), Inches(d))
    _no_deco(shp)
    shp.fill.solid(); shp.fill.fore_color.rgb = _rgb(fill)
    return shp


def _diamond(slide, x, y, w, h, fill="amber"):
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches
    shp = slide.shapes.add_shape(MSO_SHAPE.DIAMOND, Inches(x), Inches(y), Inches(w), Inches(h))
    _no_deco(shp)
    shp.fill.solid(); shp.fill.fore_color.rgb = _rgb(fill)
    return shp


def _arrow(slide, x, y, w, h, fill="amber"):
    """단계·순서 연결자(다이아몬드 대신 화살표) — 첨삭 반영."""
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Inches
    shp = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(x), Inches(y), Inches(w), Inches(h))
    _no_deco(shp)
    shp.fill.solid(); shp.fill.fore_color.rgb = _rgb(fill)
    return shp


def _text(slide, x, y, w, h, anchor="t"):
    from pptx.util import Inches
    from pptx.enum.text import MSO_ANCHOR
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(0.05)
    tf.margin_top = tf.margin_bottom = Inches(0.02)
    tf.vertical_anchor = {"t": MSO_ANCHOR.TOP, "m": MSO_ANCHOR.MIDDLE,
                          "b": MSO_ANCHOR.BOTTOM}.get(anchor, MSO_ANCHOR.TOP)
    return tf


def _run(p, text, size, color, bold=False, font=FONT):
    from pptx.util import Pt
    r = p.add_run(); r.text = text
    r.font.size = Pt(size); r.font.bold = bold; r.font.name = font
    r.font.color.rgb = _rgb(color)
    return r


def _para(tf, first, align=None, space_after=4, line=1.05):
    from pptx.util import Pt
    from pptx.enum.text import PP_ALIGN
    p = tf.paragraphs[0] if first else tf.add_paragraph()
    if align:
        p.alignment = {"c": PP_ALIGN.CENTER, "l": PP_ALIGN.LEFT, "r": PP_ALIGN.RIGHT}[align]
    p.space_after = Pt(space_after); p.space_before = Pt(0)
    try:
        p.line_spacing = line
    except Exception:  # noqa: BLE001
        pass
    return p


# 제목·칩 세로 위치(v2 반영: 제목을 위에서 살짝 내리고 로고 피해 폭 축소)
TITLE_Y, TITLE_H, TITLE_W, TITLE_SZ = 0.7, 1.0, 10.6, 27
CHIP_Y = 1.82


# ── 구성 요소 ─────────────────────────────────────────────────────────────
def add_title(slide, text):
    tf = _text(slide, MARGIN, TITLE_Y, TITLE_W, TITLE_H, anchor="m")
    _run(_para(tf, True), text or "", TITLE_SZ, "navy", bold=True)


def add_chip(slide, text, x=MARGIN, y=CHIP_Y, w=CONTENT_W, emphasis=False):
    """요약칩: 배경 라운드 + 앰버 점 + 굵은 한 문장. 반환=칩 하단 y.

    emphasis=True(문제/질문/목표/퀴즈)면 네이비 배경+흰 글씨로 강조.
    """
    if not text:
        return y
    h = 0.9
    _rrect(slide, x, y, w, h, fill=("navy" if emphasis else "chip"), radius=0.5)
    _oval(slide, x + 0.18, y + 0.36, 0.18, "amber")
    tf = _text(slide, x + 0.5, y, w - 0.7, h, anchor="m")
    _run(_para(tf, True, line=1.1), text, 15, ("white" if emphasis else "navy"), bold=True)
    return y + h


def _hang(p, marL_in=0.23):
    """문단에 내어쓰기(hanging indent): ▸는 왼쪽으로 튀고 줄바꿈 글자는 텍스트 아래 정렬."""
    emu = str(int(marL_in * 914400))
    pPr = p._p.get_or_add_pPr()
    pPr.set("marL", emu)
    pPr.set("indent", "-" + emu)


def add_bullets(slide, bullets, x, y, w, h, size=15):
    if not bullets:
        return
    tf = _text(slide, x, y, w, h, anchor="t")
    for i, b in enumerate(bullets):
        p = _para(tf, i == 0, space_after=8, line=1.15)
        _hang(p)
        _run(p, "▸ ", size, "amber", bold=True)
        _run(p, str(b), size, "grey", bold=False)


def add_photo(slide, data, x=8.25, y=1.7, w=4.5, h=4.31):
    from pptx.util import Inches
    from pptx.oxml.ns import qn
    try:
        pic = slide.shapes.add_picture(io.BytesIO(data), Inches(x), Inches(y),
                                       Inches(w), Inches(h))
    except Exception:  # noqa: BLE001
        return None
    # 종횡비 맞춰 센터 크롭(가능하면)
    try:
        from PIL import Image
        iw, ih = Image.open(io.BytesIO(data)).size
        tgt, src = w / h, iw / ih
        if src > tgt:
            c = (1 - tgt / src) / 2; pic.crop_left = c; pic.crop_right = c
        elif src < tgt:
            c = (1 - src / tgt) / 2; pic.crop_top = c; pic.crop_bottom = c
    except Exception:  # noqa: BLE001
        pass
    geom = pic._element.spPr.find(qn("a:prstGeom"))
    if geom is not None:
        geom.set("prst", "roundRect")
    return pic


def add_footer_key(slide, text, y=5.75):
    if not text:
        return
    h = 0.62
    _rrect(slide, MARGIN, y, CONTENT_W, h, fill="chip", radius=0.3)
    tf = _text(slide, MARGIN + 0.3, y, CONTENT_W - 0.5, h, anchor="m")
    p = _para(tf, True)
    _run(p, "핵심  ", 14, "amber", bold=True)
    _run(p, text, 14, "navy", bold=True)


def add_logo(slide, logo_path):
    from pptx.util import Inches
    if not logo_path or not os.path.isfile(logo_path):
        return
    try:
        slide.shapes.add_picture(logo_path, Inches(SLIDE_W - 1.85), Inches(0.28),
                                 height=Inches(0.42))
    except Exception:  # noqa: BLE001
        pass


# ── 타입별 렌더 ───────────────────────────────────────────────────────────
def render_cover(slide, s):
    _oval(slide, 9.6, -1.6, 5.6, "navy_dk")
    _oval(slide, 10.8, 3.6, 3.4, "navy_dk2")
    _rrect(slide, MARGIN, 2.15, 0.9, 0.9, fill="amber", radius=0.3)
    tf = _text(slide, MARGIN, 3.25, 10.8, 1.7, anchor="m")
    _run(_para(tf, True), s.get("title", "강의 슬라이드"), 40, "navy", bold=True)
    sub = s.get("chip") or s.get("subtitle") or ""
    if sub:
        tf2 = _text(slide, MARGIN, 4.75, 11.0, 0.7)
        _run(_para(tf2, True), sub, 18, "navy", bold=False)


def render_section(slide, s):
    _rrect(slide, MARGIN, 3.15, 0.16, 1.2, fill="amber", radius=0.5)
    tf = _text(slide, MARGIN + 0.4, 3.0, CONTENT_W - 0.4, 1.5, anchor="m")
    _run(_para(tf, True), s.get("title", ""), 34, "navy", bold=True)
    if s.get("chip"):
        tf2 = _text(slide, MARGIN + 0.4, 4.5, CONTENT_W - 0.4, 0.8)
        _run(_para(tf2, True), s["chip"], 16, "grey", bold=False)


# 사진 배치 프리셋(v2 실측): 슬라이드마다 순환해 크기·위치를 다양화.
# img=(L,T,W,H), tx/tw=텍스트(칩·본문) 열, below=이미지가 하단(본문은 위 전폭).
# 세로형(2.83폭)은 가로 사진을 잘라먹어 제거 — 정사각/가로형/하단와이드만 사용(첨삭 반영).
PHOTO_PRESETS = [
    {"img": (8.15, 2.5, 4.15, 4.15), "tx": 0.6, "tw": 7.3},                 # 우측 정사각
    {"img": (0.5, 2.5, 4.15, 4.15), "tx": 4.95, "tw": 7.85},               # 좌측 정사각(텍스트 우)
    {"img": (8.0, 2.75, 4.8, 3.2), "tx": 0.6, "tw": 7.1},                  # 우측 가로형
    {"img": (0.5, 2.75, 4.8, 3.2), "tx": 5.5, "tw": 7.3},                  # 좌측 가로형(텍스트 우)
    {"img": (9.08, 2.7, 3.75, 3.75), "tx": 0.6, "tw": 8.2},                # 우측 중간 정사각
    {"img": (2.7, 4.75, 7.9, 2.35), "tx": 0.6, "tw": 12.1, "below": True}, # 하단 와이드(텍스트 위 전폭)
]


def render_photo(slide, s, img):
    add_title(slide, s.get("title", ""))
    emph = bool(s.get("emphasis"))
    if not img:
        by = add_chip(slide, s.get("chip"), emphasis=emph)
        add_bullets(slide, s.get("bullets", []), MARGIN + 0.02, by + 0.15,
                    CONTENT_W, 6.95 - (by + 0.15))
        return
    p = PHOTO_PRESETS[s.get("_idx", 0) % len(PHOTO_PRESETS)]
    add_photo(slide, img, *p["img"])
    tx, tw = p["tx"], p["tw"]
    by = add_chip(slide, s.get("chip"), x=tx, w=tw, emphasis=emph)
    top = by + 0.15
    bottom = (p["img"][1] - 0.15) if p.get("below") else 6.95
    add_bullets(slide, s.get("bullets", []), tx + 0.02, top, tw - 0.04, max(1.0, bottom - top))


def render_bullets(slide, s):
    add_title(slide, s.get("title", ""))
    by = add_chip(slide, s.get("chip"), emphasis=bool(s.get("emphasis")))
    add_bullets(slide, s.get("bullets", []), MARGIN + 0.02, max(by + 0.2, 2.72),
                CONTENT_W, 6.9 - max(by + 0.2, 2.72))


def render_process(slide, s):
    add_title(slide, s.get("title", ""))
    add_chip(slide, s.get("chip"))
    items = s.get("items") or [{"label": b} for b in s.get("bullets", [])]
    items = items[:5] or [{"label": "항목"}]
    n = len(items)
    gap = 0.34
    node_h = 1.5
    node_w = (CONTENT_W - gap * (n - 1)) / n
    y = 3.15
    for i, it in enumerate(items):
        x = MARGIN + i * (node_w + gap)
        color = _NODE_ROT[i % len(_NODE_ROT)]
        _rrect(slide, x, y, node_w, node_h, fill=color, radius=0.12)
        tf = _text(slide, x + 0.1, y, node_w - 0.2, node_h, anchor="m")
        p = _para(tf, True, align="c")
        _run(p, it.get("label", ""), 15, "white", bold=True)
        desc = it.get("desc")
        if desc:
            tf2 = _text(slide, x + 0.05, y + node_h + 0.05, node_w - 0.1, 1.0, anchor="t")
            _run(_para(tf2, True, align="c", line=1.1), desc, 11, "grey", bold=False)
        if i < n - 1:
            _arrow(slide, x + node_w + 0.03, y + node_h / 2 - 0.14, gap - 0.06, 0.28, "amber")
    add_footer_key(slide, s.get("key") or s.get("footer"))


def render_cards(slide, s):
    add_title(slide, s.get("title", ""))
    add_chip(slide, s.get("chip"))
    items = s.get("items") or [{"label": b} for b in s.get("bullets", [])]
    items = items[:4] or [{"label": "항목"}]
    numbered = bool(s.get("numbered", False))  # 비순차 분류는 숫자 없음(첨삭)
    n = len(items)
    gap = 0.34
    card_w = (CONTENT_W - gap * (n - 1)) / n
    y, card_h = 2.95, 2.7
    for i, it in enumerate(items):
        x = MARGIN + i * (card_w + gap)
        accent = _BADGE_ROT[i % len(_BADGE_ROT)]
        _rrect(slide, x, y, card_w, card_h, fill="white", radius=0.1,
               line="chip", line_w=1.5)
        if numbered:
            _oval(slide, x + 0.28, y + 0.28, 0.55, accent)
            tfb = _text(slide, x + 0.28, y + 0.28, 0.55, 0.55, anchor="m")
            _run(_para(tfb, True, align="c"), str(i + 1), 16, "white", bold=True)
        else:
            # 숫자 대신 색 구분 도형(상단 컬러 바)
            _rrect(slide, x, y, card_w, 0.16, fill=accent, radius=0.3)
            _oval(slide, x + 0.28, y + 0.4, 0.22, accent)
        tft = _text(slide, x + 0.28, y + (1.0 if numbered else 0.78), card_w - 0.56, 0.5)
        _run(_para(tft, True), it.get("label", ""), 16, "navy", bold=True)
        if it.get("desc"):
            dy = y + (1.55 if numbered else 1.33)
            tfd = _text(slide, x + 0.28, dy, card_w - 0.56, card_h - (dy - y) - 0.15)
            _run(_para(tfd, True, line=1.15), it["desc"], 12.5, "grey", bold=False)
    add_footer_key(slide, s.get("key") or s.get("footer"))


def render_compare(slide, s):
    add_title(slide, s.get("title", ""))
    add_chip(slide, s.get("chip"))
    items = (s.get("items") or [])[:2]
    while len(items) < 2:
        items.append({"label": "", "desc": ""})
    y, h = 2.95, 3.6
    col_w = (CONTENT_W - 0.4) / 2
    colors = ("navy", "teal")
    for i, it in enumerate(items):
        x = MARGIN + i * (col_w + 0.4)
        _rrect(slide, x, y, col_w, h, fill="chip", radius=0.08)
        # 컬러 헤더 바 + 가운데 정렬 라벨(첨삭: 비교 라벨 가운데 정렬)
        _rrect(slide, x, y, col_w, 0.62, fill=colors[i % 2], radius=0.08)
        tft = _text(slide, x + 0.2, y, col_w - 0.4, 0.62, anchor="m")
        _run(_para(tft, True, align="c"), it.get("label", ""), 16, "white", bold=True)
        lines = it.get("lines") or ([it.get("desc")] if it.get("desc") else [])
        if lines:
            tf = _text(slide, x + 0.25, y + 0.85, col_w - 0.5, h - 1.0, anchor="t")
            for j, ln in enumerate(lines):
                _run(_para(tf, j == 0, align="c", space_after=7, line=1.2),
                     str(ln), 13.5, "grey", bold=False)


def render_table(slide, s):
    from pptx.util import Inches, Pt
    add_title(slide, s.get("title", ""))
    by = add_chip(slide, s.get("chip"))
    rows_data = s.get("rows")
    if not rows_data:
        return render_bullets(slide, s)
    headers = s.get("headers") or []
    y = max(by + 0.25, 2.8)
    ncol = len(headers) or max(len(r) for r in rows_data)
    nrow = len(rows_data) + (1 if headers else 0)
    tbl_shape = slide.shapes.add_table(nrow, ncol, Inches(MARGIN), Inches(y),
                                       Inches(CONTENT_W), Inches(min(0.5 * nrow, 4.0)))
    tbl = tbl_shape.table
    from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

    def _style_cell(cell, text, *, bold, size):
        cell.text = str(text)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        for para in cell.text_frame.paragraphs:
            para.alignment = PP_ALIGN.CENTER  # 첨삭: 표 셀 텍스트 가운데 정렬
            for run in para.runs:
                run.font.bold = bold; run.font.size = Pt(size); run.font.name = FONT

    r0 = 0
    if headers:
        for c, htxt in enumerate(headers):
            _style_cell(tbl.cell(0, c), htxt, bold=True, size=12)  # 헤더 bold
        r0 = 1
    for ri, row in enumerate(rows_data):
        for c in range(ncol):
            _style_cell(tbl.cell(ri + r0, c), row[c] if c < len(row) else "",
                        bold=False, size=11)


_RENDER = {
    "cover": lambda sl, s, img: render_cover(sl, s),
    "section": lambda sl, s, img: render_section(sl, s),
    "photo": render_photo,
    "process": lambda sl, s, img: render_process(sl, s),
    "cards": lambda sl, s, img: render_cards(sl, s),
    "compare": lambda sl, s, img: render_compare(sl, s),
    "table": lambda sl, s, img: render_table(sl, s),
    "bullets": lambda sl, s, img: render_bullets(sl, s),
}


def build_deck(plan: List[Dict], template_path: Optional[str] = None,
               images: Optional[Dict[int, bytes]] = None,
               deck_title: str = "강의 슬라이드",
               logo_path: Optional[str] = None) -> Optional[bytes]:
    """슬라이드플랜 → 디자인된 .pptx 바이트. python-pptx 미설치 시 None."""
    try:
        from pptx import Presentation
        from pptx.util import Inches
    except Exception:  # noqa: BLE001
        return None

    images = images or {}
    use_tpl = bool(template_path and os.path.isfile(template_path))
    if use_tpl:
        try:
            prs = Presentation(template_path)
            _remove_all_slides(prs)
        except Exception:  # noqa: BLE001
            prs = Presentation(); use_tpl = False
    else:
        prs = Presentation()
    if not use_tpl:
        prs.slide_width = Inches(SLIDE_W)
        prs.slide_height = Inches(SLIDE_H)

    layout = _blank_layout(prs)
    for i, s in enumerate(plan):
        slide = prs.slides.add_slide(layout)
        _strip_placeholders(slide)
        s["_idx"] = i  # 사진 배치 프리셋 순환용
        typ = (s.get("type") or "bullets").lower()
        fn = _RENDER.get(typ, _RENDER["bullets"])
        try:
            fn(slide, s, images.get(i))
        except Exception as e:  # noqa: BLE001
            print(f"[deck] slide {i} ({typ}) 렌더 오류: {e}", flush=True)
            try:
                render_bullets(slide, s)
            except Exception:  # noqa: BLE001
                pass
        add_logo(slide, logo_path)

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


# ── 아트디렉터(LLM) 패스 — 개요 md → 슬라이드플랜 JSON ─────────────────────
_ART_SYS = (
    "너는 강의 슬라이드 아트디렉터다. 주어진 슬라이드 개요를 슬라이드플랜 JSON 배열로만 변환한다. "
    "설명·머리말·코드펜스 없이 JSON 배열만 출력한다."
)

_ART_RULES = """개요의 '### 슬라이드 N' 블록 하나당 JSON 객체 하나를 순서대로 만든다(개수 유지).
각 객체 스키마:
{
  "type": "section|photo|process|cards|compare|table|bullets",
  "title": "간결·정확한 제목",
  "chip": "요약 한 문장(명사형). 없으면 생략 가능",
  "bullets": ["불릿 3~5개(명사형 어미)"],
  "items": [{"label":"짧은 이름","desc":"한 줄 설명"}],
  "image_query": "photo형일 때만, 영어 검색어",
  "numbered": false,
  "emphasis": false
}
문체·제목:
- 불릿·라벨은 **명사형 어미**(예 "즉시 피드백 제공" → "즉시 피드백"). 존댓말·서술체·교수자 나레이션 문장 금지.
- 제목은 간결·일관("정의와 과정"·"개요"·"논의" 같은 군더더기 제거).
타입 선택 규칙:
- "cover"는 만들지 마라(표지는 시스템이 맨 앞에 자동 추가).
- 순서·단계·절차가 핵심: "process" (items[].label 2~5개). 화살표로 연결됨.
- 병렬·분류·구성요소: "cards" (items[].label + desc). **비순차이므로 numbered=false(기본). 순서가 있을 때만 numbered=true.**
- 두 개념 대비/비교: "compare" (items 2개, 각 {"label","lines":[...]}).
- 구성 개요·유형 비교 등 표가 자연스러운 것: "table" (headers:[...], rows:[[...]]).
- 도입·구간 전환: "section".
- 사진이 이해를 돕는 개념/사례/인물: "photo" + 구체적 영어 image_query(막연한 'education' 금지). 예) 행동주의→'Pavlov conditioning experiment', 인지주의→'human brain memory diagram', 매체→'classroom projector lesson', 인물→'portrait of a scholar'. 전체의 약 40~55%.
- 그 외: "bullets".
이미지 배치 규칙(중요):
- **학습목표·문제·퀴즈·순수 정리 슬라이드는 photo 로 하지 말 것(이미지 없음).** 이런 슬라이드는 bullets/cards/table 로.
- 사례·실물·인물·대표 이론 슬라이드에는 photo 지정.
강조(emphasis):
- **emphasis=true 는 문제/질문/학습목표/퀴즈 슬라이드에만.** 그 외에는 false(강조 남용 금지).
공통:
- 같은 타입이 여러 장 연속되지 않게 섞는다. 동일 내용이 반복되면 한 슬라이드로 합친다.
- process/cards엔 key(하단 핵심 한 줄, 명사형)를 넣어도 좋다.
"""


def _extract_json_array(text: str):
    """JSON 배열 파싱. 잘린 응답도 완결된 {…} 객체만 골라 살려낸다."""
    if not text:
        return None
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text.strip())
    i = t.find("[")
    if i == -1:
        return None
    j = t.rfind("]")
    if j > i:
        try:
            return json.loads(t[i:j + 1])
        except Exception:  # noqa: BLE001
            pass
    # 살리기: 배열 안의 완결된 최상위 { … } 객체들을 순서대로 파싱
    objs, depth, start, instr, esc = [], 0, None, False, False
    for k in range(i + 1, len(t)):
        c = t[k]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
            continue
        if c == '"':
            instr = True
        elif c == "{":
            if depth == 0:
                start = k
            depth += 1
        elif c == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        objs.append(json.loads(t[start:k + 1]))
                    except Exception:  # noqa: BLE001
                        pass
                    start = None
    return objs or None


def _outline_blocks(outline_md: str) -> List[str]:
    """개요 md 를 '### 슬라이드 …' 단위 블록 리스트로 분할."""
    parts = re.split(r"(?m)^(?=#{2,3}\s*슬라이드)", outline_md or "")
    return [p.strip() for p in parts if re.match(r"#{2,3}\s*슬라이드", p.strip())]


def _block_to_bullets(block: str) -> Dict:
    """개요 블록 하나 → bullets 슬라이드 dict(아트디렉터 실패 시 폴백)."""
    lines = [l.strip() for l in block.splitlines() if l.strip()]
    title = re.sub(r"^#{2,3}\s*", "", lines[0]) if lines else "슬라이드"
    title = re.sub(r"^슬라이드\s*\d+\s*[—\-:：]\s*", "", title).strip()
    chip, bullets = "", []
    for ln in lines[1:]:
        m = re.match(r"^[-*+]?\s*\*{0,2}([^*:：]{1,20})\*{0,2}\s*[:：]\s*(.*)$", ln)
        if m and "핵심" in m.group(1):
            chip = m.group(2).strip()
        elif m and "레이아웃" in m.group(1):
            continue
        else:
            v = re.sub(r"^[-*+•·]\s*", "", ln)
            if v and not re.match(r"^\**본문", v):
                bullets.append(v)
    return {"type": "bullets", "title": title, "chip": chip, "bullets": bullets[:5]}


def _fallback_plan(outline_md: str, deck_title: str) -> List[Dict]:
    """개요 md 를 단순 파싱해 bullets 위주 플랜으로(아트디렉터 실패 시)."""
    plan = [{"type": "cover", "title": deck_title, "chip": ""}]
    plan += [_block_to_bullets(b) for b in _outline_blocks(outline_md)]
    return plan


def _normalize_slide(s: Dict) -> Optional[Dict]:
    if not isinstance(s, dict):
        return None
    s.setdefault("type", "bullets")
    s.setdefault("title", "")
    s.setdefault("bullets", [])
    if s["type"] == "cover":   # LLM이 표지를 만들면 내용형으로 강등
        s["type"] = "bullets"
    return s


_CHUNK = 14  # 청크당 슬라이드 수(토큰 잘림 방지)


def plan_from_outline(generate_fn: Callable[[str, str, int], str],
                      outline_md: str, deck_title: str,
                      subtitle: str = "") -> List[Dict]:
    """개요를 슬라이드플랜으로 변환. 개요를 청크로 나눠 변환해 개수 잘림을 막는다.

    표지(cover)는 LLM이 아니라 여기서 맨 앞에 자동 추가한다. 청크가 실패하면
    그 청크만 블록 파싱(bullets)으로 폴백 → 전체 슬라이드 수는 항상 보존.
    """
    blocks = _outline_blocks(outline_md)
    if not blocks:
        return _fallback_plan(outline_md, deck_title)

    chunks = [blocks[i:i + _CHUNK] for i in range(0, len(blocks), _CHUNK)]
    out: List[Dict] = []
    for ci, ch in enumerate(chunks):
        chunk_md = "\n\n".join(ch)
        user = (f"{_ART_RULES}\n\n[덱 제목]\n{deck_title}\n"
                f"[부분 {ci + 1}/{len(chunks)} · 이 부분의 슬라이드 {len(ch)}개를 빠짐없이 변환]\n\n"
                f"[슬라이드 개요]\n{chunk_md}")
        try:
            raw = generate_fn(_ART_SYS, user, 12000)
        except Exception as e:  # noqa: BLE001
            print(f"[art] 청크 {ci + 1} 생성 오류: {e}", flush=True)
            raw = ""
        arr = _extract_json_array(raw)
        got = [x for x in (_normalize_slide(s) for s in arr) if x] if isinstance(arr, list) else []
        # 개수가 모자라면 부족분을 블록 파싱으로 보충(개수 보존)
        if len(got) < len(ch):
            print(f"[art] 청크 {ci + 1}: {len(got)}/{len(ch)} → 부족분 블록 폴백", flush=True)
            got += [_block_to_bullets(b) for b in ch[len(got):]]
        out += got[:len(ch)]

    out.insert(0, {"type": "cover", "title": deck_title, "chip": subtitle})
    return out


def image_queries(plan: List[Dict]) -> Dict[int, str]:
    """photo 타입 슬라이드의 {인덱스: 영어 검색어}."""
    out = {}
    for i, s in enumerate(plan):
        if (s.get("type") == "photo") and s.get("image_query"):
            out[i] = s["image_query"]
    return out


# ── 이미지 생성 프롬프트 번들 (codex-prompt-img-studio 인풋 JSON) ──────────
DEFAULT_STYLE_HINT = ("clean modern educational illustration, flat vector with subtle depth, "
                      "soft navy (#1E2761) and amber (#F2A900) accents, uncluttered, 16:9")

_IMGPROMPT_SYS = ("너는 강의 슬라이드용 이미지 생성 프롬프트 작가다. 각 슬라이드 제목/요약을 보고 "
                  "이미지 생성 모델(diffusion)용 **영어** 프롬프트 한 줄을 만든다. 그림 안에 글자·워터마크·"
                  "로고가 들어가지 않게 하고, 주제를 상징하는 구체적 장면/오브젝트로 묘사한다. "
                  "JSON 배열 [{\"n\":정수,\"prompt\":\"...\"}] 만 출력한다.")


def _prompt_place(s: Dict) -> bool:
    """이미지를 실제로 배치할 슬라이드인가(학습목표·문제·퀴즈·표지·섹션 제외)."""
    if s.get("emphasis"):
        return False
    return (s.get("type") or "bullets") not in ("cover", "section", "table")


def image_prompt_bundle(plan: List[Dict], deck_title: str, *,
                        generate_fn: Optional[Callable[[str, str, int], str]] = None,
                        style_hint: str = DEFAULT_STYLE_HINT) -> Dict:
    """덱 전체 슬라이드의 이미지 생성 프롬프트를 1개 번들(dict)로.

    generate_fn 이 있으면 LLM으로 영어 프롬프트를 짓고(청크·폴백), 없으면
    image_query/제목 기반으로 결정론적으로 만든다. codex-prompt-img-studio 가
    'prompts' 배열을 위→아래 순차 실행하도록 설계.
    """
    n = len(plan)
    en: Dict[int, str] = {}
    if generate_fn:
        idx = list(range(n))
        for a in range(0, n, _CHUNK):
            ch = idx[a:a + _CHUNK]
            lines = "\n".join(
                f'{i + 1}. {plan[i].get("title", "")} | {plan[i].get("chip", "")}' for i in ch)
            user = (f"[공통 스타일] {style_hint}\n"
                    f"아래 슬라이드 {len(ch)}개 각각에 대해 n(슬라이드 번호)과 영어 이미지 프롬프트를 만들어라. "
                    f"프롬프트에 공통 스타일을 녹이고, 그림 안 텍스트 금지.\n\n{lines}")
            try:
                arr = _extract_json_array(generate_fn(_IMGPROMPT_SYS, user, 6000))
            except Exception as e:  # noqa: BLE001
                print(f"[imgprompt] 청크 오류: {e}", flush=True); arr = None
            if isinstance(arr, list):
                for o in arr:
                    if isinstance(o, dict) and o.get("n") and o.get("prompt"):
                        en[int(o["n"]) - 1] = str(o["prompt"]).strip()

    prompts = []
    for i, s in enumerate(plan):
        subj = en.get(i) or s.get("image_query") or f"conceptual illustration for '{s.get('title', '')}'"
        prompt = f"{subj}. {style_hint}. No text, no watermark, no logo."
        prompts.append({
            "n": i + 1,
            "title": s.get("title", ""),
            "type": s.get("type", "bullets"),
            "prompt": prompt,
            "negative": "text, letters, watermark, logo, low quality, distorted",
            "keywords": ([s["image_query"]] if s.get("image_query") else []),
            "place": _prompt_place(s),
        })
    return {
        "deck": deck_title,
        "style_hint": style_hint,
        "aspect": "landscape",
        "count": n,
        "prompts": prompts,
    }
