from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import db_dep, role_dep
from app.core.settings import STORAGE_DIR
from app.db.models import NormalizedRecord, TaskRun, UploadJob, UploadedFile
from app.schemas.common import FileResponse, JobResponse
from app.services.config_service import ConfigService
from app.services.document_type_guard import TypeGuardMismatch, enforce_document_type_guard
from app.services.file_storage import compute_sha256, store_upload
from app.services.lifecycle_service import build_job_summary, is_review_queue_record
from app.services.normalize_service import get_core, is_effective_order_payload, normalize_record
from app.services.order_governance import apply_order_governance, should_auto_delete_duplicate_record
from app.services.order_governance import (
    LIFECYCLE_ACTIVE,
    LIFECYCLE_ARCHIVED,
    LIFECYCLE_RECYCLE_BIN,
    LIFECYCLE_SPECIAL_CASE,
)
from app.services.parsers import (
    IMAGE_EXTS,
    PDF_EXTS,
    TABULAR_EXTS,
    parse_document_fallback,
    parse_meta_json,
    parse_with_mapping,
)
from app.services.task_runner import process_task_compat

router = APIRouter(prefix="/upload-jobs", tags=["upload"])

ALLOWED_BY_DOC: dict[str, set[str]] = {
    "order": TABULAR_EXTS | PDF_EXTS,
    "shipment": TABULAR_EXTS,
    "payment_notice": PDF_EXTS | IMAGE_EXTS,
    "invoice": PDF_EXTS | IMAGE_EXTS,
}


def _normalize_batch_view(batch_view: str | None) -> str:
    raw = str(batch_view or "").strip().lower()
    if raw in {"", "all"}:
        return "all"
    if raw in {"non_empty", "active_only"}:
        return "non_empty"
    raise HTTPException(status_code=400, detail="Unsupported batch_view. Use all or non_empty.")


def _normalize_lifecycle_view(lifecycle_view: str | None) -> str:
    raw = str(lifecycle_view or "").strip().lower()
    if raw in {"", "all"}:
        return "all"
    if raw == "current":
        return "current"
    if raw in {"review_queue", "archived", "special_case", "recycle_bin"}:
        return raw
    raise HTTPException(
        status_code=400,
        detail="Unsupported lifecycle_view. Use all, current, review_queue, archived, special_case or recycle_bin.",
    )


def _normalize_business_view(business_view: str | None) -> str:
    raw = str(business_view or "").strip().lower()
    if raw in {"", "all"}:
        return "all"
    if raw in {"unshipped", "uninvoiced"}:
        return raw
    raise HTTPException(status_code=400, detail="Unsupported business_view. Use all, unshipped or uninvoiced.")


def _safe_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _job_summary_matches_lifecycle_view(item: dict[str, object], lifecycle_view: str) -> bool:
    if lifecycle_view == "all":
        return True
    if lifecycle_view == "current":
        if bool(item.get("is_empty_shell")):
            return True
        active_file_count = _safe_int(item.get("active_file_count"))
        active_record_count = _safe_int(item.get("active_record_count"))
        current_effective_record_count = _safe_int(item.get("current_effective_record_count"))
        review_queue_record_count = _safe_int(item.get("review_queue_record_count"))
        parse_failed_count = _safe_int(item.get("parse_failed_count"))
        if current_effective_record_count > 0:
            return True
        if active_record_count > review_queue_record_count:
            return True
        if active_record_count == 0 and active_file_count > 0:
            return True
        if parse_failed_count > 0:
            return True
        return False
    if lifecycle_view == "review_queue":
        return _safe_int(item.get("review_queue_record_count")) > 0
    if lifecycle_view == "archived":
        return _safe_int(item.get("archived_file_count")) > 0 or _safe_int(item.get("archived_record_count")) > 0
    if lifecycle_view == "special_case":
        return _safe_int(item.get("special_case_file_count")) > 0 or _safe_int(item.get("special_case_record_count")) > 0
    if lifecycle_view == "recycle_bin":
        return _safe_int(item.get("recycle_bin_file_count")) > 0 or _safe_int(item.get("recycle_bin_record_count")) > 0
    return True


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        text = str(value).replace(",", "").strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _numbers_equal(left: object, right: object) -> bool:
    left_num = _to_float(left)
    right_num = _to_float(right)
    if left_num is None or right_num is None:
        return False
    return abs(left_num - right_num) <= 1e-9


