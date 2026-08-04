from __future__ import annotations

import io
import json
import logging
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import soundfile as sf
import yaml
from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Response, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse

from pydantic import BaseModel, Field

from .. import settings as settings_module
from ..audio_io import to_wav_bytes
from ..batch import parse_script, run_batch
from ..engine import Engine
from ..pronunciation import load_pronunciation_map
from ..paths import (
    chapter_audio_dir,
    chapter_srt_path,
    chapter_subtitles_dir,
    narration_path,
    normalize_chapter_id,
    resolve_chapter_id,
    srt_path,
)
from ..schemas import (
    BatchSubmitResponse,
    JobStatus,
    SynthesizeRequest,
    VoiceInfoOut,
    VoiceListResponse,
)
from ..srt import (
    Cue,
    auto_time_cues,
    make_multi_srt,
    merge_scene_cues,
    parse_srt_cues,
    split_into_cues,
)
from ..voices import ALL_VOICE_CODES, load_voice_map
from .jobs import JobRecord, get_registry

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/voices", response_model=VoiceListResponse)
async def get_voices() -> VoiceListResponse:
    s = settings_module.load()
    vmap = load_voice_map(s.voice_map_path)
    voices = [
        VoiceInfoOut(
            code=c,
            gender="male" if c.startswith("M") else "female",
            default_for_unknown=(c == vmap.default),
        )
        for c in ALL_VOICE_CODES
    ]
    return VoiceListResponse(voices=voices, voice_map=dict(vmap.styles), default=vmap.default)


@router.post("/synthesize")
async def synthesize(req: SynthesizeRequest) -> Response:
    engine = await Engine.get()
    s = settings_module.load()
    vmap = load_voice_map(s.voice_map_path)

    if req.voice:
        voice_code = req.voice.upper()
        if voice_code not in ALL_VOICE_CODES:
            raise HTTPException(status_code=422, detail=f"unknown voice: {req.voice}")
    else:
        voice_code, _ = vmap.resolve(req.voice_style)

    try:
        wav = await engine.synth(
            req.text,
            voice_code=voice_code,
            lang=req.lang,
            total_step=req.total_step,
            speed=req.speed,
        )
    except Exception as exc:
        logger.exception("synthesize 실패")
        raise HTTPException(status_code=500, detail=f"synthesis failed: {exc}") from exc

    data = to_wav_bytes(wav, engine.sample_rate)
    ts = int(time.time())
    return Response(
        content=data,
        media_type="audio/wav",
        headers={"Content-Disposition": f'attachment; filename="synth_{voice_code}_{ts}.wav"'},
    )


async def _run_batch_job(rec: JobRecord, *, raw: bytes, filename: str | None,
                        chapter_explicit: str | None, output_root: str | None,
                        voice_override: str | None, speed: float | None,
                        total_step: int | None) -> None:
    try:
        rec.status = "running"
        engine = await Engine.get()
        script = parse_script(raw)

        async def cb(completed: int, total: int, current: int | None):
            rec.completed = completed
            rec.current_scene = current

        result = await run_batch(
            engine=engine,
            script=script,
            chapter_id_explicit=chapter_explicit,
            filename_hint=filename,
            output_root=Path(output_root) if output_root else None,
            voice_override=voice_override,
            speed=speed,
            total_step=total_step,
            on_progress=cb,
        )
        rec.files = result.files
        rec.warnings = result.warnings
        rec.output_dir = result.output_dir
        rec.status = "done"
    except Exception as exc:
        logger.exception("batch job 실패: %s", rec.job_id)
        rec.status = "error"
        rec.error = str(exc)
    finally:
        rec.finished_at = datetime.now(timezone.utc)


