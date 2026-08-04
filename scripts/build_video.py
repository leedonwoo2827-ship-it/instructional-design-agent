# -*- coding: utf-8 -*-
"""PPTX → 이론강의 mp4, 한 방에.

    .venv\\Scripts\\python scripts\\build_video.py "deck.pptx" --limit 6
    .venv\\Scripts\\python scripts\\build_video.py "deck.pptx" --chapter 2 --voice F3

--script 로 대본 생성기를 고른다: passthrough(기본) | claude_cli | litellm
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import bundle as bundle_mod, config, runner        # noqa: E402
from backend.ingest import pptx_in, pptx_render                 # noqa: E402


def passthrough(slides) -> list[str]:
    """LLM 없이 슬라이드 텍스트로 대본을 만든다 (파이프라인 검증용)."""
    out = []
    for s in slides:
        parts = []
        if s.title:
            parts.append(s.title + ".")
        if s.spine:
            parts.append(s.spine)
        for b in s.bullets:
            parts.append(b if b.endswith((".", "!", "?")) else b + ".")
        for c in s.cards:
            lbl, desc = c.get("label", ""), c.get("desc", "")
            parts.append(f"{lbl}. {desc}" if desc else f"{lbl}.")
        out.append(" ".join(parts).strip())
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pptx")
    ap.add_argument("--chapter", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0, help="앞 N장만 (빠른 검증)")
    ap.add_argument("--voice", default=config.DEFAULT_VOICE)
    ap.add_argument("--speed", type=float, default=config.DEFAULT_SPEED)
    ap.add_argument("--script", default="passthrough",
                    choices=["passthrough", "claude_cli", "litellm"])
    ap.add_argument("--target-minutes", type=float, default=0,
                    help="목표 영상 길이(분). 지정 시 분량 게이트+보강 패스가 돈다")
    ap.add_argument("--kenburns", default="off", choices=["off", "auto"])
    ap.add_argument("--no-subs", action="store_true")
    ap.add_argument("--reuse-images", default="", help="이미 렌더된 PNG 디렉터리")
    ap.add_argument("--meta", default="", help="과목·주차 (프롬프트 맥락)")
    ap.add_argument("--script-only", action="store_true", help="대본까지만 만들고 멈춘다")
    ap.add_argument("--reuse-script", default="", help="이미 만든 대본 JSON 재사용")
    args = ap.parse_args()

    t0 = time.time()

    print("[1/5] 슬라이드 추출")
    slides = pptx_in.extract(args.pptx)
    if args.limit:
        slides = slides[:args.limit]
    from collections import Counter
    print(f"      {len(slides)}장  {dict(Counter(s.kind for s in slides))}  "
          f"spine {sum(1 for s in slides if s.spine)}장")

    print("[2/5] 슬라이드 렌더 (PowerPoint COM)")
    png_dir = config.WORKSPACE / f"_png_ch{args.chapter:02d}"
    if args.reuse_images:
        pngs = sorted(Path(args.reuse_images).glob("*.png"))[:len(slides)]
        print(f"      재사용 {len(pngs)}장")
    else:
        pngs = pptx_render.render(args.pptx, png_dir)
        pngs = pngs[:len(slides)]
        print(f"      {len(pngs)}장  {time.time()-t0:.1f}s")
    if len(pngs) < len(slides):
        raise SystemExit(f"PNG({len(pngs)})가 슬라이드({len(slides)})보다 적습니다")

    tgt = args.target_minutes or None
    from backend import script_gen
    if args.reuse_script:
        import json as _json
        doc = _json.loads(Path(args.reuse_script).read_text(encoding="utf-8"))
        by_idx = {int(s["index"]): s["narration"] for s in doc["slides"]}
        narrations = [by_idx.get(s.index, "") for s in slides]
        rep = doc.get("report", [])
        print(f"[3/5] 대본 재사용 ({args.reuse_script})")
    else:
        print(f"[3/5] 대본 생성 ({args.script})" + (f"  목표 {tgt:.0f}분" if tgt else ""))
        narrations, rep = script_gen.generate(
            slides, backend=args.script, target_minutes=tgt, meta=args.meta,
            on_progress=lambda stage, d, t: print(f"      [{stage}] {d}/{t}"))
    for line in script_gen.report_summary(rep, tgt).splitlines():
        print("      " + line)
    empty = sum(1 for n in narrations if not n.strip())
    if empty:
        print(f"      ⚠ 빈 대본 {empty}장")

    if args.script_only:
        import json as _json
        out = config.WORKSPACE / f"_script_ch{args.chapter:02d}.json"
        out.write_text(_json.dumps(
            {"target_minutes": tgt, "report": rep,
             "slides": [{"index": s.index, "kind": s.kind, "title": s.title,
                         "narration": n} for s, n in zip(slides, narrations)]},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n대본만 생성: {out}  ({time.time()-t0:.1f}s)")
        return 0

    print("[4/5] 번들 구성")
    b = bundle_mod.build(slides, narrations, pngs, chapter=args.chapter,
                         title=slides[0].title if slides else "",
                         voice=args.voice, speed=args.speed)
    print(f"      {b}")

    print("[5/5] 음성 합성 + 영상 합성")
    last = [""]

    def prog(p):
        line = f"      [{p.stage}] {p.done}/{p.total} {p.message}"[:110]
        if line != last[0]:
            print(line)
            last[0] = line

    mp4 = await runner.render(b, voice=args.voice, speed=args.speed,
                              kenburns=args.kenburns, burn_subs=not args.no_subs,
                              on_progress=prog)
    size = mp4.stat().st_size / 1024 / 1024
    print(f"\n완료: {mp4}  ({size:.1f} MB, 총 {time.time()-t0:.1f}s)")
    return 0


sys.exit(asyncio.run(main()))