def _job_order_records_for_business_view(db: Session, *, job_id: str, lifecycle_view: str) -> list[NormalizedRecord]:
    base_query = (
        db.query(NormalizedRecord)
        .join(UploadedFile, UploadedFile.id == NormalizedRecord.file_id)
        .filter(
            NormalizedRecord.job_id == job_id,
            NormalizedRecord.document_type == "order",
        )
    )
    if lifecycle_view == "current":
        return (
            base_query.filter(
                NormalizedRecord.lifecycle_state == LIFECYCLE_ACTIVE,
                UploadedFile.lifecycle_state == LIFECYCLE_ACTIVE,
                NormalizedRecord.is_current_effective.is_(True),
            )
            .order_by(NormalizedRecord.created_at.asc(), NormalizedRecord.source_row.asc())
            .all()
        )
    if lifecycle_view == "review_queue":
        rows = (
            base_query.filter(
                NormalizedRecord.lifecycle_state == LIFECYCLE_ACTIVE,
                UploadedFile.lifecycle_state == LIFECYCLE_ACTIVE,
            )
            .order_by(NormalizedRecord.created_at.asc(), NormalizedRecord.source_row.asc())
            .all()
        )
        return [row for row in rows if is_review_queue_record(row)]
    if lifecycle_view == "archived":
        return (
            base_query.filter(NormalizedRecord.lifecycle_state == LIFECYCLE_ARCHIVED)
            .order_by(NormalizedRecord.created_at.asc(), NormalizedRecord.source_row.asc())
            .all()
        )
    if lifecycle_view == "special_case":
        return (
            base_query.filter(NormalizedRecord.lifecycle_state == LIFECYCLE_SPECIAL_CASE)
            .order_by(NormalizedRecord.created_at.asc(), NormalizedRecord.source_row.asc())
            .all()
        )
    if lifecycle_view == "recycle_bin":
        return (
            base_query.filter(NormalizedRecord.lifecycle_state == LIFECYCLE_RECYCLE_BIN)
            .order_by(NormalizedRecord.created_at.asc(), NormalizedRecord.source_row.asc())
            .all()
        )
    return base_query.order_by(NormalizedRecord.created_at.asc(), NormalizedRecord.source_row.asc()).all()


def _record_matches_business_view(record: NormalizedRecord, business_view: str) -> bool:
    if business_view == "all":
        return True
    core = get_core(record.payload_json)
    quantity = _to_float(core.get("quantity"))
    executed_shipped_qty = _to_float(core.get("executed_shipped_qty"))
    invoiced_qty = _to_float(core.get("invoiced_qty"))
    if business_view == "unshipped":
        if quantity is None or executed_shipped_qty is None:
            return False
        return not _numbers_equal(executed_shipped_qty, quantity)
    if business_view == "uninvoiced":
        if executed_shipped_qty is None or invoiced_qty is None:
            return False
        if executed_shipped_qty <= 0:
            return False
        return not _numbers_equal(invoiced_qty, executed_shipped_qty)
    return True


def _job_summary_matches_business_view(
    db: Session,
    *,
    job_id: str,
    lifecycle_view: str,
    business_view: str,
) -> bool:
    if business_view == "all":
        return True
    rows = _job_order_records_for_business_view(db, job_id=job_id, lifecycle_view=lifecycle_view)
    return any(_record_matches_business_view(row, business_view) for row in rows)


