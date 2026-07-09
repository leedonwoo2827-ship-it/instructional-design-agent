# -*- coding: utf-8 -*-
"""무료 CC 이미지 검색·다운로드 (Openverse REST, 키 불필요).

디자인 슬라이드 빌더가 슬라이드 주제(영문 검색어)로 사진 1장을 받아오는 용도.
- 네트워크/레이트리밋/미설치는 모두 None 으로 그레이스풀 처리(사진 없이도 덱은 완성).
- 다운로드한 이미지마다 출처(제목/저작자/라이선스/원본URL)를 함께 돌려줘, 애프터 동봉
  '이미지 출처.txt' 와 동일한 크레딧 파일을 만들 수 있게 한다.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

_API = "https://api.openverse.org/v1/images/"
_UA = "gyosu-design-agent/1.0 (educational slide builder)"

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
    version = (version or "").strip()
    return f"{label} {version}".strip()


def search_credit(query: str, *, timeout: float = 8.0) -> Optional[Dict[str, str]]:
    """검색어로 Openverse 이미지 1건의 메타(다운로드 URL 포함)를 반환. 실패 시 None."""
    rq = _requests()
    if not rq or not query:
        return None
    params = {
        "q": query, "page_size": 8, "license_type": "all",
        "mature": "false",
    }
    try:
        r = rq.get(_API, params=params, headers={"User-Agent": _UA}, timeout=timeout)
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
            "download_url": url,
            "title": (it.get("title") or "이미지").strip(),
            "creator": (it.get("creator") or "Unknown").strip(),
            "license": _fmt_license(it.get("license", ""), it.get("license_version", "")),
            "source_url": it.get("foreign_landing_url") or it.get("url") or "",
        }
    return None


def download(url: str, *, timeout: float = 12.0) -> Optional[bytes]:
    """이미지 바이트 다운로드. 실패 시 None."""
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
          grayscale: bool = False) -> Tuple[Optional[bytes], Optional[Dict[str, str]]]:
    """검색어 → (이미지 bytes, 크레딧 dict). 실패 시 (None, None).

    cache: 동일 검색어 재요청 방지용 세션 dict(선택).
    grayscale: True 면 Pillow 로 흑백 변환(미설치 시 원본 유지).
    """
    if cache is not None and query in cache:
        return cache[query]
    credit = search_credit(query)
    data = download(credit["download_url"]) if credit else None
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
    """[(slide_no, credit_dict)] → 애프터 포맷의 출처 텍스트."""
    lines = [title_line, "=" * 50, ""]
    for no, c in entries:
        if not c:
            continue
        lines.append(f"슬라이드 {no}: {c.get('title', '이미지')}")
        lines.append(f"  저작자: {c.get('creator', 'Unknown')} | 라이선스: {c.get('license', '')}")
        lines.append(f"  출처: {c.get('source_url', '')}")
        lines.append("")
    lines.append("※ 모든 이미지는 Openverse(openverse.org)에서 수집한 "
                 "크리에이티브 커먼즈/퍼블릭 도메인 자료입니다. "
                 "CC BY 등은 배포 시 저작자 표시를 권장합니다.")
    return "\n".join(lines)