@router.post("/batch", response_model=BatchSubmitResponse)
async def submit_batch(
    background: BackgroundTasks,
    script: UploadFile = File(...),
    chapter: str | None = Form(None),
    output_root: str | None = Form(None),
    voice_override: str | None = Form(None),
    speed: float | None = Form(None),
    total_step: int | None = Form(None),
) -> BatchSubmitResponse:
    raw = await script.read()
    if not raw:
        raise HTTPException(status_code=422, detail="empty script file")

    try:
        parsed = parse_script(raw)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid script JSON: {exc}") from exc

    try:
        chapter_id = resolve_chapter_id(
            explicit=chapter,
            script_field=parsed.chapter,
            filename_hint=script.filename,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    s = settings_module.load()
    out_root = Path(output_root) if output_root else s.workspace_root
    out_dir = out_root / f"ch{chapter_id}" / "audio"

    if voice_override and voice_override.upper() not in ALL_VOICE_CODES:
        raise HTTPException(status_code=422, detail=f"unknown voice: {voice_override}")

    registry = get_registry()
    rec = await registry.create(chapter=chapter_id, scene_count=len(parsed.scenes), output_dir=out_dir)

    background.add_task(
        _run_batch_job,
        rec,
        raw=raw,
        filename=script.filename,
        chapter_explicit=chapter,
        output_root=output_root,
        voice_override=voice_override.upper() if voice_override else None,
        speed=speed,
        total_step=total_step,
    )

    return BatchSubmitResponse(
        job_id=rec.job_id,
        scene_count=len(parsed.scenes),
        chapter=chapter_id,
        status_url=f"/api/jobs/{rec.job_id}",
    )


@router.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job(job_id: str) -> JobStatus:
    rec = await get_registry().get(job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="job not found")
    return rec.to_status()


@router.get("/jobs/{job_id}/zip")
async def get_job_zip(job_id: str) -> StreamingResponse:
    rec = await get_registry().get(job_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="job not found")
    if rec.status != "done":
        raise HTTPException(status_code=409, detail=f"job not done (status={rec.status})")

    s = settings_module.load()
    audio_dir = chapter_audio_dir(s.workspace_root, rec.chapter)
    sub_dir = chapter_subtitles_dir(s.workspace_root, rec.chapter)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if audio_dir.exists():
            for p in sorted(audio_dir.glob("*.wav")):
                zf.write(p, arcname=f"audio/{p.name}")
        if sub_dir.exists():
            for p in sorted(sub_dir.glob("*.srt")):
                zf.write(p, arcname=f"subtitles/{p.name}")
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="ch{rec.chapter}_bundle.zip"'},
    )


# ---------------------------------------------------------------------------
# Scene-by-scene UI: parse → list scenes, synthesize one at a time, serve files
# ---------------------------------------------------------------------------

