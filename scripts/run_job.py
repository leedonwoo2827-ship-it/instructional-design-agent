# -*- coding: utf-8 -*-
r"""앱이 부르는 단계별 실행기 — job.json 을 읽고 progress.json 을 쓴다.

    .venv\Scripts\python scripts\run_job.py "<06_영상>\job.json"

앱(FastAPI 로컬 콘솔)은 이 프로세스를 subprocess 로 띄우고 progress.json 만 폴링한다.
그래서 앱 venv 에 onnxruntime·pywin32 가 없어도 되고, 앱을 닫아도 렌더가 계속된다.

job.json (앱이 쓴다):
  { lecture_dir, chapter, deck, script, slides_dir, bundle_root, out_dir, out_name,
    voice, speed, limit, kenburns, burn_subs, stages[], force[], title }

progress.json (여기서 원자적으로 쓴다):
  { pid, stage, done, total, message, stages{}, started_at, updated_at,
    finished_at, error, out, log[] }

단계는 산출물이 있으면 건너뛴다(force 로 강제).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import bundle as bundle_mod, config, runner   # noqa: E402
from backend.ingest import pptx_in, pptx_render            # noqa: E402

STAGES = ("render", "script", "bundle", "tts", "compose", "viewer")


class Prog:
    """progress.json 원자적 기록 — 앱이 언제 읽어도 반쯤 쓰인 파일을 보지 않게."""

    def __init__(self, path: Path, stages: list[str]):
        self.path = path
        self.d = {
            "pid": os.getpid(),
            "stage": "", "done": 0, "total": 0, "message": "",
            "stages": {s: ("todo" if s in stages else "skip") for s in STAGES},
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": "", "finished_at": None, "error": None, "out": None,
            "log": [],
        }
        self.flush()

    def flush(self) -> None:
        self.d["updated_at"] = datetime.now().isoformat(timespec="seconds")
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def stage(self, name: str, state: str, msg: str = "") -> None:
        self.d["stages"][name] = state
        if state == "run":
            self.d.update(stage=name, done=0, total=0)
        if msg:
            self.d["message"] = msg
            self.d["log"] = (self.d["log"] + [f"{name}: {msg}"])[-40:]
            print(f"[{name}] {msg}", flush=True)
        self.flush()

    def tick(self, done: int, total: int, msg: str = "") -> None:
        self.d.update(done=done, total=total)
        if msg:
            self.d["message"] = msg
        self.flush()

    def fail(self, err: str) -> None:
        self.d["error"] = err
        self.d["finished_at"] = datetime.now().isoformat(timespec="seconds")
        if (cur := self.d.get("stage")):
            self.d["stages"][cur] = "fail"
        self.flush()

    def done(self, out: str | None) -> None:
        self.d["out"] = out
        self.d["message"] = "완료"
        self.d["finished_at"] = datetime.now().isoformat(timespec="seconds")
        self.flush()


def read_narration(path: Path) -> tuple[list[str], int]:
    """앱이 만든 나레이션 파일 → 씬 순서대로의 대본 목록."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    rows = sorted(doc.get("slides") or [], key=lambda r: int(r["index"]))
    return [str(r.get("narration") or "") for r in rows], len(rows)


