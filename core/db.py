# -*- coding: utf-8 -*-
"""프로젝트(강의 1건) 영속화 — SQLite (stdlib sqlite3, 추가 의존성 없음).

'프로젝트 = 1행' 모델. 강의 정보(form)·강의계획서·교재·PPT 개요와 각 대화이력을
한 행에 담아 저장/로드한다. data/app.db 한 파일. (260622 SQLite 패턴 차용, 단순화)
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# 기본은 data/app.db. 테스트/개발용으로 IDA_DB_PATH 로 격리 가능(실 DB 보호).
DB_PATH = Path(os.environ.get("IDA_DB_PATH") or (Path(__file__).resolve().parent.parent / "data" / "app.db"))

# 텍스트/정수 컬럼과 JSON(직렬화) 컬럼 구분
_TEXT_COLS = ("name", "syllabus_md", "script_doc_md", "script_ppt_md")
_INT_COLS = ("script_week",)
_JSON_COLS = ("form", "syllabus_msgs", "script_doc_msgs", "script_ppt_msgs")
SAVABLE = _TEXT_COLS + _INT_COLS + _JSON_COLS


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                name              TEXT NOT NULL,
                created_at        TEXT NOT NULL,
                updated_at        TEXT NOT NULL,
                form_json         TEXT DEFAULT '{}',
                syllabus_md       TEXT DEFAULT '',
                syllabus_msgs_json TEXT DEFAULT '[]',
                script_week       INTEGER DEFAULT 1,
                script_doc_md     TEXT DEFAULT '',
                script_doc_msgs_json TEXT DEFAULT '[]',
                script_ppt_md     TEXT DEFAULT '',
                script_ppt_msgs_json TEXT DEFAULT '[]'
            )
            """
        )


def list_projects() -> List[Dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, name, updated_at FROM projects ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def create_project(name: str) -> int:
    ts = _now()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO projects (name, created_at, updated_at) VALUES (?, ?, ?)",
            (name or "새 강의", ts, ts),
        )
        return int(cur.lastrowid)


def load_project(pid: int) -> Optional[Dict[str, Any]]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    out: Dict[str, Any] = {
        "id": d["id"], "name": d["name"],
        "created_at": d["created_at"], "updated_at": d["updated_at"],
        "syllabus_md": d["syllabus_md"] or "",
        "script_doc_md": d["script_doc_md"] or "",
        "script_ppt_md": d["script_ppt_md"] or "",
        "script_week": d["script_week"] or 1,
    }
    out["form"] = json.loads(d["form_json"] or "{}")
    out["syllabus_msgs"] = json.loads(d["syllabus_msgs_json"] or "[]")
    out["script_doc_msgs"] = json.loads(d["script_doc_msgs_json"] or "[]")
    out["script_ppt_msgs"] = json.loads(d["script_ppt_msgs_json"] or "[]")
    return out


def save_project(pid: int, **fields: Any) -> None:
    """제공된 필드만 갱신. 키는 SAVABLE 참고(form/*_msgs 는 JSON 직렬화)."""
    sets, vals = [], []
    for key, val in fields.items():
        if key in _JSON_COLS:
            sets.append(f"{key}_json = ?")
            vals.append(json.dumps(val, ensure_ascii=False))
        elif key in _TEXT_COLS or key in _INT_COLS:
            sets.append(f"{key} = ?")
            vals.append(val)
    sets.append("updated_at = ?")
    vals.append(_now())
    vals.append(pid)
    with _connect() as conn:
        conn.execute(f"UPDATE projects SET {', '.join(sets)} WHERE id = ?", vals)


def rename_project(pid: int, name: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET name=?, updated_at=? WHERE id=?",
            (name or "새 강의", _now(), pid),
        )


def delete_project(pid: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM projects WHERE id=?", (pid,))
