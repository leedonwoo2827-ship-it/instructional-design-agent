# -*- coding: utf-8 -*-
"""교수설계 시스템 프롬프트 로더.

프롬프트는 코드가 아니라 skills/<name>/SKILL.md 에서 관리한다(전문가 첨삭을 매일
파일 편집만으로 반영). 각 SKILL.md 의 YAML frontmatter 는 벗겨내고 본문만 사용한다.
import 시 1회 로드되며, 파일을 수정한 뒤 앱을 재시작하면 반영된다.

근거: ABCD 모델, Bloom 개정분류(Anderson & Krathwohl, 2001), 구성적 정렬(Biggs, 1996),
백워드 설계·WHERETO(Wiggins & McTighe, 2005), 멀티미디어 학습 원리(Mayer, 2009).
"""
from __future__ import annotations

import re
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

# skills 파일이 없을 때를 위한 최소 폴백(안전망)
_FALLBACK = "당신은 대학 교수자를 지원하는 '교수설계 가이드 에이전트'입니다. 한국어 마크다운으로만 답합니다."


def _strip_frontmatter(text: str) -> str:
    """맨 앞의 --- ... --- YAML frontmatter 제거."""
    if text.lstrip().startswith("---"):
        m = re.match(r"\s*---\s*\n.*?\n---\s*\n?", text, flags=re.DOTALL)
        if m:
            return text[m.end():].lstrip("\n")
    return text


def _skill(name: str) -> str:
    """skills/<name>/SKILL.md 본문(프론트매터 제거)을 반환. 없으면 빈 문자열."""
    path = SKILLS_DIR / name / "SKILL.md"
    try:
        return _strip_frontmatter(path.read_text(encoding="utf-8")).strip()
    except OSError:
        return ""


def _compose(*names: str) -> str:
    parts = [_skill(n) for n in names]
    parts = [p for p in parts if p]
    return ("\n\n".join(parts)).strip() or _FALLBACK


# 모듈 상수 — app.py 는 이 이름들을 그대로 사용한다.
SYS_BASE = _compose("gyosu-base")
SYS_SYLLABUS = _compose("gyosu-base", "syllabus")
SYS_SCRIPT_DOC = _compose("gyosu-base", "textbook")
SYS_SCRIPT_PPT = _compose("gyosu-base", "slides")
SYS_CHECK_SYL = _compose("gyosu-base", "check-syllabus")
SYS_CHECK_SCR = _compose("gyosu-base", "check-script")
