# -*- coding: utf-8 -*-
r"""슬라이드 배색 템플릿 — templates/*.json 을 읽어 deck_builder 에 주입한다.

배자(좌표)는 템플릿에서 읽지 않는다. 내용 길이가 템플릿과 다를 때 넘치거나
빈 공간이 생기고, 그건 deck_builder 의 _fit() 이 이미 처리한다. 여기서 바꾸는
것은 **색과 글꼴** 뿐이고, 그것만으로도 덱의 인상이 완전히 달라진다.

templates/<이름>.json:
    { "name", "label", "note", "font", "colors": { <키>: "RRGGBB", … } }

색 키는 deck_builder._HEX 의 키와 같아야 한다. 빠진 키는 기본 배색에서 채운다 —
템플릿을 절반만 채워도 덱이 깨지지 않게 하려는 것이다.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, List

TPL_DIR = Path(__file__).resolve().parent.parent / "templates"
# 회사 로고가 청색이라 시안을 기본으로 둔다.
# ★ templates/ 에서 이 이름의 json 을 지우면 전 주차가 조용히 다른 색으로 빌드된다.
DEFAULT = "cyan"

# _HEX 를 제자리에서 바꾸므로 빌드가 겹치면 색이 섞인다. 빌드는 순간이지만
# 두 요청이 동시에 들어올 수 있어 락을 둔다.
_LOCK = threading.RLock()


def _hex_to_rgb(v: str) -> tuple:
    v = (v or "").lstrip("#").strip()
    if len(v) != 6:
        raise ValueError(f"색 형식이 RRGGBB 가 아닙니다: {v!r}")
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))


def available() -> List[Dict[str, Any]]:
    """고를 수 있는 템플릿 목록. 화면의 <select> 가 그대로 쓴다."""
    out: List[Dict[str, Any]] = []
    if not TPL_DIR.is_dir():
        return out
    for p in sorted(TPL_DIR.glob("*.json")):
        if p.name.endswith(".raw.json"):      # 추출 원본은 후보가 아니다
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({"name": d.get("name") or p.stem,
                    "label": d.get("label") or p.stem,
                    "note": d.get("note") or "",
                    "font": d.get("font") or "",
                    "colors": len(d.get("colors") or {})})
    return out


def names() -> List[str]:
    return [t["name"] for t in available()]


def load(name: str | None) -> Dict[str, Any]:
    """템플릿 하나. 없으면 기본으로 떨어진다(빌드가 멈추지 않게)."""
    name = (name or DEFAULT).strip()
    for cand in (name, DEFAULT):
        p = TPL_DIR / f"{cand}.json"
        if p.is_file():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                d.setdefault("name", cand)
                return d
            except (OSError, json.JSONDecodeError):
                continue
    return {"name": DEFAULT, "colors": {}}


def apply(name: str | None) -> Dict[str, Any]:
    """deck_builder 의 색·글꼴을 이 템플릿으로 바꾼다. 적용된 템플릿을 돌려준다.

    ★ 기본 배색을 **한 번만** 스냅샷해 둔다. 그래야 템플릿을 여러 번 바꿔도
      빠진 키가 '직전 템플릿의 색' 이 아니라 항상 기본색으로 채워진다.
    """
    from core import deck_builder as db

    with _LOCK:
        if not hasattr(db, "_HEX_BASE"):
            db._HEX_BASE = dict(db._HEX)

        tpl = load(name)
        merged = dict(db._HEX_BASE)
        bad = []
        for k, v in (tpl.get("colors") or {}).items():
            if k not in db._HEX_BASE:
                bad.append(k)             # 모르는 키는 무시하되 알린다
                continue
            try:
                merged[k] = _hex_to_rgb(v)
            except ValueError:
                bad.append(f"{k}={v}")
        db._HEX.clear()
        db._HEX.update(merged)

        font = (tpl.get("font") or "").strip()
        db.TEMPLATE_FONT = font or None    # build_deck 이 이 값을 우선 쓴다
        db.TEMPLATE_NAME = tpl.get("name") or DEFAULT

        if bad:
            print(f"[palette] {tpl.get('name')}: 무시한 항목 {bad}", flush=True)
        return tpl


def current() -> str:
    from core import deck_builder as db
    return getattr(db, "TEMPLATE_NAME", DEFAULT)
