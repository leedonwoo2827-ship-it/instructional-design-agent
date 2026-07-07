"""LLM provider — Ubion LiteLLM proxy (OpenAI 호환 chat.completions).

base_url 에 `/v1` 를 자동으로 붙인다. 스트리밍/논스트리밍 모두 지원.
`claude-opus-4-7` 은 temperature 파라미터를 받지 않으므로(마이그레이션 함정 #8)
opus 계열에는 temperature 를 보내지 않는다.

패턴 차용: 260527-textmarketingLM/core/llm.py
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, List, Dict

from core.user_settings import Settings


@dataclass
class UbionLiteLLMProvider:
    base_url: str
    api_key: str
    model: str

    def _client(self):
        from openai import OpenAI  # lazy import
        url = (self.base_url or "").rstrip("/")
        return OpenAI(api_key=self.api_key, base_url=f"{url}/v1")

    def _params(self, messages, *, max_tokens, temperature, stream):
        params = dict(model=self.model, messages=messages, max_tokens=max_tokens, stream=stream)
        # opus 4.x 는 temperature 미지원 → 명시 제거
        if not self.model.startswith("claude-opus"):
            params["temperature"] = temperature
        return params

    @staticmethod
    def _with_system(system: str, messages: List[Dict]) -> List[Dict]:
        return ([{"role": "system", "content": system}] + messages) if system else list(messages)

    def stream(self, system: str, messages: List[Dict], *, max_tokens: int = 10000,
               temperature: float = 0.7) -> Iterator[str]:
        msgs = self._with_system(system, messages)
        resp = self._client().chat.completions.create(
            **self._params(msgs, max_tokens=max_tokens, temperature=temperature, stream=True)
        )
        for chunk in resp:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content

    def generate(self, system: str, messages: List[Dict], *, max_tokens: int = 10000,
                 temperature: float = 0.7) -> str:
        msgs = self._with_system(system, messages)
        resp = self._client().chat.completions.create(
            **self._params(msgs, max_tokens=max_tokens, temperature=temperature, stream=False)
        )
        return resp.choices[0].message.content or ""

    def ping(self) -> tuple[bool, str]:
        try:
            text = self.generate(
                "You are a connection tester.",
                [{"role": "user", "content": "Respond with exactly: OK"}],
                max_tokens=8, temperature=0,
            )
            return True, f"OK ({self.model}) → {text.strip()[:40]}"
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"


def build_provider(settings: Settings) -> UbionLiteLLMProvider:
    return UbionLiteLLMProvider(
        base_url=settings.base_url,
        api_key=settings.api_key,
        model=settings.model,
    )
