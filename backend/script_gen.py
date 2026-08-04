# -*- coding: utf-8 -*-
"""슬라이드플랜 → 강의 내레이션 대본 (목표 시간 기반 분량 제어).

영상 길이 = 슬라이드 학습 시간이다. 1강 100분 / 40장이면 **슬라이드당 2.5분**,
실측 발화속도 7.7자/초로 환산하면 **슬라이드당 약 1,100자**가 필요하다.

분량을 프롬프트에만 맡기면 반드시 짧게 나온다(초기 버전이 목표의 1/8이었다).
그래서 이 모듈은 **생성 → 측정 → 부족분만 보강 재요청**을 돈다.

백엔드:
  claude_cli  — VSCode 확장에 번들된 claude.exe headless. **OAuth 구독, API 키 불필요** (기본)
  litellm     — OpenAI 호환 프록시. 로컬 OAuth를 못 쓰는 환경용 (현재 미사용)
  passthrough — LLM 없이 슬라이드 텍스트 (스모크 테스트·폴백)

파이프라인은 절대 멈추지 않는다: 파싱 실패 → 재시도 → passthrough 폴백.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

# 실측값 (Supertonic F2, speed 1.02, 8씬 19분 영상): 8,576자 → 1,139.4초
CHARS_PER_SEC = 7.53

# 목표 여유. 생성이 목표를 정확히 맞추는 일은 없고 늘 조금 모자라므로 위로 잡는다.
# (여유 없이 돌린 8장/20분 실측이 -5.1% 였다. 넘치는 쪽은 ③ 검수에서 사람이 줄인다)
TARGET_HEADROOM = 1.08

BATCH = 4           # 슬라이드당 ~1,100자면 4장 배치가 출력 ~4,400자. 10장은 잘린다
MIN_RATIO = 0.85    # 목표의 85% 미만이면 보강 (0.70은 너무 느슨해 -5% 미달로 통과했다)
MAX_REFILL = 2      # 보강 재요청 최대 횟수

# 슬라이드 유형별 분량 가중치 — 섹션 전환 슬라이드에 2.5분을 말할 수는 없다
KIND_WEIGHT = {
    "section": 0.35,
    "cover": 0.30,
    "content": 1.0,
    "bullets": 1.0,
    "cards": 1.1,
    "compare": 1.1,
    "process": 1.1,
    "table": 1.1,
    "photo": 0.9,
}

_JSON_BLOCK = re.compile(r"\{.*\}", re.S)

SYSTEM = """\
너는 대학 강의 영상의 내레이션 대본을 쓰는 교수자다. 이 대본은 그대로 음성으로 합성된다.

가장 중요한 규칙 — 분량:
- 각 슬라이드에 **지정된 목표 글자수**를 반드시 채운다. 짧으면 영상 길이가 안 맞아 실패다.
- 목표를 채우려고 같은 말을 반복하거나 뻔한 문장으로 늘리지 마라. 대신 **내용을 실제로 펼친다**.

한 슬라이드를 이렇게 전개한다 (순서 고정 아님, 슬라이드 성격에 맞게):
1. 개념·용어를 정의한다
2. 왜 중요한지, 무엇을 해결하는지 말한다
3. 흔한 오해나 혼동되는 개념과 구분해 준다
4. 구체적인 예시를 든다 — 학교·기업·일상에서 하나
5. 다음 슬라이드로 자연스럽게 넘긴다

문체:
- 강의체 종결어미(~습니다 / ~합니다 / ~죠 / ~까요). 문어체 '~한다'는 쓰지 않는다.
- 화면에 이미 글자가 보이므로 **불릿을 그대로 읽지 말고 풀어서 설명**한다.
- 한 문장은 40~70자. 너무 긴 문장은 음성이 부자연스러워진다.
- 앞 슬라이드와 이어지는 연결어를 쓴다(자, 그럼, 이제, 다음으로, 정리하면, 반대로).
- 괄호·불릿기호·마크다운·이모지·따옴표를 쓰지 않는다. 소리 내어 읽을 수 있는 문장만.
- 숫자와 영문 약어는 그대로 둔다(ADDIE, SCORM). 발음 교정은 별도 단계에서 한다.

