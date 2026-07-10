# -*- coding: utf-8 -*-
"""무료 이미지 검색·다운로드 — Unsplash(키 있으면 우선) + Openverse(폴백, 키 불필요).

디자인 슬라이드 빌더가 슬라이드 주제(영문 검색어)로 사진 1장을 받아오는 용도.
- Unsplash: 심미성 높음. Access Key 필요(무료). 가이드라인상 저작자 표시 + 사용 시
  download 트래킹 엔드포인트 호출을 자동 처리한다.
- Openverse: 키 불필요 폴백(CC/퍼블릭 도메인).
- 네트워크/레이트리밋/미설치는 모두 None 으로 그레이스풀(사진 없이도 덱은 완성).
- 다운로드마다 출처(source·저작자·라이선스·링크)를 함께 돌려줘 크레딧 파일을 만든다.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

_OV_API = "https://api.openverse.org/v1/images/"
_UN_API = "https://api.unsplash.com/search/photos"
_UA = "gyosu-design-agent/1.0 (educational slide builder)"
_UTM = "?utm_source=gyosu_design_agent&utm_medium=referral"

# CC 라이선스 코드 → 표기
_LIC = {
    "by": "CC BY", "by-sa": "CC BY-SA", "by-nc": "CC BY-NC",
    "by-nd": "CC BY-ND", "by-nc-sa": "CC BY-NC-SA", "by-nc-nd": "CC BY-NC-ND",
    "cc0": "CC CC0", "pdm": "CC PDM",
}


def _requests():
    try:
        import requests  # noqa: PLC0415
        return requests
    except Exception:  # noqa: BLE001
        return None


def _fmt_license(code: str, version: str) -> str:
    label = _LIC.get((code or "").lower(), (code or "").upper())
    return f"{label} {(version or '').strip()}".strip()


# ── Unsplash ──────────────────────────────────────────────────────────────
def _unsplash_credit(query: str, key: str, *, timeout: float = 8.0) -> Optional[Dict[str, str]]:
    rq = _requests()
    if not rq or not query or not key:
        return None
    try:
        r = rq.get(_UN_API, params={"query": query, "per_page": 5, "orientation": "landscape"},
                   headers={"Authorization": f"Client-ID {key}", "User-Agent": _UA,
                            "Accept-Version": "v1"}, timeout=timeout)
        if r.status_code != 200:
            return None
        results = (r.json() or {}).get("results") or []
    except Exception:  # noqa: BLE001
        return None
    if not results:
        return None
    it = results[0]
    user = it.get("user") or {}
    urls = it.get("urls") or {}
    links = it.get("links") or {}
    name = (user.get("name") or "Unknown").strip()
    uname = user.get("username") or ""
    return {
        "source": "Unsplash",
        "download_url": urls.get("regular") or urls.get("full") or urls.get("small") or "",
        "download_location": links.get("download_location") or "",
        "title": (it.get("description") or it.get("alt_description") or "이미지").strip(),
        "creator": name,
        "creator_url": (f"https://unsplash.com/@{uname}" + _UTM) if uname else "",
        "license": "Unsplash License",
        "source_url": (links.get("html") or "") + (_UTM if links.get("html") else ""),
    }


def _unsplash_trigger(location: str, key: str) -> None:
    """Unsplash 가이드라인: 사진 사용 시 download 엔드포인트 1회 호출."""
    rq = _requests()
    if not rq or not location or not key:
        return
    try:
        rq.get(location, headers={"Authorization": f"Client-ID {key}"}, timeout=6)
    except Exception:  # noqa: BLE001
        pass


# ── Openverse ─────────────────────────────────────────────────────────────
def _openverse_credit(query: str, *, timeout: float = 8.0) -> Optional[Dict[str, str]]:
    rq = _requests()
    if not rq or not query:
        return None
    try:
        r = rq.get(_OV_API, params={"q": query, "page_size": 8, "license_type": "all",
                                    "mature": "false"},
                   headers={"User-Agent": _UA}, timeout=timeout)
        if r.status_code != 200:
            return None
        results = (r.json() or {}).get("results") or []
    except Exception:  # noqa: BLE001
        return None
    for it in results:
        url = it.get("url") or ""
        if not url.lower().split("?")[0].endswith((".jpg", ".jpeg", ".png")):
            continue
        return {
            "source": "Openverse",
            "download_url": url,
            "title": (it.get("title") or "이미지").strip(),
            "creator": (it.get("creator") or "Unknown").strip(),
            "license": _fmt_license(it.get("license", ""), it.get("license_version", "")),
            "source_url": it.get("foreign_landing_url") or it.get("url") or "",
        }
    return None


# ── 공통 ──────────────────────────────────────────────────────────────────
def download(url: str, *, timeout: float = 12.0) -> Optional[bytes]:
    rq = _requests()
    if not rq or not url:
        return None
    try:
        r = rq.get(url, headers={"User-Agent": _UA}, timeout=timeout)
        if r.status_code != 200 or not r.content:
            return None
        return r.content
    except Exception:  # noqa: BLE001
        return None


def fetch(query: str, *, cache: Optional[Dict[str, Tuple]] = None,
          grayscale: bool = False, unsplash_key: str = "") -> Tuple[Optional[bytes], Optional[Dict[str, str]]]:
    """검색어 → (이미지 bytes, 크레딧 dict). 실패 시 (None, None).

    unsplash_key 있으면 Unsplash 우선, 실패 시 Openverse 폴백.
    """
    if cache is not None and query in cache:
        return cache[query]

    credit = _unsplash_credit(query, unsplash_key) if unsplash_key else None
    if not credit:
        credit = _openverse_credit(query)

    data = download(credit["download_url"]) if credit else None
    if data and credit and credit.get("source") == "Unsplash":
        _unsplash_trigger(credit.get("download_location", ""), unsplash_key)
    if data and grayscale:
        data = _to_grayscale(data) or data

    out = (data, credit if data else None)
    if cache is not None:
        cache[query] = out
    return out


def _to_grayscale(data: bytes) -> Optional[bytes]:
    try:
        import io  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415
        im = Image.open(io.BytesIO(data)).convert("L").convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=88)
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None


def credits_text(title_line: str, entries) -> str:
    """[(slide_no, credit_dict)] → 출처 텍스트(소스별 형식). Unsplash 저작자 표시 규격 준수."""
    lines = [title_line, "=" * 50, ""]
    has_un = has_ov = False
    for no, c in entries:
        if not c:
            continue
        if c.get("source") == "Unsplash":
            has_un = True
            lines.append(f"슬라이드 {no}: Photo by {c.get('creator', 'Unknown')} on Unsplash")
            if c.get("creator_url"):
                lines.append(f"  작가: {c['creator_url']}")
            if c.get("source_url"):
                lines.append(f"  사진: {c['source_url']}")
        else:
            has_ov = True
            lines.append(f"슬라이드 {no}: {c.get('title', '이미지')}")
            lines.append(f"  저작자: {c.get('creator', 'Unknown')} | 라이선스: {c.get('license', '')}")
            lines.append(f"  출처: {c.get('source_url', '')}")
        lines.append("")
    if has_un:
        lines.append("※ Unsplash 사진은 Unsplash License(상업적 사용 포함 무료)이며, "
                     "위와 같이 'Photo by 작가 on Unsplash' 저작자 표시를 권장합니다.")
    if has_ov:
        lines.append("※ Openverse 사진은 크리에이티브 커먼즈/퍼블릭 도메인 자료입니다. "
                     "CC BY 등은 배포 시 저작자 표시를 권장합니다.")
    return "\n".join(lines)
