# -*- coding: utf-8 -*-
"""PPTX → 슬라이드 PNG (1920×1080).

Windows + PowerPoint COM 경로. `Presentation.Export()`는 전 슬라이드를 한 번에
내보내므로 장당 호출보다 훨씬 빠르다(102장 기준 수십 초).

주의:
  - PowerPoint가 로케일에 따라 `슬라이드1.PNG` / `Slide1.PNG` 로 뱉는다 → 숫자로 정렬해 정규화
  - `WithWindow=False`로 열고 try/finally로 반드시 Close/Quit — 안 하면 POWERPNT 프로세스가 남는다
  - VPS(Linux)에는 PowerPoint가 없다 → 같은 시그니처의 LibreOffice 렌더러를 나중에 붙인다
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

SLIDE_W, SLIDE_H = 1920, 1080
_NUM = re.compile(r"(\d+)")


def _sort_key(p: Path) -> int:
    m = _NUM.findall(p.stem)
    return int(m[-1]) if m else 0


def render_powerpoint(pptx_path: str | Path, out_dir: str | Path,
                      name_fmt: str = "{i:03d}.png",
                      width: int = SLIDE_W, height: int = SLIDE_H,
                      on_progress=None) -> list[Path]:
    """PPTX 전 슬라이드를 PNG로 내보내고 `name_fmt`(1-based `i`)로 이름을 정규화한다."""
    import pythoncom
    import win32com.client

    pptx_path = Path(pptx_path).resolve()
    out_dir = Path(out_dir).resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    raw = out_dir / "_raw"
    raw.mkdir(exist_ok=True)

    pythoncom.CoInitialize()
    app = pres = None
    try:
        app = win32com.client.Dispatch("PowerPoint.Application")
        pres = app.Presentations.Open(str(pptx_path), WithWindow=False)
        # Export는 폴더를 받아 전 슬라이드를 한 번에 쓴다.
        pres.Export(str(raw), "PNG", width, height)
    finally:
        try:
            if pres is not None:
                pres.Close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if app is not None:
                app.Quit()
        except Exception:  # noqa: BLE001
            pass
        pythoncom.CoUninitialize()

    files = sorted([p for p in raw.iterdir() if p.suffix.lower() == ".png"], key=_sort_key)
    if not files:
        raise RuntimeError(f"PowerPoint가 PNG를 만들지 못했습니다: {raw}")

    out: list[Path] = []
    for i, src in enumerate(files, start=1):
        dst = out_dir / name_fmt.format(i=i)
        src.replace(dst)
        out.append(dst)
        if on_progress:
            on_progress(i, len(files))
    shutil.rmtree(raw, ignore_errors=True)
    return out


def render(pptx_path, out_dir, **kw) -> list[Path]:
    """렌더러 진입점. 향후 LibreOffice 폴백을 여기서 분기한다."""
    return render_powerpoint(pptx_path, out_dir, **kw)


if __name__ == "__main__":
    import sys, time
    t0 = time.time()
    pngs = render(sys.argv[1], sys.argv[2])
    from PIL import Image
    w, h = Image.open(pngs[0]).size
    print(f"PNG {len(pngs)}장  {w}x{h}  {time.time()-t0:.1f}s  -> {pngs[0].parent}")
