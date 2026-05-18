from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.settings import STORAGE_DIR
from app.db.models import ExternalRef, NormalizedRecord, TaskRun, UploadJob, UploadedFile
from app.services.config_service import ConfigService
from app.services.document_type_guard import TypeGuardMismatch, enforce_document_type_guard
from app.services.file_storage import compute_sha256, store_upload
from app.services.normalize_service import is_effective_order_payload, normalize_record
from app.services.order_governance import apply_order_governance, should_auto_delete_duplicate_record
from app.services.parsers import (
    IMAGE_EXTS,
    PDF_EXTS,
    TABULAR_EXTS,
    parse_document_fallback,
    parse_meta_json,
    parse_with_mapping,
)


ALLOWED_BY_DOC: dict[str, set[str]] = {
    "order": TABULAR_EXTS | PDF_EXTS,
    "shipment": TABULAR_EXTS,
    "payment_notice": PDF_EXTS | IMAGE_EXTS,
    "invoice": PDF_EXTS | IMAGE_EXTS,
}


def create_external_job(
    db: Session,
    *,
    client_id: str,
    provider: str,
    request_id: str,
    source_ref: str | None,
    metadata: dict[str, Any] | None = None,
) -> tuple[UploadJob, bool]:
    existing = (
        db.query(ExternalRef)
        .filter(
            ExternalRef.client_id == client_id,
            ExternalRef.request_id == request_id,
            ExternalRef.direction == "inbound",
        )
        .order_by(ExternalRef.created_at.desc())
        .first()
    )
    if existing and existing.job_id:
        job = db.get(UploadJob, existing.job_id)
        if job:
            return job, True

    created_by = f"ext:{client_id}"[:20]
    job = UploadJob(status="created", created_by=created_by)
    db.add(job)
    db.flush()

    db.add(
        ExternalRef(
            job_id=job.id,
            client_id=client_id,
            provider=provider,
            request_id=request_id,
            source_ref=source_ref,
            direction="inbound",
            meta_json=metadata or {},
        )
    )
    db.commit()
    db.refresh(job)
    return job, False


def upload_file_for_job(
    db: Session,
    *,
    job_id: str,
    document_type: str,
    upload: UploadFile,
    metadata_json: str | None,
) -> UploadedFile:
    job = db.get(UploadJob, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload job not found")

    doc_type = document_type.strip().lower()
    if doc_type not in ALLOWED_BY_DOC:
        raise HTTPException(status_code=400, detail=f"Unsupported document_type: {doc_type}")

    suffix = Path(upload.filename).suffix.lower()
    if suffix not in ALLOWED_BY_DOC[doc_type]:
        raise HTTPException(
            status_code=400,
            detail=f"File extension {suffix} not allowed for {doc_type}. Allowed: {sorted(ALLOWED_BY_DOC[doc_type])}",
        )

    stored_path = store_upload(job_id, upload)
    try:
        storage_key = str(stored_path.relative_to(STORAGE_DIR))
    except Exception:
        storage_key = stored_path.name
    try:
        file_size = int(stored_path.stat().st_size)
    except Exception:
        file_size = 0
    try:
        file_hash_sha256 = compute_sha256(stored_path)
    except Exception:
        file_hash_sha256 = None
    meta = parse_meta_json(metadata_json)

    cfg = ConfigService(db)
    mappings = cfg.get("field_mappings")
    mapping = mappings.get(doc_type, {})

    try:
        guard_warning, parsed_override = enforce_document_type_guard(
            expected_type=doc_type,
            path=stored_path,
            field_mappings=mappings,
            meta_json=meta,
        )
    except TypeGuardMismatch as exc:
        try:
            stored_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise HTTPException(status_code=422, detail=exc.detail)
    if guard_warning:
        meta["_type_guard_warning"] = guard_warning

    f = UploadedFile(
        job_id=job_id,
        document_type=doc_type,
        filename=upload.filename,
        content_type=upload.content_type or "application/octet-stream",
        storage_path=str(stored_path),
        storage_key=storage_key,
        file_size=file_size,
        file_hash_sha256=file_hash_sha256,
        meta_json=meta,
        parse_status="parsing",
    )
    db.add(f)
    db.commit()
    db.refresh(f)

    if suffix in TABULAR_EXTS:
        parsed = parse_with_mapping(stored_path, mapping)
    else:
        parsed = parsed_override or parse_document_fallback(stored_path, meta)

    if parsed.error:
        f.parse_status = "failed"
        f.parse_error = parsed.error
        db.commit()
        db.refresh(f)
        return f

    count = 0
    for idx, row in enumerate(parsed.rows, start=1):
        normalized = normalize_record(
            document_type=doc_type,
            parsed_row=row,
            file_id=f.id,
            source_row=idx,
            filename=f.filename,
            source_columns=parsed.source_columns,
        )
        if doc_type == "order" and not is_effective_order_payload(normalized):
            continue
        record = NormalizedRecord(
            job_id=job_id,
            file_id=f.id,
            document_type=doc_type,
            source_row=idx,
            order_unshipped_qty=((normalized.get("core") or {}).get("order_unshipped_qty") if doc_type == "order" else None),
            payload_json=normalized,
        )
        db.add(record)
        db.flush()
        if doc_type == "order":
            decision = apply_order_governance(db, record)
            if should_auto_delete_duplicate_record(decision):
                f.auto_deleted_duplicate_count = int(f.auto_deleted_duplicate_count or 0) + 1
                db.delete(record)
                continue
        count += 1

    f.parse_status = "parsed"
    f.parsed_count = count
    job.status = "files_uploaded"
    db.commit()
    db.refresh(f)
    return f


def start_job_task(
    db: Session,
    *,
    job_id: str,
    client_id: str,
    created_by: str,
    provider_id: str,
    request_id: str,
    source_ref: str | None,
) -> TaskRun:
    job = db.get(UploadJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Upload job not found")

    existing = (
        db.query(ExternalRef)
        .filter(
            ExternalRef.client_id == client_id,
            ExternalRef.request_id == request_id,
            ExternalRef.direction == "outbound",
        )
        .order_by(ExternalRef.created_at.desc())
        .first()
    )
    if existing and existing.task_id:
        task = db.get(TaskRun, existing.task_id)
        if task:
            return task

    task = TaskRun(
        job_id=job_id,
        task_type="orchestrate",
        status="queued",
        input_json={
            "job_id": job_id,
            "provider": provider_id,
            "request_id": request_id,
            "source_ref": source_ref,
        },
        created_by=created_by[:20],
    )
    db.add(task)
    db.flush()

    db.add(
        ExternalRef(
            job_id=job_id,
            task_id=task.id,
            client_id=client_id,
            provider=provider_id,
            request_id=request_id,
            source_ref=source_ref,
            direction="outbound",
            meta_json={},
        )
    )
    db.commit()
    db.refresh(task)
    return task