def _enqueue_auto_recompute(
    *,
    db: Session,
    background_tasks: BackgroundTasks,
    job_id: str,
    created_by: str,
    trigger_doc_type: str,
    trigger_file_id: str,
) -> None:
    task = TaskRun(
        job_id=job_id,
        task_type="orchestrate",
        status="queued",
        input_json={
            "job_id": job_id,
            "provider": "copaw",
            "trigger": "auto_document_upload",
            "trigger_doc_type": trigger_doc_type,
            "trigger_file_id": trigger_file_id,
        },
        created_by=created_by[:20],
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    background_tasks.add_task(process_task_compat, task.id)


@router.post("", response_model=JobResponse)
def create_upload_job(db: Session = Depends(db_dep), role: str = Depends(role_dep)):
    job = UploadJob(status="created", created_by=role)
    db.add(job)
    db.commit()
    db.refresh(job)
    return JobResponse(id=job.id, status=job.status, created_by=job.created_by, created_at=job.created_at)


@router.get("", response_model=list[JobResponse])
def list_upload_jobs(db: Session = Depends(db_dep), role: str = Depends(role_dep)):
    _ = role
    jobs = db.query(UploadJob).order_by(UploadJob.created_at.desc()).all()
    return [
        JobResponse(id=j.id, status=j.status, created_by=j.created_by, created_at=j.created_at)
        for j in jobs
    ]


@router.get("/summary")
def list_upload_job_summaries(
    batch_view: str | None = Query(default="all"),
    lifecycle_view: str | None = Query(default="all"),
    business_view: str | None = Query(default="all"),
    db: Session = Depends(db_dep),
    role: str = Depends(role_dep),
):
    _ = role
    view = _normalize_batch_view(batch_view)
    normalized_lifecycle_view = _normalize_lifecycle_view(lifecycle_view)
    normalized_business_view = _normalize_business_view(business_view)
    jobs = db.query(UploadJob).order_by(UploadJob.created_at.desc()).all()
    items = [build_job_summary(db, job) for job in jobs]
    items = [item for item in items if _job_summary_matches_lifecycle_view(item, normalized_lifecycle_view)]
    if view == "non_empty":
        items = [item for item in items if not item.get("is_empty_shell")]
    items = [
        item
        for item in items
        if _job_summary_matches_business_view(
            db,
            job_id=str(item.get("job_id") or ""),
            lifecycle_view=normalized_lifecycle_view,
            business_view=normalized_business_view,
        )
    ]
    return {
        "total": len(items),
        "items": items,
        "batch_view": view,
        "lifecycle_view": normalized_lifecycle_view,
        "business_view": normalized_business_view,
    }


@router.post("/{job_id}/files", response_model=FileResponse)
def upload_file(
    job_id: str,
    background_tasks: BackgroundTasks,
    document_type: str = Form(...),
    metadata_json: str | None = Form(default=None),
    upload: UploadFile = File(...),
    db: Session = Depends(db_dep),
    role: str = Depends(role_dep),
):
    _ = role
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
        if doc_type in {"payment_notice", "invoice"}:
            mapping = mappings.get(doc_type, {})
            parsed = parse_with_mapping(stored_path, mapping)
        else:
            mapping = mappings.get(doc_type, {})
            parsed = parse_with_mapping(stored_path, mapping)
    else:
        parsed = parsed_override or parse_document_fallback(stored_path, meta)

    if parsed.error:
        f.parse_status = "failed"
        f.parse_error = parsed.error
        db.commit()
        db.refresh(f)
        return FileResponse(
            id=f.id,
            job_id=f.job_id,
            document_type=f.document_type,
            filename=f.filename,
            parse_status=f.parse_status,
            parse_error=f.parse_error,
            parsed_count=f.parsed_count,
        )

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

    if f.parse_status == "parsed" and f.parsed_count > 0 and doc_type in {"order", "payment_notice", "invoice"}:
        _enqueue_auto_recompute(
            db=db,
            background_tasks=background_tasks,
            job_id=job_id,
            created_by=role,
            trigger_doc_type=doc_type,
            trigger_file_id=f.id,
        )

    return FileResponse(
        id=f.id,
        job_id=f.job_id,
        document_type=f.document_type,
        filename=f.filename,
        parse_status=f.parse_status,
        parse_error=f.parse_error,
        parsed_count=f.parsed_count,
    )
