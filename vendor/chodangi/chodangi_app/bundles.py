"""번들(munje/chNN) 탐색·생성·상태 점검 헬퍼.

출력 루트는 루트의 munje/ 폴더(환경변수 MF_OUTPUT_DIR 로 변경). 그 안에 챕터 폴더:
    munje/chNN/
      script/     chNN_script.json
      images/     chNN_XX_*.{png,jpg,jpeg,webp}
      audio/      chNN_XX_narration.{wav,mp3,m4a,flac}
      subtitles/  chNN_XX_narration.srt  (+ chNN.srt)
      draft/      chNN_final.mp4
(예전 chNN_bundle 폴더명도 하위 호환으로 인식)
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

MEDIAFORGE_ROOT = Path(__file__).resolve().parents[1]
# 출력 루트(루트에 보이는 폴더, 기본 munje/) — 그 안에 ch01, ch02… 번들 폴더.
# 환경변수 MF_OUTPUT_DIR 로 변경 가능.
ASSETS_DIR = Path(os.environ.get("MF_OUTPUT_DIR") or (MEDIAFORGE_ROOT / "munje"))

IMG_EXTS = (".png", ".jpg", ".jpeg", ".webp")
AUD_EXTS = (".wav", ".mp3", ".m4a", ".flac")
SUBDIRS = ("script", "images", "audio", "subtitles", "draft")


def _chap(bundle_dir: Path) -> str | None:
    m = re.search(r"(\d{1,3})", bundle_dir.name.replace("_bundle", ""))
    return f"{int(m.group(1)):02d}" if m else None


def list_bundles() -> list[str]:
    if not ASSETS_DIR.is_dir():
        return []
    # 번들 = 출력 루트 안의 폴더(ch01…). '_' 로 시작하는 폴더(_book 등)는 제외.
    # 하위 호환: 예전 'chNN_bundle' 폴더도 그대로 인식.
    return sorted(p.name for p in ASSETS_DIR.iterdir()
                  if p.is_dir() and not p.name.startswith("_"))


def bundle_path(name: str) -> Path:
    if "/" in name or "\\" in name or name.startswith("."):
        raise ValueError(f"잘못된 번들 이름: {name}")
    return ASSETS_DIR / name


def create_bundle(name: str) -> Path:
    """번들 폴더 + 하위 폴더 골격 생성. 이름은 chNN 형태 권장(munje/ch01 …).

    '_bundle' 접미사를 붙여 오면 떼어내 chNN 으로 만든다(하위 호환).
    """
    name = name.strip()
    if name.endswith("_bundle"):
        name = name[: -len("_bundle")]
    root = bundle_path(name)
    for sub in SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


def find_script(bundle_dir: Path) -> Path | None:
    hits = sorted((bundle_dir / "script").glob("*_script.json"))
    return hits[0] if hits else None


def _newest_mtime(dirs: list[Path]) -> float:
    """주어진 폴더들 안 파일의 가장 최근 수정시각. 없으면 0."""
    newest = 0.0
    for d in dirs:
        if not d.is_dir():
            continue
        for p in d.iterdir():
            if p.is_file():
                try:
                    newest = max(newest, p.stat().st_mtime)
                except OSError:
                    pass
    return newest


def _audio_duration(path: Path | None) -> float | None:
    """오디오 길이(초). 실패하면 None. (cue 자동 채우기용)"""
    if path is None or not path.exists():
        return None
    try:
        import soundfile as sf
        info = sf.info(str(path))
        return round(info.frames / float(info.samplerate), 3)
    except Exception:
        return None


def _find_prefix_file(folder: Path, chap: str, scene: int, exts: tuple[str, ...],
                      suffix: str = "") -> Path | None:
    """이 씬에 해당하는 파일 하나를 찾아 경로를 반환 (없으면 None)."""
    if not folder.is_dir():
        return None
    for pref in (f"ch{chap}_{scene:02d}", f"{int(chap)}_{scene:02d}"):
        for ext in exts:
            if suffix:
                exact = folder / f"{pref}{suffix}{ext}"
                if exact.exists():
                    return exact
            hits = sorted(folder.glob(f"{pref}*{ext}"))
            if hits:
                return hits[0]
    return None


def bundle_status(name: str) -> dict:
    """번들 한 개의 단계별 상태를 요약한다 (UI 진행 표시줄·검증용)."""
    root = bundle_path(name)
    chap = _chap(root)
    script_path = find_script(root) if root.is_dir() else None

    scenes_out: list[dict] = []
    title = ""
    if script_path and script_path.exists():
        try:
            data = json.loads(script_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"bundle": name, "ok": False, "error": f"대본 JSON 파싱 실패: {exc}"}
        title = data.get("title") or ""
        for pos, sc in enumerate(data.get("scenes") or []):
            idx = int(sc.get("scene") or sc.get("scene_number") or pos + 1)
            img = _find_prefix_file(root / "images", chap, idx, IMG_EXTS)
            aud = _find_prefix_file(root / "audio", chap, idx, AUD_EXTS, "_narration")
            sub = _find_prefix_file(root / "subtitles", chap, idx, (".srt",), "_narration")
            scenes_out.append({
                "scene": idx,
                "title": sc.get("title") or "",
                "narration_text": sc.get("narration_text") or "",
                "srt_text": sc.get("srt_text"),
                "narration_seconds": sc.get("narration_seconds"),
                "has_image": img is not None,
                "has_audio": aud is not None,
                "has_subtitle": sub is not None,
                "image_file": img.name if img else None,
                "audio_file": aud.name if aud else None,
                "subtitle_file": sub.name if sub else None,
                "audio_duration": _audio_duration(aud),
            })

    draft_mp4 = root / "draft" / f"ch{chap}_final.mp4" if chap else None
    # 렌더 결과가 입력(음성/자막/이미지/대본)보다 오래됐으면 stale → 다시 렌더 필요
    render_stale = False
    if draft_mp4 and draft_mp4.exists():
        try:
            mp4_mtime = draft_mp4.stat().st_mtime
            newest_input = _newest_mtime([root / "audio", root / "subtitles",
                                          root / "images", root / "script"])
            render_stale = newest_input > mp4_mtime + 0.5
        except OSError:
            render_stale = False
    n = len(scenes_out)
    img_done = sum(1 for s in scenes_out if s["has_image"])
    aud_done = sum(1 for s in scenes_out if s["has_audio"])
    return {
        "bundle": name,
        "ok": True,
        "path": str(root.resolve()),
        "script_dir": str((root / "script").resolve()),
        "images_dir": str((root / "images").resolve()),
        "chapter": chap,
        "title": title,
        "has_script": bool(script_path),
        "scene_count": n,
        "scenes": scenes_out,
        "missing_images": [s["scene"] for s in scenes_out if not s["has_image"]],
        "missing_audio": [s["scene"] for s in scenes_out if not s["has_audio"]],
        "steps": {
            "script": bool(script_path),
            "images": n > 0 and img_done == n,
            "audio": n > 0 and aud_done == n,
            "render": bool(draft_mp4 and draft_mp4.exists()) and not render_stale,
        },
        "final_mp4": str(draft_mp4) if (draft_mp4 and draft_mp4.exists()) else None,
        "render_stale": render_stale,
    }
