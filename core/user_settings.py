"""GUI-managed settings persisted to data/user_settings.json.

교수설계 가이드 에이전트는 사내 Ubion LiteLLM 프록시(OpenAI 호환)를 사용한다.
URL·API 키·모델은 코드에 하드코딩하지 않고 사용자가 설정 화면(사이드바) 또는
환경변수(.env)로 입력한다. 저장 파일과 .env 는 GitHub 에 올리지 않는다(.gitignore).

패턴 차용: 260527-textmarketingLM/core/user_settings.py
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "data" / "user_settings.json"

DEFAULT_BASE_URL = "http://192.168.50.119:4000"
DEFAULT_MODEL = "deepseek-v4-flash-think"

# 선택 가능한 모델 (id -> 표시 라벨). MIGRATION.md 매핑 기준.
MODELS: dict[str, str] = {
    "claude-sonnet-4-6": "Claude Sonnet 4.6 (권장 · 균형)",
    "claude-opus-4-7": "Claude Opus 4.7 (고품질)",
    "claude-haiku-4-5": "Claude Haiku 4.5 (빠름 · 경제적)",
    "deepseek-v4-flash": "DeepSeek V4 Flash (빠름 · 저비용)",
    "deepseek-v4-flash-think": "DeepSeek V4 Flash Think (추론)",
    "deepseek-v4-pro": "DeepSeek V4 Pro (고품질)",
}


@dataclass
class Settings:
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    model: str = DEFAULT_MODEL
    # 강의계획서/원고는 표가 많은 장문이라 넉넉히
    max_tokens: int = 10000
    temperature: float = 0.7
    # 디자인 슬라이드 사진용(선택). 있으면 Unsplash, 없으면 Openverse.
    unsplash_key: str = ""


def _env_defaults() -> Settings:
    s = Settings()
    s.base_url = os.environ.get("UBION_LITELLM_URL", s.base_url)
    s.api_key = os.environ.get("UBION_LITELLM_KEY", s.api_key)
    s.model = os.environ.get("UBION_LITELLM_MODEL", s.model)
    s.unsplash_key = os.environ.get("UNSPLASH_ACCESS_KEY", s.unsplash_key)
    return s


def load() -> Settings:
    base = _env_defaults()
    if not SETTINGS_PATH.exists():
        return base
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return base
    merged = asdict(base)
    merged.update({k: v for k, v in data.items() if k in merged})
    if merged.get("model") not in MODELS:
        merged["model"] = DEFAULT_MODEL
    return Settings(**merged)


def save(settings: Settings) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
