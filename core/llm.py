"""LLM provider — **Claude Code CLI (OAuth 구독)** 기본, LiteLLM 프록시 선택.

기본을 CLI 로 둔 이유:
  - API 키를 새로 발급·과금하지 않는다. 이 PC 의 Claude Code 로그인(OAuth)을 그대로 쓴다.
  - 대본·개요 같은 긴 한국어 생성에서 품질이 가장 안정적이었다.
  - 프록시 주소·키 설정이 필요 없어 로컬 콘솔의 전제(로컬 완결)와 맞는다.

두 프로바이더가 같은 표면을 갖는다: `generate` / `stream` / `ping` / 쓰기 가능한 `.model`.
호출부(server.py)가 `p.model = ...` 로 모델을 바꾸므로 **model 은 반드시 대입 가능한 필드**여야 한다.

스트리밍: CLI 는 `--output-format stream-json` 으로 증분 텍스트를 준다. 화면의 타이핑 효과가
이걸로 유지된다. 실패하면 한 덩어리로 떨어뜨려도 기능은 동일하다(UX 만 덜 부드럽다).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List

from core.user_settings import Settings

# CLI 한 번 호출의 상한. 대본 한 배치가 4,000자를 넘을 수 있어 넉넉히 둔다.
CLI_TIMEOUT = 1800


# ── Claude Code CLI 찾기 ──────────────────────────────────────────────────
def find_cli() -> Path | None:
    """claude 실행 파일. VSCode 확장에 번들된 것까지 찾는다(설치본이 없는 PC 대비)."""
    if (env := (os.environ.get("CLAUDE_CLI") or "").strip().strip('"')):
        p = Path(env).expanduser()
        if p.exists():
            return p
    if (w := shutil.which("claude")):
        return Path(w)
    pats = ["anthropic.claude-code-*/resources/native-binary/claude.exe",
            "anthropic.claude-code-*/resources/native-binary/claude"]
    for pat in pats:
        hits = sorted((Path.home() / ".vscode" / "extensions").glob(pat))
        if hits:
            return hits[-1]           # 확장 버전이 올라가면 경로가 바뀐다 → 최신 선택
    return None


def cli_available() -> bool:
    return find_cli() is not None


@dataclass
class ClaudeCliProvider:
    """`claude -p` 를 서브프로세스로 부른다. API 키 없이 구독 인증으로 나간다."""

    model: str = ""                       # 빈 값 = CLI 기본 모델
    exe: Path | None = field(default=None)

    def __post_init__(self):
        if self.exe is None:
            self.exe = find_cli()

    # ── 내부 ──
    def _base_args(self, system: str, user: str) -> list[str]:
        if self.exe is None:
            raise RuntimeError(
                "Claude Code CLI 를 찾지 못했습니다. Claude Code 로그인 후 다시 시도하거나 "
                "CLAUDE_CLI 환경변수로 실행 파일 경로를 지정하세요.")
        args = [str(self.exe), "-p", user]
        if system:
            args += ["--append-system-prompt", system]
        if (m := (self.model or "").strip()) and m not in ("cli-default", "default"):
            args += ["--model", m]
        return args

    @staticmethod
    def _flatten(messages: List[Dict]) -> str:
        """CLI 는 대화 배열을 받지 않는다 — 역할을 표시해 한 프롬프트로 접는다."""
        if len(messages) == 1 and messages[0].get("role") == "user":
            return str(messages[0].get("content") or "")
        parts = []
        for m in messages:
            role = {"user": "사용자", "assistant": "어시스턴트",
                    "system": "지시"}.get(m.get("role", "user"), m.get("role", "user"))
            parts.append(f"[{role}]\n{m.get('content') or ''}")
        return "\n\n".join(parts)

    def _run(self, args: list[str], *, timeout: int) -> subprocess.CompletedProcess:
        return subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
            creationflags=(subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0))

    # ── 표면 ──
    def generate(self, system: str, messages: List[Dict], *, max_tokens: int = 10000,
                 temperature: float = 0.7) -> str:
        """max_tokens·temperature 는 CLI 가 받지 않으므로 무시한다(시그니처 호환용)."""
        args = self._base_args(system, self._flatten(messages)) + ["--output-format", "json"]
        proc = self._run(args, timeout=CLI_TIMEOUT)
        if proc.returncode != 0:
            raise RuntimeError(f"claude CLI 실패 (exit={proc.returncode}): "
                               f"{(proc.stderr or proc.stdout or '')[-500:]}")
        try:
            return json.loads(proc.stdout).get("result", "") or ""
        except json.JSONDecodeError:
            return proc.stdout or ""

    def stream(self, system: str, messages: List[Dict], *, max_tokens: int = 10000,
               temperature: float = 0.7) -> Iterator[str]:
        """증분 텍스트를 흘린다. stream-json 이 안 되면 generate 로 한 번에 떨어뜨린다."""
        args = self._base_args(system, self._flatten(messages)) + [
            "--output-format", "stream-json", "--include-partial-messages", "--verbose"]
        try:
            proc = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=(subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0))
        except OSError as e:
            raise RuntimeError(f"claude CLI 실행 실패: {e}") from e

        # ★ stream-json 은 증분(content_block_delta)과 **완성된 전문**(assistant /
        #   result)을 모두 보낸다. 둘 다 흘리면 문서가 두 번 나온다 —
        #   실제로 강의계획서가 통째로 두 번 찍혔다. 그래서 델타를 우선하고,
        #   전문은 델타가 하나도 없었을 때만 폴백으로 쓴다.
        got_delta = False
        whole = ""          # assistant/result 의 전문 — 폴백용
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind, text = _classify(ev)
            if kind == "delta":
                got_delta = True
                yield text
            elif kind == "whole" and len(text) > len(whole):
                whole = text
        code = proc.wait()
        if code != 0 and not got_delta and not whole:
            err = (proc.stderr.read() if proc.stderr else "")[-400:]
            raise RuntimeError(f"claude CLI 실패 (exit={code}): {err}")
        if not got_delta:
            # 형식이 바뀐 경우의 안전망. generate() 를 다시 부르면 같은 요청을
            # 두 번 보내게 되므로, 이미 받은 전문을 쓴다.
            if whole:
                yield whole
            else:
                yield self.generate(system, messages)

    def ping(self) -> tuple[bool, str]:
        if self.exe is None:
            return False, "Claude Code CLI 를 찾지 못했습니다 (CLAUDE_CLI 로 지정 가능)."
        try:
            ver = self._run([str(self.exe), "--version"], timeout=30)
            tag = (ver.stdout or "").strip().splitlines()[0] if ver.returncode == 0 else "?"
            text = self.generate("You are a connection tester.",
                                 [{"role": "user", "content": "Respond with exactly: OK"}])
            return True, f"OK ({tag}) → {text.strip()[:40]}"
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"


# ── LiteLLM 프록시 (선택) ─────────────────────────────────────────────────
@dataclass
class UbionLiteLLMProvider:
    """사내 LiteLLM 프록시(OpenAI 호환). CLI 를 못 쓰는 환경용으로 남겨 둔다.

    `claude-opus-4-x` 는 temperature 를 받지 않는다(마이그레이션 함정 #8).
    """

    base_url: str
    api_key: str
    model: str

    def _client(self):
        from openai import OpenAI  # lazy import
        url = (self.base_url or "").rstrip("/")
        return OpenAI(api_key=self.api_key, base_url=f"{url}/v1")

    def _params(self, messages, *, max_tokens, temperature, stream):
        params = dict(model=self.model, messages=messages, max_tokens=max_tokens, stream=stream)
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
            **self._params(msgs, max_tokens=max_tokens, temperature=temperature, stream=True))
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
            **self._params(msgs, max_tokens=max_tokens, temperature=temperature, stream=False))
        return resp.choices[0].message.content or ""

    def ping(self) -> tuple[bool, str]:
        try:
            text = self.generate(
                "You are a connection tester.",
                [{"role": "user", "content": "Respond with exactly: OK"}],
                max_tokens=8, temperature=0)
            return True, f"OK ({self.model}) → {text.strip()[:40]}"
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {e}"


def _classify(ev: dict) -> tuple[str, str]:
    """stream-json 이벤트 → ("delta"|"whole"|"", 텍스트).

    **delta 와 whole 을 반드시 구분해야 한다.** 같은 내용이 증분으로도 오고
    완성본으로도 오기 때문에, 둘을 섞어 흘리면 출력이 두 배가 된다.
    """
    t = ev.get("type")
    if t == "stream_event":                       # 래핑된 형태
        ev = ev.get("event") or {}
        t = ev.get("type")

    if t == "content_block_delta":
        return "delta", str((ev.get("delta") or {}).get("text") or "")

    if t == "assistant":                          # 완성된 메시지 (전문)
        parts = [b.get("text") or "" for b in ((ev.get("message") or {}).get("content") or [])
                 if isinstance(b, dict) and b.get("type") == "text"]
        return "whole", "".join(parts)

    if t == "result":                             # 최종 결과 (전문)
        return "whole", str(ev.get("result") or "")

    return "", ""


def build_provider(settings: Settings):
    """설정의 provider 값으로 갈라진다. 기본은 CLI."""
    if (settings.provider or "cli").strip().lower() == "litellm":
        return UbionLiteLLMProvider(
            base_url=settings.base_url, api_key=settings.api_key, model=settings.model)
    return ClaudeCliProvider(model=settings.model)