출력은 아래 JSON 하나만. 다른 말은 절대 붙이지 않는다.
{"slides":[{"index":<슬라이드번호>,"narration":"<대본>"}]}"""

REFILL_SYSTEM = """\
너는 대학 강의 내레이션 대본을 보강하는 교수자다.
주어진 대본이 목표 분량보다 짧다. **기존 내용을 유지하면서 더 펼쳐라.**

늘리는 방법 — 이것만 쓴다:
- 개념 정의를 더 정확하게 풀어 쓴다
- 왜 그런지 이유·배경을 덧붙인다
- 혼동되는 개념과 구분해 준다
- 구체적인 예시를 하나 더 든다
- 학습자가 놓치기 쉬운 지점을 짚어 준다

절대 하지 말 것: 같은 문장 반복, 의미 없는 접속사 늘리기, "중요합니다" 같은 빈 강조 남발.

문체는 원본과 동일하게 강의체(~습니다/~죠), 한 문장 40~70자, 기호·마크다운 없음.
출력은 아래 JSON 하나만.
{"slides":[{"index":<슬라이드번호>,"narration":"<보강된 전체 대본>"}]}"""


# ── 목표 분량 배분 ────────────────────────────────────────────────────────
def target_chars(slides, target_minutes: float,
                 chars_per_sec: float = CHARS_PER_SEC) -> dict[int, int]:
    """목표 시간을 슬라이드 유형 가중치로 나눠 슬라이드별 목표 글자수를 낸다."""
    total = target_minutes * 60.0 * chars_per_sec * TARGET_HEADROOM
    weights = {s.index: KIND_WEIGHT.get(s.kind, 1.0) for s in slides}
    wsum = sum(weights.values()) or 1.0
    return {i: max(120, int(total * w / wsum)) for i, w in weights.items()}


def est_seconds(text: str, chars_per_sec: float = CHARS_PER_SEC) -> float:
    return len(text or "") / chars_per_sec


# ── 프롬프트 ──────────────────────────────────────────────────────────────
_KIND_HINT = {
    "section": "섹션 전환 슬라이드다. 앞 내용을 한 문장으로 정리하고 다음 주제를 예고한다. 짧게.",
    "cover": "표지다. 인사와 오늘 주제 소개만 간단히.",
    "cards": "병렬 항목이 카드로 놓였다. 항목마다 정의와 예시를 붙여 설명한다.",
    "compare": "두 개를 대조하는 슬라이드다. 차이가 왜 생기는지까지 설명한다.",
    "process": "순서·단계 슬라이드다. 각 단계에서 무엇을 하고 무엇이 나오는지 말한다.",
    "table": "표다. 행마다 읽지 말고 표가 말하려는 패턴을 설명한다.",
    "bullets": "불릿 슬라이드다. 항목을 그대로 읽지 말고 각각을 풀어 설명한다.",
}


def _user_prompt(slides, targets: dict[int, int], prev_tail: str, meta: str) -> str:
    head = f"강의: {meta}\n" if meta else ""
    if prev_tail:
        head += f'직전 슬라이드의 마지막 문장: "{prev_tail}"\n'
    blocks = []
    for s in slides:
        hint = _KIND_HINT.get(s.kind, "")
        blocks.append(
            f"[슬라이드 {s.index}] 목표 {targets[s.index]}자\n"
            f"{hint}\n{s.text_for_prompt()}")
    return (f"{head}\n다음 슬라이드들의 내레이션을 작성하라. "
            f"각 슬라이드의 목표 글자수를 반드시 채워라.\n\n" + "\n\n".join(blocks))


def _refill_prompt(items, meta: str) -> str:
    """items: [(slide, current_narration, target)]"""
    head = f"강의: {meta}\n" if meta else ""
    blocks = []
    for s, cur, tgt in items:
        blocks.append(
            f"[슬라이드 {s.index}] 현재 {len(cur)}자 → 목표 {tgt}자 "
            f"(약 {tgt - len(cur)}자 더 필요)\n"
            f"슬라이드 내용:\n{s.text_for_prompt()}\n"
            f"현재 대본:\n{cur}")
    return f"{head}\n아래 대본들을 목표 분량까지 보강하라.\n\n" + "\n\n".join(blocks)


# ── 백엔드 ────────────────────────────────────────────────────────────────
def find_claude():
    """VSCode 확장에 번들된 claude.exe 중 최신 버전. OAuth 구독 인증을 그대로 쓴다."""
    from pathlib import Path
    if (env := os.environ.get("CLAUDE_CLI")) and Path(env).exists():
        return Path(env)
    import shutil
    if (w := shutil.which("claude")):
        return Path(w)
    hits = sorted((Path.home() / ".vscode" / "extensions").glob(
        "anthropic.claude-code-*/resources/native-binary/claude.exe"))
    return hits[-1] if hits else None


def _call_claude(system: str, user: str, timeout: int = 900) -> str:
    exe = find_claude()
    if exe is None:
        raise RuntimeError("claude CLI를 찾지 못했습니다. CLAUDE_CLI 환경변수로 지정하세요.")
    proc = subprocess.run(
        [str(exe), "-p", user, "--append-system-prompt", system, "--output-format", "json"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI 실패 (exit={proc.returncode}): {proc.stderr[-400:]}")
    try:
        return json.loads(proc.stdout).get("result", "")
    except json.JSONDecodeError:
        return proc.stdout


def _call_litellm(system: str, user: str, timeout: int = 900) -> str:
    import httpx
    base = os.environ.get("LITELLM_BASE_URL", "").rstrip("/")
    key = os.environ.get("LITELLM_API_KEY", "")
    model = os.environ.get("LITELLM_MODEL", "claude-sonnet-4-5")
    if not base or not key:
        raise RuntimeError("LITELLM_BASE_URL / LITELLM_API_KEY 가 필요합니다.")
    r = httpx.post(f"{base}/v1/chat/completions",
                   headers={"Authorization": f"Bearer {key}"},
                   json={"model": model, "temperature": 0.4, "max_tokens": 16000,
                         "messages": [{"role": "system", "content": system},
                                      {"role": "user", "content": user}]},
                   timeout=timeout)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


_BACKENDS = {"claude_cli": _call_claude, "litellm": _call_litellm}


# ── 공통 ──────────────────────────────────────────────────────────────────
def _fallback(s) -> str:
    parts = [s.spine] if s.spine else ([s.title + "."] if s.title else [])
    for b in s.bullets:
        parts.append(b if b.endswith((".", "!", "?")) else b + ".")
    for c in s.cards:
        lbl, desc = c.get("label", ""), c.get("desc", "")
        parts.append(f"{lbl}. {desc}" if desc else f"{lbl}.")
    return " ".join(parts).strip()


def _parse(raw: str) -> dict[int, str]:
    m = _JSON_BLOCK.search(raw or "")
    if not m:
        raise ValueError("응답에서 JSON을 찾지 못함")
    doc = json.loads(m.group(0))
    return {int(x["index"]): str(x.get("narration", "")).strip()
            for x in doc.get("slides", []) if x.get("index") is not None}


def _call_with_retry(call, system: str, user: str, label: str) -> dict[int, str]:
    for attempt in (1, 2):
        try:
            return _parse(call(system, user))
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                print(f"  [warn] {label} 실패 → 폴백: {e}", flush=True)
                return {}
            user += "\n\n반드시 지정된 JSON 형식으로만 응답하라."
    return {}


def generate(slides, *, target_minutes: float | None = None,
             chars_per_sec: float = CHARS_PER_SEC,
             backend: str = "claude_cli", meta: str = "",
             on_progress=None) -> tuple[list[str], list[dict]]:
    """슬라이드 목록 → (내레이션 리스트, 슬라이드별 분량 리포트).

    target_minutes=None 이면 분량 제어 없이 짧은 대본(기존 동작).
    """
    if backend == "passthrough":
        narr = [_fallback(s) for s in slides]
        rep = [{"index": s.index, "chars": len(n), "target": 0,
                "est_seconds": round(est_seconds(n, chars_per_sec), 1), "refills": 0}
               for s, n in zip(slides, narr)]
        return narr, rep

    call = _BACKENDS.get(backend)
    if call is None:
        raise ValueError(f"알 수 없는 backend: {backend}")

    targets = (target_chars(slides, target_minutes, chars_per_sec)
               if target_minutes else {s.index: 0 for s in slides})

    # ── 1차 생성 ──
    out: dict[int, str] = {}
    batches = [slides[i:i + BATCH] for i in range(0, len(slides), BATCH)]
    prev_tail = ""
    for bi, group in enumerate(batches, start=1):
        got = _call_with_retry(call, SYSTEM,
                               _user_prompt(group, targets, prev_tail, meta),
                               f"배치 {bi}/{len(batches)}")
        for s in group:
            out[s.index] = (got.get(s.index) or "").strip() or _fallback(s)
        last = out.get(group[-1].index, "")
        prev_tail = last.rstrip().split(". ")[-1][:80] if last else ""
        if on_progress:
            on_progress("script", bi, len(batches))

    # ── 보강 패스: 목표의 70% 미만인 것만 ──
    refills = {s.index: 0 for s in slides}
    if target_minutes:
        for rnd in range(1, MAX_REFILL + 1):
            short = [(s, out[s.index], targets[s.index]) for s in slides
                     if len(out[s.index]) < targets[s.index] * MIN_RATIO]
            if not short:
                break
            print(f"  [보강 {rnd}] {len(short)}장이 목표의 70% 미만", flush=True)
            for i in range(0, len(short), BATCH):
                chunk = short[i:i + BATCH]
                got = _call_with_retry(call, REFILL_SYSTEM,
                                       _refill_prompt(chunk, meta), f"보강 {rnd}")
                for s, cur, _tgt in chunk:
                    new = (got.get(s.index) or "").strip()
                    # 보강 결과가 더 짧으면 원본을 지킨다
                    if len(new) > len(cur):
                        out[s.index] = new
                        refills[s.index] += 1
            if on_progress:
                on_progress("refill", rnd, MAX_REFILL)

    narr = [out.get(s.index, "") or _fallback(s) for s in slides]
    rep = [{"index": s.index, "kind": s.kind, "chars": len(n),
            "target": targets[s.index],
            "ratio": round(len(n) / targets[s.index], 2) if targets[s.index] else None,
            "est_seconds": round(est_seconds(n, chars_per_sec), 1),
            "refills": refills[s.index]}
           for s, n in zip(slides, narr)]
    return narr, rep


def report_summary(rep: list[dict], target_minutes: float | None = None) -> str:
    total_chars = sum(r["chars"] for r in rep)
    total_sec = sum(r["est_seconds"] for r in rep)
    lines = [f"슬라이드 {len(rep)}장  총 {total_chars:,}자  "
             f"예상 {total_sec/60:.1f}분  평균 {total_chars//max(len(rep),1)}자/장"]
    if target_minutes:
        lines.append(f"목표 {target_minutes:.0f}분 → 오차 "
                     f"{(total_sec/60 - target_minutes)/target_minutes*100:+.1f}%")
        under = [r for r in rep if r.get("ratio") is not None and r["ratio"] < MIN_RATIO]
        if under:
            lines.append(f"⚠ 목표 70% 미만 {len(under)}장: "
                         + ", ".join(f"#{r['index']}({r['ratio']:.0%})" for r in under[:10]))
    return "\n".join(lines)
