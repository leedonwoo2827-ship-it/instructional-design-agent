from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..schemas import JobProgress, JobStatus


@dataclass
class JobRecord:
    job_id: str
    chapter: str
    scene_count: int
    output_dir: Path
    status: str = "queued"          # queued | running | done | error
    completed: int = 0
    current_scene: int | None = None
    files: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def to_status(self) -> JobStatus:
        return JobStatus(
            job_id=self.job_id,
            status=self.status,  # type: ignore[arg-type]
            progress=JobProgress(
                completed=self.completed,
                total=self.scene_count,
                current_scene=self.current_scene,
            ),
            output_dir=str(self.output_dir),
            files=list(self.files),
            warnings=list(self.warnings),
            error=self.error,
            started_at=self.started_at,
            finished_at=self.finished_at,
        )


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, *, chapter: str, scene_count: int, output_dir: Path) -> JobRecord:
        async with self._lock:
            job_id = secrets.token_hex(4)
            rec = JobRecord(
                job_id=job_id,
                chapter=chapter,
                scene_count=scene_count,
                output_dir=output_dir,
                started_at=datetime.now(timezone.utc),
                status="queued",
            )
            self._jobs[job_id] = rec
            return rec

    async def get(self, job_id: str) -> JobRecord | None:
        async with self._lock:
            return self._jobs.get(job_id)


_registry: JobRegistry | None = None


def get_registry() -> JobRegistry:
    global _registry
    if _registry is None:
        _registry = JobRegistry()
    return _registry
