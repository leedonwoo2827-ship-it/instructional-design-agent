# -*- coding: utf-8 -*-
"""슬라이드 폰트(Pretendard) 내려받기 — assets/fonts/ 에 TTF 3종을 놓는다.

Pretendard 는 SIL Open Font License 1.1 로 상업 이용·PPT 임베드가 허용된다.
용량(약 8MB)이 커 저장소에는 커밋하지 않으므로(.gitignore) 설치 시 한 번 받는다.

    python tools/get_fonts.py            # 없으면 받고, 이미 있으면 건너뜀
    python tools/get_fonts.py --force    # 다시 받기

폰트가 없어도 앱은 정상 동작한다 — '맑은 고딕'으로 자동 폴백한다.
"""
from __future__ import annotations

import argparse
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

URL = ("https://github.com/orioncactus/pretendard/releases/download/"
       "v1.3.9/Pretendard-1.3.9.zip")
SRC_DIR = "public/static/alternative"     # PPT 임베드는 TTF 만 가능(OTF 불가)
WANTED = ("Pretendard-Regular.ttf", "Pretendard-SemiBold.ttf", "Pretendard-Bold.ttf")
_ROOT = Path(__file__).resolve().parent.parent
DEST = _ROOT / "assets" / "fonts"        # PPTX 임베드용
WEB = _ROOT / "static" / "fonts"         # 웹 UI @font-face 용


def _mirror_to_web() -> None:
    """웹 UI 도 같은 폰트를 쓴다 — 화면과 산출물의 글자가 달라 보이지 않게."""
    import shutil
    WEB.mkdir(parents=True, exist_ok=True)
    for f in WANTED:
        src = DEST / f
        if src.is_file():
            shutil.copyfile(src, WEB / f)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="이미 있어도 다시 받기")
    args = ap.parse_args()

    DEST.mkdir(parents=True, exist_ok=True)
    if not args.force and all((DEST / f).is_file() for f in WANTED):
        _mirror_to_web()
        print(f"[font] 이미 설치됨: {DEST}")
        return 0

    print(f"[font] 내려받는 중 (약 45MB) … {URL}")
    try:
        with urllib.request.urlopen(URL, timeout=180) as r:
            blob = r.read()
    except Exception as e:  # noqa: BLE001
        print(f"[font] 실패: {e}")
        print("[font] 폰트 없이도 앱은 동작합니다(맑은 고딕 폴백).")
        print(f"[font] 수동 설치: {URL} 를 받아 {SRC_DIR} 안의 다음 파일을 "
              f"{DEST} 로 복사하세요 → {', '.join(WANTED)}")
        return 1

    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            for name in WANTED:
                data = z.read(f"{SRC_DIR}/{name}")
                (DEST / name).write_bytes(data)
                print(f"[font]   {name}  {len(data):,} bytes")
            (DEST / "OFL.txt").write_bytes(z.read("LICENSE.txt"))
    except Exception as e:  # noqa: BLE001
        print(f"[font] 압축 해제 실패: {e}")
        return 1

    _mirror_to_web()
    print(f"[font] 완료 — {DEST}")
    print("[font] 이제 디자인 슬라이드가 Pretendard 로 만들어지고, .pptx 에 폰트가 "
          "임베드되어 다른 PC 에서도 그대로 열립니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
