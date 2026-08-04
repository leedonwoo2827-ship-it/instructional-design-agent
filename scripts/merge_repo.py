# -*- coding: utf-8 -*-
r"""저장소 합치기 — instructional-design-agent 를 루트로 흡수한다.

    .venv\Scripts\python scripts\merge_repo.py --dry
    .venv\Scripts\python scripts\merge_repo.py

왜: 엔진과 콘솔이 한 제품인데 저장소가 둘이라 docs·skills 가 앱 안에 갇혀 있었다.
합친 뒤에는 루트가 유일한 저장소다 (.git 을 옮겨 이력을 보존한다).

한 번만 돌린다. 두 번째부터는 옮길 것이 없어 그대로 끝난다.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "instructional-design-agent"

# 앱 → 루트로 그대로 올릴 것. .git 을 포함해야 이력이 살아남는다.
MOVE = [".git", ".gitattributes", ".env", ".env.example",
        "core", "static", "server.py", "pyproject.toml",
        "docs", "skills", "assets", "data", "tools"]

# 루트에 이미 있어서 버리는 것 (루트 쪽이 최신이다)
DROP = ["run.bat", "setup.bat", "run.sh", "setup.sh",
        "__pycache__", "instructional_design_agent.egg-info", ".venv"]

# 앱 README 는 콘솔 설명으로 살려 둔다 — 루트 README 는 제품 전체를 다룬다
README_TO = "docs/콘솔.md"


def log(msg: str) -> None:
    sys.stdout.write(msg + "\n")


def merge_gitignore(dry: bool) -> None:
    """두 .gitignore 를 합친다. 엔진 쪽 항목(assets 383MB 등)이 빠지면 사고가 난다."""
    root_p, app_p = ROOT / ".gitignore", APP / ".gitignore"
    if not app_p.is_file():
        return
    have = root_p.read_text(encoding="utf-8").splitlines() if root_p.is_file() else []
    add = [ln for ln in app_p.read_text(encoding="utf-8").splitlines()
           if ln.strip() and ln not in have]
    if not add:
        return
    log(f"  .gitignore 에 {len(add)}줄 추가")
    if not dry:
        text = "\n".join(have + ["", "# ── 교수설계 콘솔 (합치기 전 앱 .gitignore) ──"] + add)
        root_p.write_text(text.rstrip() + "\n", encoding="utf-8")


def fix_code(dry: bool) -> None:
    """이동으로 어긋나는 경로 두 곳을 고친다.

    core/ 가 루트로 올라오면 대부분의 `parent.parent` 계산은 그대로 맞는다.
    맞지 않는 것만 손댄다 — 추측으로 전부 고치면 멀쩡한 것을 깨뜨린다.
    """
    # 1) core/video.py 의 engine_root() — 앱이 엔진 안에 있다는 가정이 사라졌다
    p = ROOT / "core" / "video.py"
    if p.is_file():
        s = p.read_text(encoding="utf-8")
        old = '    return Path(__file__).resolve().parents[2]'
        new = ('    # 합친 뒤에는 core/ 가 저장소 루트 바로 아래다 → parents[1] 이 루트다.\n'
               '    return Path(__file__).resolve().parents[1]')
        if old in s:
            log("  core/video.py: engine_root() parents[2] → parents[1]")
            if not dry:
                p.write_text(s.replace(old, new), encoding="utf-8")

    # 2) backend/config.py 의 엔진 스크래치 — core/workspace.py 의 기본값과 이름이 겹친다
    p = ROOT / "backend" / "config.py"
    if p.is_file():
        s = p.read_text(encoding="utf-8")
        old = 'WORKSPACE = PROJECT_ROOT / "workspace"'
        new = ('# 콘솔의 산출물 트리(core/workspace.py 의 기본값)와 이름이 겹치면 헷갈린다.\n'
               'WORKSPACE = PROJECT_ROOT / ".engine-work"')
        if old in s:
            log("  backend/config.py: workspace/ → .engine-work/")
            if not dry:
                p.write_text(s.replace(old, new), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="옮기지 않고 계획만 보여준다")
    args = ap.parse_args()
    dry = args.dry

    if not APP.is_dir():
        log("이미 합쳐져 있습니다 (instructional-design-agent 폴더 없음).")
        return 0
    if (ROOT / ".git").exists():
        log(f"[중단] 루트에 이미 .git 이 있습니다: {ROOT / '.git'}")
        log("       손으로 확인한 뒤 다시 실행하세요 — 이력을 덮어쓸 수 있습니다.")
        return 1

    log(f"루트 : {ROOT}")
    log(f"앱   : {APP}")
    log("=" * 60)

    log("[1] 앱 → 루트 이동")
    for name in MOVE:
        src, dst = APP / name, ROOT / name
        if not src.exists():
            continue
        if dst.exists():
            log(f"  ! {name} 이 루트에 이미 있습니다 — 건너뜁니다(손으로 확인)")
            continue
        log(f"  {name}")
        if not dry:
            shutil.move(str(src), str(dst))

    log("[2] 앱 README → " + README_TO)
    src = APP / "README.md"
    if src.is_file():
        dst = ROOT / README_TO
        if not dry:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
        log(f"  {README_TO}")

    log("[3] .gitignore 합치기")
    merge_gitignore(dry)

    log("[4] 코드 경로 보정")
    fix_code(dry)

    log("[5] 남은 것 정리")
    for name in DROP:
        p = APP / name
        if not p.exists():
            continue
        log(f"  버림: instructional-design-agent/{name}")
        if not dry:
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink()

    left = [p.name for p in APP.iterdir()] if APP.is_dir() else []
    if left:
        log(f"  남음: {left}  → 확인 후 손으로 지우세요")
    elif not dry:
        APP.rmdir()
        log("  instructional-design-agent 폴더 제거")

    log("=" * 60)
    if dry:
        log("계획만 보여줬습니다. --dry 없이 다시 실행하세요.")
    else:
        log("완료. 다음을 하세요:")
        log("  1) setup.bat          (앱 venv 를 .venv-app 으로 새로 만듭니다)")
        log("  2) git status         (이동이 rename 으로 잡히는지 확인)")
        log("  3) run.bat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