@router.post("/parse_script")
async def parse_script_endpoint(
    script: UploadFile = File(...),
    chapter: str | None = Form(None),
) -> dict:
    raw = await script.read()
    if not raw:
        raise HTTPException(status_code=422, detail="empty script file")
    try:
        parsed = parse_script(raw)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid script JSON: {exc}") from exc
    try:
        chapter_id = resolve_chapter_id(
            explicit=chapter,
            script_field=parsed.chapter,
            filename_hint=script.filename,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    s = settings_module.load()
    vmap = load_voice_map(s.voice_map_path)
    scenes_out = []
    for sc in parsed.scenes:
        code, _ = vmap.resolve(sc.voice_style)
        scenes_out.append({
            "scene": sc.scene,
            "narration_text": sc.narration_text,
            "srt_text": sc.srt_text,
            "narration_seconds": sc.narration_seconds,
            "voice_style": sc.voice_style,
            "voice_resolved": code,
            "image_filename": sc.image_filename,
        })
    return {"chapter": chapter_id, "scenes": scenes_out}


@router.post("/synthesize_scene")
async def synthesize_scene(
    chapter: str = Form(...),
    scene: int = Form(...),
    text: str = Form(...),
    srt_text: str | None = Form(None),       # SRT 자막에 들어갈 원본 텍스트 (없으면 text 사용)
    voice: str | None = Form(None),
    voice_style: str | None = Form(None),
    speed: float | None = Form(None),
    total_step: int | None = Form(None),
    narration_seconds: float | None = Form(None),
    output_root: str | None = Form(None),
) -> dict:
    if not text.strip():
        raise HTTPException(status_code=422, detail="empty narration_text")

    engine = await Engine.get()
    s = settings_module.load()
    vmap = load_voice_map(s.voice_map_path)

    if voice:
        voice_code = voice.upper()
        if voice_code not in ALL_VOICE_CODES:
            raise HTTPException(status_code=422, detail=f"unknown voice: {voice}")
    else:
        voice_code, _ = vmap.resolve(voice_style)

    out_root = Path(output_root) if output_root else s.workspace_root

    try:
        wav = await engine.synth(text, voice_code=voice_code, total_step=total_step, speed=speed)
    except Exception as exc:
        logger.exception("synthesize_scene 실패: ch%s scene %s", chapter, scene)
        raise HTTPException(status_code=500, detail=f"synthesis failed: {exc}") from exc

    from ..audio_io import write_wav as _write_wav
    wav_path = narration_path(out_root, chapter, int(scene))
    _write_wav(wav_path, wav, engine.sample_rate)

    actual_duration = float(len(wav)) / float(engine.sample_rate)
    body_for_srt = (srt_text or text).strip()  # 자막엔 항상 원본 텍스트가 들어감
    # ~30자 구간으로 쪼개고 실측 오디오 길이에 맞춰 자동 타임코드 부여 (사용자가 이후 조정)
    cues = auto_time_cues(split_into_cues(body_for_srt), actual_duration)
    srt_body_str = make_multi_srt(cues)
    srt_p = srt_path(out_root, chapter, int(scene))
    srt_p.parent.mkdir(parents=True, exist_ok=True)
    srt_p.write_text(srt_body_str, encoding="utf-8")

    return {
        "chapter": chapter,
        "scene": int(scene),
        "voice": voice_code,
        "duration_seconds": actual_duration,
        "wav_url": f"/api/files/ch{chapter}/audio/{wav_path.name}",
        "srt_url": f"/api/files/ch{chapter}/subtitles/{srt_p.name}",
        "cues": [{"text": c.text, "start": c.start, "end": c.end} for c in cues],
    }


@router.get("/files/ch{chapter_id}/{kind}/{filename}")
async def serve_workspace_file(chapter_id: str, kind: str, filename: str) -> FileResponse:
    if kind not in ("audio", "subtitles"):
        raise HTTPException(status_code=404, detail="unknown kind")
    if "/" in filename or "\\" in filename or filename.startswith(".."):
        raise HTTPException(status_code=400, detail="invalid filename")
    s = settings_module.load()
    base = s.workspace_root / f"ch{chapter_id}" / kind
    p = base / filename
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    media = "audio/wav" if kind == "audio" else "text/plain; charset=utf-8"
    return FileResponse(str(p), media_type=media, filename=filename)


@router.get("/files/ch{chapter_id}/subtitles_full")
async def serve_chapter_srt(chapter_id: str) -> FileResponse:
    s = settings_module.load()
    p = chapter_srt_path(s.workspace_root, chapter_id)
    if not p.exists():
        raise HTTPException(status_code=404, detail="chapter SRT not generated yet")
    return FileResponse(str(p), media_type="text/plain; charset=utf-8", filename=f"ch{chapter_id}.srt")


_PER_SCENE_SRT_RE = re.compile(r"^ch[^_]+_(\d+)_narration\.srt$")


def _wav_duration(path: Path) -> float:
    info = sf.info(str(path))
    return info.frames / float(info.samplerate)


def _cues_from_payload(raw: str) -> list[Cue]:
    """프런트가 보낸 cues JSON([{start,end,text}, ...])을 검증해 Cue 목록으로."""
    try:
        data = json.loads(raw)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid cues JSON: {exc}") from exc
    if not isinstance(data, list):
        raise HTTPException(status_code=422, detail="cues must be a list")
    cues: list[Cue] = []
    prev_end = -1.0
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise HTTPException(status_code=422, detail=f"cue {i} must be an object")
        text = str(item.get("text", "")).strip()
        try:
            start = float(item.get("start", 0.0))
            end = float(item.get("end", 0.0))
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"cue {i} has non-numeric time")
        if start < 0 or end < start:
            raise HTTPException(status_code=422, detail=f"cue {i} time invalid (start={start}, end={end})")
        if start < prev_end - 1e-3:
            raise HTTPException(status_code=422, detail=f"cue {i} overlaps previous (start < prev end)")
        prev_end = end
        if text:
            cues.append(Cue(text=text, start=round(start, 3), end=round(end, 3)))
    return cues


@router.post("/save_scene_srt")
async def save_scene_srt(
    chapter: str = Form(...),
    scene: int = Form(...),
    cues: str = Form(...),
    output_root: str | None = Form(None),
) -> dict:
    """사용자가 UI에서 조정한 큐 타임코드를 per-scene SRT로 덮어쓴다."""
    chap = normalize_chapter_id(chapter)
    if chap is None:
        raise HTTPException(status_code=422, detail=f"invalid chapter: {chapter}")
    parsed = _cues_from_payload(cues)
    if not parsed:
        raise HTTPException(status_code=422, detail="no cues to save")

    s = settings_module.load()
    out_root = Path(output_root) if output_root else s.workspace_root
    srt_p = srt_path(out_root, chap, int(scene))
    srt_p.parent.mkdir(parents=True, exist_ok=True)
    srt_p.write_text(make_multi_srt(parsed), encoding="utf-8")
    return {
        "chapter": chap,
        "scene": int(scene),
        "cue_count": len(parsed),
        "srt_url": f"/api/files/ch{chap}/subtitles/{srt_p.name}",
    }


