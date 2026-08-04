"""Find a usable Korean font on Windows."""
from __future__ import annotations

import os
from pathlib import Path

# Order = preference. First hit wins.
PREFERRED_FONTS = [
    ("Pretendard", ["Pretendard-Bold.ttf", "Pretendard-SemiBold.ttf", "Pretendard-Regular.ttf",
                    "Pretendard-Bold.otf", "Pretendard-Regular.otf"]),
    ("NanumGothic", ["NanumGothicBold.ttf", "NanumGothic.ttf",
                     "NanumGothicBold.otf", "NanumGothic.otf"]),
    ("Malgun Gothic", ["malgunbd.ttf", "malgun.ttf"]),
]


def _font_dirs() -> list[Path]:
    dirs: list[Path] = []
    windir = os.environ.get("WINDIR") or "C:\\Windows"
    dirs.append(Path(windir) / "Fonts")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Microsoft" / "Windows" / "Fonts")
    return [d for d in dirs if d.is_dir()]


def find_font() -> tuple[str, Path | None]:
    """Return (font_name_for_libass, ttf_path_or_None).

    libass on Windows resolves font by FontName via fontconfig + system fonts.
    We return a font name that libass can find. The .ttf path is informational
    (used for the --probe report).
    """
    dirs = _font_dirs()
    for name, files in PREFERRED_FONTS:
        for d in dirs:
            for fn in files:
                p = d / fn
                if p.is_file():
                    return name, p
    # Fallback: assume Malgun Gothic is available (default on Windows 10+).
    return "Malgun Gothic", None


def probe() -> str:
    name, path = find_font()
    if path:
        return f"font: {name}  ({path})"
    return f"font: {name}  (system default, file not auto-located)"
