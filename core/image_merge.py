# -*- coding: utf-8 -*-
"""외부에서 만들어 온 이미지를 슬라이드에 합치기.

규칙은 하나뿐이다: **파일명 맨 앞 숫자 = 슬라이드 번호**(1-based).
이미지프롬프트 JSON 의 "n" 값과 같은 번호라 그대로 대응된다.

    images/003.png        → 3번 슬라이드
    images/07.jpg         → 7번 슬라이드
    images/012_뇌구조.png  → 12번 슬라이드
    images/표지.png        → 무시(숫자로 시작하지 않음)

같은 번호가 여러 개면 파일명 사전순 마지막 하나를 쓰고 나머지는 리포트한다.
자르거나 조용히 넘기는 대신 무엇이 무시됐는지 항상 돌려준다.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional

EXTS = (".png", ".jpg", ".jpeg", ".webp")
_NUM = re.compile(r"^\D{0,4}?(\d{1,3})")


class MergeResult(NamedTuple):
    images: Dict[int, bytes]      # {0-based 슬라이드 인덱스: 바이트}
    matched: Dict[int, str]       # {1-based 번호: 파일명}
    ignored: List[str]            # 숫자로 시작하지 않거나 확장자 불일치
    duplicates: List[str]         # 같은 번호라 밀려난 파일
    out_of_range: List[str]       # 슬라이드 수를 넘는 번호

    @property
    def summary(self) -> str:
        parts = [f"{len(self.matched)}장 매칭"]
        if self.duplicates:
            parts.append(f"중복 {len(self.duplicates)}개")
        if self.out_of_range:
            parts.append(f"범위 밖 {len(self.out_of_range)}개")
        if self.ignored:
            parts.append(f"무시 {len(self.ignored)}개")
        return " · ".join(parts)


def slide_no(filename: str) -> Optional[int]:
    """파일명 → 슬라이드 번호. 맨 앞(또는 짧은 접두어 뒤) 숫자군을 읽는다."""
    m = _NUM.match(Path(filename).stem)
    if not m:
        return None
    n = int(m.group(1))
    return n if n >= 1 else None


def _to_png(data: bytes) -> bytes:
    """python-pptx 가 다루기 쉬운 포맷으로. webp 등은 PNG 로 변환."""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        if (im.format or "").upper() in ("PNG", "JPEG"):
            return data
        buf = io.BytesIO()
        im.convert("RGB").save(buf, "PNG")
        return buf.getvalue()
    except Exception:  # noqa: BLE001
        return data


def scan(folder, n_slides: Optional[int] = None) -> MergeResult:
    """폴더를 훑어 슬라이드 인덱스별 이미지 바이트를 모은다."""
    d = Path(folder)
    matched: Dict[int, str] = {}
    images: Dict[int, bytes] = {}
    ignored: List[str] = []
    duplicates: List[str] = []
    oor: List[str] = []
    if not d.is_dir():
        return MergeResult({}, {}, [], [], [])

    # 사전순으로 훑으므로 같은 번호는 뒤 파일이 앞 파일을 덮는다(= 마지막 채택)
    for f in sorted(d.iterdir(), key=lambda p: p.name.lower()):
        if not f.is_file():
            continue
        if f.suffix.lower() not in EXTS:
            ignored.append(f.name)
            continue
        n = slide_no(f.name)
        if n is None:
            ignored.append(f.name)
            continue
        if n_slides is not None and n > n_slides:
            oor.append(f.name)
            continue
        if n in matched:
            duplicates.append(matched[n])
        try:
            images[n - 1] = _to_png(f.read_bytes())
            matched[n] = f.name
        except Exception:  # noqa: BLE001
            ignored.append(f.name)
    return MergeResult(images, matched, ignored, duplicates, oor)


def report(res: MergeResult, limit: int = 6) -> str:
    """UI 에 그대로 붙일 수 있는 여러 줄 리포트."""
    lines = [res.summary]
    if res.duplicates:
        lines.append("중복(밀려남): " + ", ".join(res.duplicates[:limit]))
    if res.out_of_range:
        lines.append("범위 밖: " + ", ".join(res.out_of_range[:limit]))
    if res.ignored:
        lines.append("무시(번호 없음/지원 안 함): " + ", ".join(res.ignored[:limit]))
    return "\n".join(lines)
