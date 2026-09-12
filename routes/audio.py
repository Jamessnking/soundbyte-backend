"""
SoundByte audio processing endpoints.

All endpoints accept a multipart upload, run ffmpeg on the container,
and stream back an MP3 (audio/mpeg). Temp files are cleaned up in
BackgroundTasks so streaming isn't blocked.

Endpoints:
- POST /api/audio/extract   -> Video -> MP3
- POST /api/audio/trim      -> Audio/Video trimmed to [start, end] as MP3
- POST /api/audio/merge     -> N audio files concatenated to a single MP3
- GET  /api/audio/health    -> FFmpeg presence + version
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter(prefix="/audio", tags=["audio"])
logger = logging.getLogger(__name__)

# 100 MB per upload matches the client-side guard.
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
# 6 clips max for merge, matches the UI.
MAX_MERGE_CLIPS = 6
# Absolute ffmpeg cap so runaway inputs can't lock a worker forever.
FFMPEG_TIMEOUT_SECONDS = 180

FFMPEG_BIN = shutil.which("ffmpeg") or "ffmpeg"


def _job_dir() -> Path:
    """Fresh working dir per job so parallel uploads never collide."""
    root = Path(tempfile.gettempdir()) / "soundbyte" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cleanup(path: Path) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:  # pragma: no cover - best effort
        logger.warning("Failed to cleanup job dir %s", path)


async def _save_upload(upload: UploadFile, dst: Path) -> int:
    """Stream an UploadFile to disk, enforcing MAX_UPLOAD_BYTES."""
    size = 0
    with dst.open("wb") as f:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"File exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit",
                )
            f.write(chunk)
    if size == 0:
        raise HTTPException(status_code=400, detail="Empty upload")
    return size


def _run_ffmpeg(args: List[str]) -> None:
    """Invoke ffmpeg synchronously with a hard timeout."""
    cmd = [FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error", *args]
    logger.info("ffmpeg %s", " ".join(cmd[1:]))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(
            status_code=504,
            detail="Audio processing timed out",
        ) from exc

    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        logger.error("ffmpeg failed: %s", stderr[-500:])
        raise HTTPException(
            status_code=422,
            detail=f"FFmpeg error: {stderr[-300:] or 'unknown failure'}",
        )


def _mp3_response(path: Path, filename: str, background: BackgroundTasks, job: Path) -> FileResponse:
    background.add_task(_cleanup, job)
    return FileResponse(
        path,
        media_type="audio/mpeg",
        filename=filename,
        headers={"Cache-Control": "no-store"},
    )


@router.get("/health")
async def audio_health() -> JSONResponse:
    version = "unknown"
    try:
        out = subprocess.run(
            [FFMPEG_BIN, "-version"], capture_output=True, timeout=5, check=False
        )
        if out.returncode == 0:
            version = out.stdout.decode("utf-8", errors="replace").splitlines()[0]
    except Exception:  # pragma: no cover
        pass
    return JSONResponse({"ok": True, "ffmpeg": version})


@router.post("/extract")
async def extract_audio(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    bitrate: str = Form("192k"),
) -> FileResponse:
    """Extract audio from an uploaded video and return a 192 kbps MP3."""
    job = _job_dir()
    try:
        src = job / f"input{Path(file.filename or 'input').suffix or ''}"
        out = job / "extracted.mp3"
        await _save_upload(file, src)
        _run_ffmpeg(
            [
                "-i",
                str(src),
                "-vn",              # drop video
                "-acodec",
                "libmp3lame",
                "-b:a",
                bitrate,
                "-ac",
                "2",                # stereo
                "-ar",
                "44100",            # 44.1 kHz
                str(out),
            ]
        )
        return _mp3_response(out, "soundbyte-extract.mp3", background, job)
    except HTTPException:
        _cleanup(job)
        raise
    except Exception as exc:
        _cleanup(job)
        logger.exception("extract failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/trim")
async def trim_audio(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    start: float = Form(0.0),
    end: Optional[float] = Form(None),
    bitrate: str = Form("192k"),
) -> FileResponse:
    """Trim [start, end] seconds from the upload and return an MP3."""
    if start < 0:
        raise HTTPException(status_code=400, detail="start must be >= 0")
    if end is not None and end <= start:
        raise HTTPException(status_code=400, detail="end must be greater than start")

    job = _job_dir()
    try:
        src = job / f"input{Path(file.filename or 'input').suffix or ''}"
        out = job / "trimmed.mp3"
        await _save_upload(file, src)

        args: List[str] = ["-ss", f"{start}", "-i", str(src)]
        if end is not None:
            args += ["-t", f"{max(0.1, end - start)}"]
        args += [
            "-vn",
            "-acodec",
            "libmp3lame",
            "-b:a",
            bitrate,
            "-ac",
            "2",
            "-ar",
            "44100",
            str(out),
        ]
        _run_ffmpeg(args)
        return _mp3_response(out, "soundbyte-trim.mp3", background, job)
    except HTTPException:
        _cleanup(job)
        raise
    except Exception as exc:
        _cleanup(job)
        logger.exception("trim failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/merge")
async def merge_audio(
    background: BackgroundTasks,
    files: List[UploadFile] = File(...),
    bitrate: str = Form("192k"),
) -> FileResponse:
    """Concatenate two or more audio files into a single MP3.

    We re-encode each clip through the concat demuxer so mismatched
    sample rates / codecs still line up cleanly.
    """
    if len(files) < 2:
        raise HTTPException(status_code=400, detail="Merge needs at least 2 clips")
    if len(files) > MAX_MERGE_CLIPS:
        raise HTTPException(
            status_code=400,
            detail=f"Merge supports up to {MAX_MERGE_CLIPS} clips",
        )

    job = _job_dir()
    try:
        # 1. Save uploads to disk in the order provided.
        saved: List[Path] = []
        for idx, upload in enumerate(files):
            suffix = Path(upload.filename or f"clip{idx}").suffix or ".bin"
            dst = job / f"clip-{idx:02d}{suffix}"
            await _save_upload(upload, dst)
            saved.append(dst)

        # 2. Normalise every clip to the same PCM layout via a first pass so
        #    the concat demuxer never sees codec/sample-rate mismatches.
        normalised: List[Path] = []
        for i, src in enumerate(saved):
            norm = job / f"norm-{i:02d}.mp3"
            _run_ffmpeg(
                [
                    "-i",
                    str(src),
                    "-vn",
                    "-acodec",
                    "libmp3lame",
                    "-b:a",
                    bitrate,
                    "-ac",
                    "2",
                    "-ar",
                    "44100",
                    str(norm),
                ]
            )
            normalised.append(norm)

        # 3. Build a concat list file and do a stream copy for the join.
        list_file = job / "concat.txt"
        with list_file.open("w") as f:
            for p in normalised:
                # concat demuxer requires POSIX-safe paths and escaped quotes.
                safe = str(p).replace("'", "'\\''")
                f.write(f"file '{safe}'\n")

        out = job / "merged.mp3"
        _run_ffmpeg(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_file),
                "-c",
                "copy",
                str(out),
            ]
        )
        return _mp3_response(out, "soundbyte-merge.mp3", background, job)
    except HTTPException:
        _cleanup(job)
        raise
    except Exception as exc:
        _cleanup(job)
        logger.exception("merge failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
