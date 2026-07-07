# -*- coding: utf-8 -*-
"""PPT 개요(마크다운) → 실제 .pptx 변환.

SYS_SCRIPT_PPT 가 만드는 개요 형식(### 슬라이드 N — 제목 + 불릿 + 발표자 노트)을
파싱해 편집 가능한 PowerPoint 파일로 만든다. 완벽한 디자인이 아니라, 그대로 열어
다듬을 수 있는 골격을 제공하는 것이 목적이다.
"""
from __future__ import annotations

import io
import re
from typing import Optional


def _clean(text: str) -> str:
    text = re.sub(r"\*\*|__|`|~~", "", text)
    text = re.sub(r"^[\-\*\+•·]\s*", "", text)
    return text.strip()


def outline_to_pptx(md: str, deck_title: str = "강의 슬라이드") -> Optional[bytes]:
    """개요 마크다운을 .pptx 바이트로. python-pptx 미설치 시 None."""
    try:
        from pptx import Presentation
        from pptx.util import Pt
    except Exception:  # noqa: BLE001
        return None

    prs = Presentation()
    prs.slide_width = Pt(960)
    prs.slide_height = Pt(540)

    # 표지
    title_layout = prs.slide_layouts[0]
    s = prs.slides.add_slide(title_layout)
    s.shapes.title.text = deck_title
    try:
        s.placeholders[1].text = "교수설계 가이드 에이전트 · Mayer 멀티미디어 원리 기반"
    except Exception:  # noqa: BLE001
        pass

    # "### 슬라이드 …" 단위로 분할
    blocks = re.split(r"(?m)^\s*#{2,3}\s+", md or "")
    slide_blocks = [b for b in blocks if re.match(r"\s*슬라이드", b)]
    if not slide_blocks:
        # 슬라이드 마커가 없으면 문단 단위로라도 담는다
        slide_blocks = [b for b in blocks if b.strip()][:20]

    body_layout = prs.slide_layouts[1]  # Title and Content
    for block in slide_blocks:
        lines = [ln for ln in block.splitlines()]
        header = lines[0].strip() if lines else "슬라이드"
        title = re.sub(r"^슬라이드\s*\d+\s*[—\-:：]\s*", "", header).strip() or header

        body, notes, in_notes = [], [], False
        for ln in lines[1:]:
            t = ln.strip()
            if not t:
                continue
            if "발표자 노트" in t:
                in_notes = True
                after = re.split(r"[:：]", t, 1)
                if len(after) > 1 and after[1].strip():
                    notes.append(_clean(after[1]))
                continue
            if in_notes:
                notes.append(_clean(t))
            else:
                body.append(_clean(t))

        slide = prs.slides.add_slide(body_layout)
        slide.shapes.title.text = title[:120]
        try:
            tf = slide.placeholders[1].text_frame
            tf.clear()
            for i, line in enumerate(body[:12]):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                p.text = line[:200]
                p.font.size = Pt(16)
        except Exception:  # noqa: BLE001
            pass
        if notes:
            try:
                slide.notes_slide.notes_text_frame.text = "\n".join(notes)
            except Exception:  # noqa: BLE001
                pass

    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
