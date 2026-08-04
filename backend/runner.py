# -*- coding: utf-8 -*-
"""렌더 실행 — Supertonic 합성 + mp4maker 합성, 진행률 콜백 포함.

`CROSSFADE_SEC`를 **양쪽에 같은 값으로** 주입하는 것이 이 모듈의 존재 이유다.
어긋나면 자막이 씬마다 누적으로 밀린다(원본 프로젝트에서 83.6초 밀린 이력).
"""
from __future__ import annotations

import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from . import config

config.bootstrap()

_LOCK = threading.Lock()   # 엔진 스크래치가 exist_ok=True라 동시 렌더는 충돌한다

_RE_SCENE = re.compile(r"\[scene\]\s+sc(\d+)\s+done.*?progress=(\d+)/(\d+)")
_RE_STAGE = re.compile(r"\[stage\]\s+(\S+)")
_RE_ERROR = re.compile(r"\[(?:error|ERROR)\]\s*(.*)$")


@dataclass
class Progress:
    stage: str = ""
    done: int = 0
    total: int = 0
    message: str = ""


async def synthesize(bundle: Path, *, voice: str | None = None, speed: float | None = None,
                     only: list[int] | None = None, on_progress=None) -> dict:
    """Supertonic 3로 씬별 wav + srt 생성. 통합 srt 타임코드까지 여기서 확정된다."""
    from chodangi_app.synth import synthesize as _synth

    def cb(done, total, scene=None):
        if on_progress:
            on_progress(Progress("tts", done, total, f"음성 {done}/{total}"))

    return await _synth(
        bundle,
        only=only,
        voice_override=voice or config.DEFAULT_VOICE,
        speed=speed if speed is not None else config.DEFAULT_SPEED,
        total_step=config.DEFAULT_TOTAL_STEP,
        on_progress=cb,
        crossfade=config.CROSSFADE_SEC,      # ★ 아래 --crossfade 와 반드시 같은 값
    )


def compose(bundle: Path, *, kenburns: str = "off", burn_subs: bool = False,
            soft_subs: bool = True, on_progress=None, on_log=None) -> Path:
    """mp4maker(ffmpeg)로 최종 mp4 합성. 성공 시 mp4 경로를 돌려준다.

    자막 두 방식은 서로 배타적이다:
      burn_subs — 픽셀에 굽는다. 어디서든 보이지만 **끌 수 없다.**
      soft_subs — mp4 안에 자막 트랙(mov_text)을 넣는다. 플레이어에서 켜고 끈다.

    기본을 soft 로 둔 이유: 번인은 한 번 구우면 되돌릴 수 없고, 강의 영상은
    자막을 끄고 보고 싶을 때가 있다. 소프트 자막 mux 는 재인코딩이 아니라
    스트림 복사라 추가 시간도 거의 없다.
    """
    args = [
        sys.executable, "-m", "mp4maker", str(Path(bundle).resolve()),
        "--crossfade", str(config.CROSSFADE_SEC),   # ★ 위 synthesize(crossfade=) 와 동일
        "--kenburns", kenburns,
    ]
    if not burn_subs:
        args.append("--no-subs")
    if not soft_subs:
        args.append("--no-soft-sub")

    proc = subprocess.Popen(
        args, cwd=str(config.ENGINE_DIR), env=config.engine_env(),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    tail: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip()
        tail.append(line)
        del tail[:-40]
        if on_log:
            on_log(line)
        m = _RE_SCENE.search(line)
        if m and on_progress:
            on_progress(Progress("compose", int(m.group(2)), int(m.group(3)), line))
            continue
        m = _RE_STAGE.search(line)
        if m and on_progress:
            on_progress(Progress("compose", 0, 0, m.group(1)))
    code = proc.wait()

    from .bundle import output_mp4
    out = output_mp4(Path(bundle))
    if soft_subs:
        # mp4maker 는 자막 트랙이 든 것을 `chNN_final_softsub.mp4` 로 따로 낸다.
        # 그게 있으면 그걸 최종본으로 쓴다 — 없는데 쓰면 자막이 통째로 사라진다.
        soft = sorted((Path(bundle) / "draft").glob("*_final_softsub.mp4"))
        if soft:
            out = soft[0]
        elif on_log:
            on_log("[warn] softsub mp4 가 없습니다 — 자막 트랙 없는 본편을 씁니다.")
    if code != 0 or out is None or out.stat().st_size < 1_000_000:
        raise RuntimeError(
            f"mp4maker 실패 (exit={code}, out={out}).\n" + "\n".join(tail[-15:]))
    return out


async def render(bundle: Path, *, voice: str | None = None, speed: float | None = None,
                 kenburns: str = "off", burn_subs: bool = True, on_progress=None) -> Path:
    """합성 → 영상까지 한 번에. 동시 실행은 락으로 직렬화한다."""
    if not _LOCK.acquire(blocking=False):
        raise RuntimeError("이미 렌더가 진행 중입니다 (엔진이 동시 실행을 지원하지 않음)")
    try:
        await synthesize(bundle, voice=voice, speed=speed, on_progress=on_progress)
        return compose(bundle, kenburns=kenburns, burn_subs=burn_subs, on_progress=on_progress)
    finally:
        _LOCK.release()
