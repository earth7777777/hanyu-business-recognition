from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

from fastapi import UploadFile

from app.core.settings import STORAGE_DIR


def _safe_filename(name: str) -> str:
    base = Path(name or "").name.strip()
    if not base:
        return "upload.bin"
    return base.replace("/", "_").replace("\\", "_")


def store_upload(job_id: str, upload: UploadFile, *, storage_name: str | None = None) -> Path:
    job_dir = STORAGE_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    original = _safe_filename(upload.filename or "")
    if storage_name:
        target_name = storage_name
    else:
        target_name = f"{uuid.uuid4().hex}__{original}"
    target = job_dir / target_name
    with target.open("wb") as f:
        shutil.copyfileobj(upload.file, f)
    return target


def compute_sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()
