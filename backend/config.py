# -*- coding: utf-8 -*-
"""프로젝트 경로·상수 — 엔진을 import 하기 전에 반드시 먼저 import 할 것.

chodangi 엔진(voicewright/mp4maker)은 VOICEWRIGHT_* 환경변수로 asset 위치를 찾는다.
그 기본값(`settings._project_root()` = `parents[2]`)은 이 레이아웃에서 vendor/ 를
가리켜 빗나가므로, 여기서 **프로젝트 상대경로로 계산해 명시 주입**한다.
절대경로 하드코딩은 하지 않는다 — 폴더째 옮겨도 동작해야 한다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ── 경로 ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = PROJECT_ROOT / "vendor" / "chodangi"
ASSETS_DIR = ENGINE_DIR / "assets"
CONFIG_DIR = ENGINE_DIR / "config"
# 콘솔의 산출물 트리(core/workspace.py 의 기본값)와 이름이 겹치면 헷갈린다.
WORKSPACE = PROJECT_ROOT / ".engine-work"
UPLOADS = PROJECT_ROOT / "uploads"

# ── 엔진 상수 ─────────────────────────────────────────────────────────────
# ★ 씬 경계 crossfade — TTS(자막 타임코드)와 mp4maker(실제 합성)가 **같은 값**을
#   써야 한다. 어긋나면 자막이 씬마다 누적으로 밀린다(원본에서 83.6초 밀린 이력).
#   반드시 이 상수 하나만 참조할 것.
CROSSFADE_SEC = 0.6

SLIDE_W, SLIDE_H = 1920, 1080

# 기본 음성 — Supertonic 3 고정. 보이스 코드는 M1~M5 / F1~F5.
DEFAULT_VOICE = os.environ.get("PPT2VID_VOICE", "F2")
DEFAULT_SPEED = float(os.environ.get("PPT2VID_SPEED", "1.02"))
DEFAULT_TOTAL_STEP = int(os.environ.get("PPT2VID_TOTAL_STEP", "8"))


def bootstrap() -> None:
    """엔진 import 가능 상태로 만든다. 여러 번 불러도 안전."""
    if str(ENGINE_DIR) not in sys.path:
        sys.path.insert(0, str(ENGINE_DIR))

    os.environ.setdefault("VOICEWRIGHT_ASSETS_DIR", str(ASSETS_DIR))
    os.environ.setdefault("VOICEWRIGHT_VOICE_MAP", str(CONFIG_DIR / "voice_map.yaml"))
    os.environ.setdefault("VOICEWRIGHT_PRONUNCIATION_MAP",
                          str(CONFIG_DIR / "pronunciation_map.yaml"))
    os.environ.setdefault("VOICEWRIGHT_WORKSPACE", str(WORKSPACE))
    os.environ.setdefault("VOICEWRIGHT_DEFAULT_SPEED", str(DEFAULT_SPEED))
    os.environ.setdefault("VOICEWRIGHT_TOTAL_STEP", str(DEFAULT_TOTAL_STEP))
    # 로그가 전부 한국어라 서브프로세스 인코딩을 못박아야 한다.
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    WORKSPACE.mkdir(parents=True, exist_ok=True)
    UPLOADS.mkdir(parents=True, exist_ok=True)


def engine_env() -> dict:
    """mp4maker 서브프로세스에 넘길 환경변수."""
    bootstrap()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ENGINE_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return env


bootstrap()
