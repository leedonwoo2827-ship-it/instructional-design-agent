# -*- coding: utf-8 -*-
"""슬라이드플랜 → chodangi 번들 디렉터리.

이 프로젝트의 핵심이자 사실상 유일한 신규 로직. 엔진(voicewright/mp4maker)과의 계약은
API가 아니라 **디렉터리 모양**이므로, 여기서 그 모양을 정확히 만들어 준다.

    <workspace>/ch01/
      script/ch01_script.json      ← voicewright + mp4maker가 함께 읽음
      images/ch01_01_slide.png     ← chNN_XX_*.png (1-based, 2자리)
      audio/     …                 ← voicewright가 채움
      subtitles/ …                 ← voicewright가 채움
      draft/ch01_final.mp4         ← mp4maker 출력

제약(엔진에서 승계):
  - 번들 폴더명에 숫자가 있어야 한다 (`CHAPTER_RE = r"ch(\\d{1,3})"`)
  - 씬 번호는 1-based, 파일명은 `ch{chap:02d}_{scene:02d}_*`
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from . import config
from .ingest.pptx_in import Slide

SUBDIRS = ("script", "images", "audio", "subtitles", "draft")


def chapter_dir(chapter: int, workspace: Path | None = None) -> Path:
    ws = workspace or config.WORKSPACE
    return ws / f"ch{chapter:02d}"


def scene_stem(chapter: int, scene: int) -> str:
    return f"ch{chapter:02d}_{scene:02d}"


def build(
    slides: list[Slide],
    narrations: list[str],
    png_paths: list[Path],
    *,
    chapter: int = 1,
    title: str = "",
    voice: str | None = None,
    speed: float | None = None,
    workspace: Path | None = None,
    clean: bool = True,
) -> Path:
    """번들 디렉터리를 만들고 경로를 돌려준다.

    slides / narrations / png_paths 는 같은 길이여야 한다.
    내레이션이 빈 슬라이드는 `silent` 씬으로 넣어 3초 정지 화면이 된다.
    """
    n = len(slides)
    if not (len(narrations) == len(png_paths) == n):
        raise ValueError(
            f"길이 불일치: slides={n} narrations={len(narrations)} pngs={len(png_paths)}")
    if n == 0:
        raise ValueError("슬라이드가 없습니다")

    bundle = chapter_dir(chapter, workspace)
    if clean and bundle.exists():
        shutil.rmtree(bundle)
    for d in SUBDIRS:
        (bundle / d).mkdir(parents=True, exist_ok=True)

    scenes = []
    for i, (sl, narr, png) in enumerate(zip(slides, narrations, png_paths), start=1):
        stem = scene_stem(chapter, i)
        img_name = f"{stem}_slide.png"
        shutil.copyfile(png, bundle / "images" / img_name)

        narr = (narr or "").strip()
        scene: dict = {
            "scene": i,
            "title": sl.title,
            "image_filename": img_name,
            "narration_text": narr,
        }
        if not narr:
            # TTS를 건너뛰고 무음 wav를 만든다 → 슬라이드가 잠깐 보였다 넘어감
            scene["silent"] = True
            scene["narration_seconds"] = 3
        scenes.append(scene)

    doc = {
        "version": "1.0",
        "kind": "lesson",
        "chapter": chapter,
        "title": title or (slides[0].title if slides else f"ch{chapter:02d}"),
        "aspect_ratio": "16:9",
        "voice": voice or config.DEFAULT_VOICE,
        "speed": speed if speed is not None else config.DEFAULT_SPEED,
        "scenes": scenes,
    }
    script_path = bundle / "script" / f"ch{chapter:02d}_script.json"
    script_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return bundle


def read_script(bundle: Path) -> dict:
    hits = list((bundle / "script").glob("*_script.json"))
    if not hits:
        raise FileNotFoundError(f"script JSON 없음: {bundle}")
    return json.loads(hits[0].read_text(encoding="utf-8"))


def update_narration(bundle: Path, scene: int, narration: str) -> dict:
    """한 씬의 대본만 수정한다 (에디터에서 부분 재합성할 때 사용)."""
    hits = list((bundle / "script").glob("*_script.json"))
    if not hits:
        raise FileNotFoundError(f"script JSON 없음: {bundle}")
    path = hits[0]
    doc = json.loads(path.read_text(encoding="utf-8"))
    for sc in doc["scenes"]:
        if int(sc.get("scene", 0)) == scene:
            text = (narration or "").strip()
            sc["narration_text"] = text
            if text:
                sc.pop("silent", None)
                sc.pop("narration_seconds", None)
            else:
                sc["silent"] = True
                sc["narration_seconds"] = 3
            break
    else:
        raise KeyError(f"씬 {scene} 없음")
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc


def output_mp4(bundle: Path) -> Path | None:
    hits = sorted((bundle / "draft").glob("*_final.mp4"))
    return hits[0] if hits else None
