# -*- coding: utf-8 -*-
"""Phase 0 게이트 — 엔진이 이 프로젝트 안에서 자기완결적으로 도는지 검증.

    .venv\\Scripts\\python scripts\\doctor.py            # 검증만
    .venv\\Scripts\\python scripts\\doctor.py --voices   # 보이스 10종 샘플까지 합성

프로젝트 폴더를 통째로 다른 경로에 옮긴 뒤에도 이게 통과해야 한다.
"""
from __future__ import annotations

import asyncio
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import config  # noqa: E402  (env 주입이 먼저 일어나야 함)

from voicewright._assets_check import check_assets  # noqa: E402
from voicewright.engine import Engine               # noqa: E402
from voicewright.settings import load as load_settings  # noqa: E402
from voicewright.voices import ALL_VOICE_CODES      # noqa: E402

SAMPLE = "오늘은 체제적 접근과 ADDIE 모형을 학습하겠습니다. 먼저 체제의 개념부터 살펴보죠."

ok = True


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok
    mark = "OK  " if cond else "FAIL"
    if not cond:
        ok = False
    print(f"[{mark}] {label}" + (f"  — {detail}" if detail else ""))


async def main() -> int:
    print(f"project_root : {config.PROJECT_ROOT}")
    print(f"engine_dir   : {config.ENGINE_DIR}")
    print(f"crossfade    : {config.CROSSFADE_SEC}s\n")

    st = load_settings()

    # 1) 경로가 전부 프로젝트 안을 가리키는가 (자기완결성)
    for label, p in (("assets/onnx", st.onnx_dir),
                     ("voice_styles", st.voice_styles_dir),
                     ("voice_map", st.voice_map_path),
                     ("pronunciation_map", st.pronunciation_map_path)):
        inside = config.PROJECT_ROOT in Path(p).resolve().parents
        check(f"{label} 프로젝트 내부", inside, str(p))

    # 2) 모델 자산
    try:
        check_assets(st.onnx_dir, st.voice_styles_dir)
        check("Supertonic assets 무결성", True)
    except Exception as e:  # noqa: BLE001
        check("Supertonic assets 무결성", False, str(e).splitlines()[0])
        return 1

    # 3) 외부 바이너리
    for b in ("ffmpeg", "ffprobe"):
        check(f"{b} on PATH", shutil.which(b) is not None)

    # 4) 엔진 모듈
    try:
        import mp4maker  # noqa: F401
        import chodangi_app.synth  # noqa: F401
        check("mp4maker / chodangi_app import", True)
    except Exception as e:  # noqa: BLE001
        check("mp4maker / chodangi_app import", False, repr(e))

    # 5) 실제 합성
    t0 = time.time()
    eng = await Engine.get()
    check("Supertonic 엔진 로드", True, f"{time.time()-t0:.1f}s, sr={eng.sample_rate}")

    wav = await eng.synth(SAMPLE, voice_code=config.DEFAULT_VOICE, lang="ko")
    dur = len(wav) / eng.sample_rate
    check(f"스모크 합성 ({config.DEFAULT_VOICE})", dur > 1.0, f"{dur:.2f}s")

    if "--voices" in sys.argv:
        out = config.PROJECT_ROOT / "workspace" / "_voice_samples"
        out.mkdir(parents=True, exist_ok=True)
        import soundfile as sf
        print("\n보이스 10종 샘플:")
        for code in ALL_VOICE_CODES:
            t = time.time()
            w = await eng.synth(SAMPLE, voice_code=code, lang="ko")
            d = len(w) / eng.sample_rate
            sf.write(str(out / f"{code}.wav"), w, eng.sample_rate)
            print(f"  {code}  dur={d:5.2f}s  rtf={(time.time()-t)/d:.2f}")
        print(f"  -> {out}")

    print("\n" + ("게이트 통과" if ok else "게이트 실패"))
    return 0 if ok else 1


sys.exit(asyncio.run(main()))