@router.post("/regenerate_chapter_srt")
async def regenerate_chapter_srt(chapter: str = Form(...)) -> dict:
    """workspace의 per-scene SRT + WAV을 다시 모아 챕터 통합 SRT를 새로 만든다.

    카드별 재생성으로 일부 scene이 갱신된 뒤, 챕터 자막의 타임코드를 현재
    오디오와 동기화하기 위함. 멀티큐 per-scene SRT를 누적 offset으로 병합한다.
    """
    chap = normalize_chapter_id(chapter)
    if chap is None:
        raise HTTPException(status_code=422, detail=f"invalid chapter: {chapter}")

    s = settings_module.load()
    sub_dir = chapter_subtitles_dir(s.workspace_root, chap)
    if not sub_dir.exists():
        raise HTTPException(status_code=404, detail=f"no subtitles dir for ch{chap}")

    scene_data: list[tuple[int, list[Cue], float]] = []
    for srt_p in sorted(sub_dir.glob("*_narration.srt")):
        m = _PER_SCENE_SRT_RE.match(srt_p.name)
        if not m:
            continue
        scene_num = int(m.group(1))
        wav_p = narration_path(s.workspace_root, chap, scene_num)
        if not wav_p.exists():
            # SRT만 있고 WAV가 없으면 스킵 (오디오 없는 scene은 챕터 SRT에 포함 못함)
            continue
        cues = parse_srt_cues(srt_p.read_text(encoding="utf-8"))
        dur = _wav_duration(wav_p)
        scene_data.append((scene_num, cues, dur))

    if not scene_data:
        raise HTTPException(status_code=404, detail="no per-scene SRT/WAV pairs found")

    scene_data.sort(key=lambda t: t[0])
    chapter_text = merge_scene_cues([(cues, dur) for _, cues, dur in scene_data])
    cp = chapter_srt_path(s.workspace_root, chap)
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(chapter_text, encoding="utf-8")
    return {
        "chapter": chap,
        "scene_count": len(scene_data),
        "url": f"/api/files/ch{chap}/subtitles_full",
    }


class ToPronunciationRequest(BaseModel):
    text: str = Field(..., max_length=5000)


class ToPronunciationResponse(BaseModel):
    text: str


@router.post("/to_pronunciation", response_model=ToPronunciationResponse)
async def to_pronunciation(req: ToPronunciationRequest) -> ToPronunciationResponse:
    """발음 사전 + 영문 대문자 약어 음역 + 연도(예: 1989년 → 천구백팔십구년) +
    숫자+단위(킬로미터/미터/분/초/도/원/퍼센트 등)를 한자어 수사로 변환."""
    if not req.text.strip():
        return ToPronunciationResponse(text="")
    s = settings_module.load()
    pmap = load_pronunciation_map(s.pronunciation_map_path)
    return ToPronunciationResponse(
        text=pmap.apply(req.text, spell_unknown_acronyms=True, convert_years=True)
    )


# ---------- 발음 사전 편집 (웹 UI) ----------

_DICT_DEFAULT_HEADER = """# voicewright 발음 사전
#
# 합성 직전에 텍스트의 약자·외래어를 한국어 발음으로 자동 치환합니다.
# Supertonic은 영문 약자를 음절 단위로 읽는 경향이 있어 (예: MOOC → "엠오오씨"),
# 자연스러운 발음을 원하면 여기에 등록해두세요.
#
# 동작 규칙
#   - 단어 경계(\\b) 매칭 — "MOOC"는 잡지만 "MOOCAR" 같은 합성어 일부는 안 잡힘
#   - SRT 자막에는 적용되지 않음 (자막에는 항상 원본 텍스트가 들어감)
#   - 사용자가 카드에서 직접 텍스트를 편집한 경우엔 편집 결과가 우선 (이중 변환은 자연스럽게 누적)
#   - 새 항목 추가 후엔 즉시 반영 (서버 재시작 불필요)
"""