async def run(job_path: Path) -> int:
    job = json.loads(job_path.read_text(encoding="utf-8"))
    lec = Path(job["lecture_dir"]).resolve()
    chapter = int(job.get("chapter") or 1)
    want = [s for s in STAGES if s in (job.get("stages") or list(STAGES))]
    force = set(job.get("force") or [])

    deck = Path(job["deck"]).resolve()
    script_p = Path(job["script"]).resolve()
    slides_dir = Path(job.get("slides_dir") or (lec / "슬라이드")).resolve()
    bundle_root = Path(job.get("bundle_root") or (lec / "번들")).resolve()
    out_dir = Path(job.get("out_dir") or (lec / "완성")).resolve()
    out_name = job.get("out_name") or "영상_v1.mp4"
    for d in (slides_dir, bundle_root, out_dir):
        d.mkdir(parents=True, exist_ok=True)

    pg = Prog(lec / "progress.json", want)
    try:
        if not deck.is_file():
            raise SystemExit(f"덱을 찾을 수 없습니다: {deck}")
        if not script_p.is_file():
            raise SystemExit(f"나레이션 대본이 없습니다: {script_p}")

        narr, n_script = read_narration(script_p)
        slides = pptx_in.extract(deck)
        n_deck = len(slides)

        # ★ 씬 수는 **대본이 정한다.** 덱이 더 길면 뒤쪽을 무음으로 채우는 게 아니라
        #   대본 길이에서 자른다 — 대본 일부만 써 놓고 미리 보는 것이 정상 흐름이고,
        #   무음 94장을 렌더하면 시간만 버린다(102장 덱 + 8장 대본에서 실제로 그랬다).
        n = min(len(narr), n_deck)
        if (lim := int(job.get("limit") or 0)) > 0:
            n = min(n, lim)
        slides, narr = slides[:n], narr[:n]

        if n_script > n_deck:
            pg.stage("script", "done",
                     f"⚠ 대본 {n_script}장 > 덱 {n_deck}장 — 덱 길이로 자름")
        elif n < n_deck:
            pg.stage("script", "done",
                     f"대본 {n}장까지만 렌더 (덱 {n_deck}장 · 나머지는 대본 없음)")
        else:
            pg.stage("script", "done", f"대본 {n}장 사용")
        pg.stage("render", "todo", f"슬라이드 {len(slides)}장")

        # ── 1. 슬라이드 PNG ──
        pngs = sorted(slides_dir.glob("*.png"))
        if "render" in want and (len(pngs) < len(slides) or "render" in force):
            pg.stage("render", "run", "PowerPoint 로 슬라이드 내보내는 중")
            pngs = pptx_render.render(deck, slides_dir,
                                      on_progress=lambda i, t: pg.tick(i, t))
            pg.stage("render", "done", f"PNG {len(pngs)}장")
        else:
            pg.stage("render", "done", f"PNG {len(pngs)}장 (기존)")
        pngs = pngs[:len(slides)]
        if len(pngs) < len(slides):
            raise SystemExit(f"PNG({len(pngs)})가 슬라이드({len(slides)})보다 적습니다.")

        bundle = bundle_root / f"ch{chapter:02d}"

        # ── 2. 번들 ──
        if "bundle" in want:
            pg.stage("bundle", "run", "번들 구성 중")
            bundle_mod.build(slides, narr, pngs, chapter=chapter,
                             title=job.get("title") or "",
                             voice=job.get("voice"), speed=job.get("speed"),
                             workspace=bundle_root, clean=("bundle" in force))
            pg.stage("bundle", "done", f"{bundle.name} · 씬 {len(slides)}개")

        # ── 3. 음성 + 자막 ──
        if "tts" in want:
            pg.stage("tts", "run", "Supertonic 음성 합성 중")
            await runner.synthesize(
                bundle, voice=job.get("voice"), speed=job.get("speed"),
                only=job.get("only"),
                on_progress=lambda pr: pg.tick(pr.done, pr.total, pr.message))
            pg.stage("tts", "done", "음성·자막 생성 완료")

        # ── 4. 영상 ──
        final = out_dir / out_name
        if "compose" in want:
            pg.stage("compose", "run", "ffmpeg 합성 중 (가장 오래 걸립니다)")
            mp4 = runner.compose(
                bundle, kenburns=job.get("kenburns", "off"),
                # 기본은 끌 수 있는 자막(mov_text). 번인은 되돌릴 수 없어 기본에서 뺐다.
                burn_subs=bool(job.get("burn_subs", False)),
                soft_subs=bool(job.get("soft_subs", True)),
                on_progress=lambda pr: pg.tick(pr.done, pr.total, pr.message),
                on_log=lambda ln: pg.stage("compose", "run", ln) if "[warn]" in ln else None)
            final.write_bytes(mp4.read_bytes())
            comb = next(iter((bundle / "subtitles").glob("ch*.srt")), None)
            if comb:
                final.with_suffix(".srt").write_text(
                    comb.read_text(encoding="utf-8"), encoding="utf-8")
            pg.stage("compose", "done",
                     f"{final.name} · {final.stat().st_size/1048576:.1f} MB")

        # ── 5. 뷰어 ──
        if "viewer" in want:
            pg.stage("viewer", "run", "뷰어 생성 중")
            import subprocess
            subprocess.run([sys.executable, str(Path(__file__).parent / "make_viewer.py"),
                            str(lec)], env=config.engine_env(), check=False)
            pg.stage("viewer", "done", "script.html · bundle.html")

        pg.done(str(final) if final.is_file() else None)
        return 0

    except BaseException as e:  # noqa: BLE001  (취소도 progress 에 남겨야 한다)
        pg.fail(f"{type(e).__name__}: {e}")
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    t0 = time.time()
    code = asyncio.run(run(Path(sys.argv[1]).resolve()))
    print(f"[exit] code={code} {time.time()-t0:.1f}s", flush=True)
    sys.exit(code)
