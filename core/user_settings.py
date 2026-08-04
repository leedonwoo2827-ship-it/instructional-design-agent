"""GUI-managed settings persisted to data/user_settings.json.

**기본 LLM 은 이 PC 의 Claude Code CLI(OAuth 구독)** 다. API 키를 새로 발급·과금하지 않고,
설정할 것도 없다(로그인만 되어 있으면 된다). 사내 Ubion LiteLLM 프록시는 CLI 를 못 쓰는
환경용으로 남겨 두고 `provider` 로 고른다.

저장 파일과 .env 는 GitHub 에 올리지 않는다(.gitignore).

패턴 차용: 260527-textmarketingLM/core/user_settings.py
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parent.parent / "data" / "user_settings.json"

DEFAULT_PROVIDER = "cli"                  # cli | litellm
DEFAULT_BASE_URL = "http://192.168.50.119:4000"
DEFAULT_MODEL = "cli-default"

PROVIDERS: dict[str, str] = {
    "cli": "Claude Code CLI (구독 인증 · 키 불필요)",
    "litellm": "Ubion LiteLLM 프록시 (OpenAI 호환)",
}

# CLI 모델 — 빈 값에 가까운 'cli-default' 는 CLI 가 알아서 고르게 둔다는 뜻이다.
CLI_MODELS: dict[str, str] = {
    "cli-default": "CLI 기본 모델 (권장)",
    "opus": "Opus (고품질 · 느림)",
    "sonnet": "Sonnet (균형)",
    "haiku": "Haiku (빠름)",
}

# LiteLLM 프록시 모델 (id -> 표시 라벨). MIGRATION.md 매핑 기준.
PROXY_MODELS: dict[str, str] = {
    "claude-sonnet-4-6": "Claude Sonnet 4.6 (권장 · 균형)",
    "claude-opus-4-7": "Claude Opus 4.7 (고품질)",
    "claude-haiku-4-5": "Claude Haiku 4.5 (빠름 · 경제적)",
    "deepseek-v4-flash": "DeepSeek V4 Flash (빠름 · 저비용)",
    "deepseek-v4-flash-think": "DeepSeek V4 Flash Think (추론)",
    "deepseek-v4-pro": "DeepSeek V4 Pro (고품질)",
}

# 화면의 <select> 와 PUT 검증이 함께 쓴다. 두 프로바이더의 모델을 모두 허용해야
# 프로바이더를 바꿀 때 모델이 기본값으로 되돌아가지 않는다.
MODELS: dict[str, str] = {**CLI_MODELS, **PROXY_MODELS}


def models_for(provider: str) -> dict[str, str]:
    return PROXY_MODELS if (provider or "").lower() == "litellm" else CLI_MODELS


def default_model_for(provider: str) -> str:
    return "claude-sonnet-4-6" if (provider or "").lower() == "litellm" else "cli-default"


@dataclass
class Settings:
    provider: str = DEFAULT_PROVIDER
    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    model: str = DEFAULT_MODEL
    # 강의계획서/원고는 표가 많은 장문이라 넉넉히 (CLI 는 이 값을 쓰지 않는다)
    max_tokens: int = 10000
    temperature: float = 0.7
    # 디자인 슬라이드 사진용(선택). 있으면 Unsplash, 없으면 Openverse.
    unsplash_key: str = ""

    @property
    def needs_key(self) -> bool:
        """연결 설정 화면이 'API 키' 를 물어야 하는가 — CLI 는 필요 없다."""
        return (self.provider or "").lower() == "litellm"


def _env_defaults() -> Settings:
    s = Settings()
    s.provider = os.environ.get("IDA_PROVIDER", s.provider)
    s.base_url = os.environ.get("UBION_LITELLM_URL", s.base_url)
    s.api_key = os.environ.get("UBION_LITELLM_KEY", s.api_key)
    s.model = os.environ.get("IDA_MODEL", os.environ.get("UBION_LITELLM_MODEL", s.model))
    s.unsplash_key = os.environ.get("UNSPLASH_ACCESS_KEY", s.unsplash_key)
    return s


def load() -> Settings:
    base = _env_defaults()
    data: dict = {}
    if SETTINGS_PATH.exists():
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
    merged = asdict(base)
    merged.update({k: v for k, v in data.items() if k in merged})
    # ★ 검증은 설정 파일이 없을 때도 돌아야 한다. .env 의 UBION_LITELLM_MODEL 이
    #   프로바이더와 무관하게 들어오므로, 파일이 없으면 CLI 인데 프록시 모델 id 가
    #   남는다(그 상태로 --model 을 넘기면 CLI 가 모르는 모델이라 실패한다).
    if merged.get("provider") not in PROVIDERS:
        merged["provider"] = DEFAULT_PROVIDER
    # 모델은 **고른 프로바이더의 목록** 안에서만 유효하다. 프로바이더를 바꾸면
    # 이전 프로바이더의 모델 id 가 남아 있을 수 있어 그때 기본값으로 되돌린다.
    if merged.get("model") not in models_for(merged["provider"]):
        merged["model"] = default_model_for(merged["provider"])
    return Settings(**merged)


def save(settings: Settings) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