def _yaml_quote(s: str) -> str:
    """필요할 때만 따옴표로 감싼다 (콜론·해시·앞공백·특수문자 포함 시)."""
    if not s:
        return '""'
    if any(c in s for c in (":", "#", "'", '"', "\n", "\t")) or s != s.strip():
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _read_dict_file(path: Path) -> tuple[str, dict[str, str]]:
    """YAML 파일에서 (헤더 주석, rules dict)를 분리해 읽는다.
    파일이 없거나 헤더가 없으면 기본 헤더를 쓴다."""
    if not path.exists():
        return _DICT_DEFAULT_HEADER, {}

    raw = path.read_text(encoding="utf-8")
    # "rules:" 첫 등장 라인을 기준으로 분할
    lines = raw.splitlines(keepends=True)
    rules_idx = next(
        (i for i, ln in enumerate(lines) if ln.lstrip().startswith("rules:")),
        None,
    )
    if rules_idx is None:
        header = _DICT_DEFAULT_HEADER
    else:
        header = "".join(lines[:rules_idx]).rstrip() + "\n"
        if not header.strip():
            header = _DICT_DEFAULT_HEADER

    try:
        data = yaml.safe_load(raw) or {}
        raw_rules = data.get("rules") or {}
    except Exception:
        raw_rules = {}

    rules: dict[str, str] = {}
    for k, v in raw_rules.items():
        key = str(k).strip()
        val = str(v).strip()
        if key and val:
            rules[key] = val
    return header, rules


def _write_dict_file(path: Path, header: str, rules: dict[str, str]) -> None:
    """카테고리 그룹 없이 키 정렬 순서로 dump (사용자가 UI에서 편집하므로 단순화)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    parts: list[str] = [header.rstrip("\n") + "\n\n", "rules:\n"]
    for k in sorted(rules.keys()):
        parts.append(f"  {_yaml_quote(k)}: {_yaml_quote(rules[k])}\n")
    path.write_text("".join(parts), encoding="utf-8")


class DictRule(BaseModel):
    key: str = Field(..., min_length=1, max_length=64)
    value: str = Field(..., min_length=1, max_length=128)


class DictResponse(BaseModel):
    rules: dict[str, str]
    count: int


@router.get("/dict", response_model=DictResponse)
async def dict_get() -> DictResponse:
    s = settings_module.load()
    _, rules = _read_dict_file(s.pronunciation_map_path)
    return DictResponse(rules=rules, count=len(rules))


@router.post("/dict", response_model=DictResponse)
async def dict_upsert(rule: DictRule) -> DictResponse:
    s = settings_module.load()
    header, rules = _read_dict_file(s.pronunciation_map_path)
    key = rule.key.strip()
    val = rule.value.strip()
    if not key or not val:
        raise HTTPException(status_code=400, detail="key/value must be non-empty")
    rules[key] = val
    _write_dict_file(s.pronunciation_map_path, header, rules)
    return DictResponse(rules=rules, count=len(rules))


@router.delete("/dict/{key}", response_model=DictResponse)
async def dict_delete(key: str) -> DictResponse:
    s = settings_module.load()
    header, rules = _read_dict_file(s.pronunciation_map_path)
    key = key.strip()
    if key not in rules:
        raise HTTPException(status_code=404, detail=f"key '{key}' not found")
    del rules[key]
    _write_dict_file(s.pronunciation_map_path, header, rules)
    return DictResponse(rules=rules, count=len(rules))


@router.post("/dict/preview", response_model=ToPronunciationResponse)
async def dict_preview(req: ToPronunciationRequest) -> ToPronunciationResponse:
    """사전 편집 화면용 — /to_pronunciation과 동일한 변환 미리보기."""
    if not req.text.strip():
        return ToPronunciationResponse(text="")
    s = settings_module.load()
    pmap = load_pronunciation_map(s.pronunciation_map_path)
    return ToPronunciationResponse(
        text=pmap.apply(req.text, spell_unknown_acronyms=True, convert_years=True)
    )


@router.get("/health")
async def health() -> dict:
    s = settings_module.load()
    info: dict = {
        "status": "ok",
        "use_gpu_mode": s.use_gpu_mode,
        "engine_loaded": Engine._instance is not None,
    }
    if Engine._instance is not None:
        info["providers"] = Engine._instance.providers
        info["sample_rate"] = Engine._instance.sample_rate
    return info
