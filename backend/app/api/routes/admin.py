from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import FileResponse as FastAPIFileResponse
from sqlalchemy import and_, exists, func
from sqlalchemy.orm import Session

from app.api.deps import db_dep, role_dep
from app.core.security import require_admin
from app.core.settings import STORAGE_DIR
from app.db.models import Alert, GroupRecordLink, NormalizedRecord, TaskRun, UploadJob, UploadedFile
from app.services.config_service import ConfigService
from app.services.backup_service import run_database_backup_now, run_file_backup_now
from app.services.log_retention_service import run_log_cleanup_now
from app.services.restore_drill_service import run_restore_drill_now
from app.services.lifecycle_service import (
    archive_file,
    archive_record,
    build_archived_items,
    build_auto_archive_preview,
    build_file_anomaly_details,
    build_file_review_counts,
    build_file_hard_delete_preview,
    build_file_impact,
    build_job_summary,
    build_operations_summary,
    build_record_hard_delete_preview,
    build_record_impact,
    build_recycle_bin_items,
    build_review_queue_reason,
    build_review_queue_items,
    build_special_case_items,
    effective_status_code,
    execute_manual_archive_preview,
    hard_delete_file,
    hard_delete_record,
    is_review_queue_record,
    move_record_to_special_case,
    normalize_archive_mode,
    return_file_to_review_queue_from_special_case,
    return_file_to_job_list_from_special_case,
    return_record_to_review_queue_from_special_case,
    return_record_to_job_list_from_special_case,
    review_status_code,
    restore_file,
    restore_record,
    special_case_source_code_for_file,
    special_case_source_code_for_record,
    soft_delete_file as move_file_to_recycle_bin,
    soft_delete_record as move_record_to_recycle_bin,
    update_archive_preview_runtime_status,
    update_archive_run_runtime_status,
    validate_special_case_reason,
)
from app.services.order_governance import (
    COMPARE_FIELDS,
    DELETE_ORIGIN_MANUAL_FILE,
    DELETE_ORIGIN_MANUAL_RECORD,
    LIFECYCLE_ACTIVE,
    LIFECYCLE_ARCHIVED,
    LIFECYCLE_RECYCLE_BIN,
    LIFECYCLE_SPECIAL_CASE,
    file_lifecycle_state,
    get_governance,
    is_current_effective_record,
    record_lifecycle_state,
    record_version_status,
)
from app.services.task_runner import process_task_compat

router = APIRouter(prefix="/admin", tags=["admin-audit"])

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
_TABULAR_EXTS = {".csv", ".xls", ".xlsx"}
_TEXT_EXTS = {".txt", ".json", ".log", ".md"}
_HARD_DELETE_PREVIEW_TTL = timedelta(minutes=10)
_HARD_DELETE_PREVIEW_TOKENS: dict[str, dict[str, Any]] = {}
_ARCHIVE_PREVIEW_TTL = timedelta(minutes=10)
_ARCHIVE_PREVIEW_TOKENS: dict[str, dict[str, Any]] = {}


def _purge_expired_hard_delete_preview_tokens(now: datetime | None = None) -> None:
    current = now or datetime.now(timezone.utc)
    expired = [
        token
        for token, payload in _HARD_DELETE_PREVIEW_TOKENS.items()
        if not isinstance(payload.get("expires_at"), datetime) or payload["expires_at"] <= current
    ]
    for token in expired:
        _HARD_DELETE_PREVIEW_TOKENS.pop(token, None)


def _issue_hard_delete_preview_token(object_type: str, object_id: str) -> dict[str, Any]:
    _purge_expired_hard_delete_preview_tokens()
    token = uuid4().hex
    expires_at = datetime.now(timezone.utc) + _HARD_DELETE_PREVIEW_TTL
    _HARD_DELETE_PREVIEW_TOKENS[token] = {
        "object_type": object_type,
        "object_id": object_id,
        "expires_at": expires_at,
    }
    return {
        "preview_token": token,
        "preview_expires_at": expires_at.isoformat(),
    }


def _consume_hard_delete_preview_token(token: str | None, *, object_type: str, object_id: str) -> bool:
    _purge_expired_hard_delete_preview_tokens()
    key = str(token or "").strip()
    if not key:
        return False
    payload = _HARD_DELETE_PREVIEW_TOKENS.get(key)
    if not isinstance(payload, dict):
        return False
    if payload.get("object_type") != object_type or payload.get("object_id") != object_id:
        return False
    _HARD_DELETE_PREVIEW_TOKENS.pop(key, None)
    return True


def _purge_expired_archive_preview_tokens(now: datetime | None = None) -> None:
    current = now or datetime.now(timezone.utc)
    expired = [
        token
        for token, payload in _ARCHIVE_PREVIEW_TOKENS.items()
        if not isinstance(payload.get("expires_at"), datetime) or payload["expires_at"] <= current
    ]
    for token in expired:
        _ARCHIVE_PREVIEW_TOKENS.pop(token, None)


def _issue_archive_preview_token(record_ids: list[str]) -> dict[str, Any]:
    _purge_expired_archive_preview_tokens()
    token = uuid4().hex
    expires_at = datetime.now(timezone.utc) + _ARCHIVE_PREVIEW_TTL
    _ARCHIVE_PREVIEW_TOKENS[token] = {
        "record_ids": [str(record_id or "").strip() for record_id in record_ids if str(record_id or "").strip()],
        "expires_at": expires_at,
    }
    return {
        "preview_token": token,
        "preview_expires_at": expires_at.isoformat(),
    }


def _peek_archive_preview_token(token: str | None) -> dict[str, Any] | None:
    _purge_expired_archive_preview_tokens()
    key = str(token or "").strip()
    if not key:
        return None
    payload = _ARCHIVE_PREVIEW_TOKENS.get(key)
    return payload if isinstance(payload, dict) else None


def _consume_archive_preview_token(token: str | None) -> None:
    _purge_expired_archive_preview_tokens()
    key = str(token or "").strip()
    if key:
        _ARCHIVE_PREVIEW_TOKENS.pop(key, None)


def _normalize_batch_view(batch_view: str | None) -> str:
    raw = str(batch_view or "").strip().lower()
    if raw in {"", "all"}:
        return "all"
    if raw in {"non_empty", "active_only"}:
        return "non_empty"
    raise HTTPException(status_code=400, detail="Unsupported batch_view. Use all or non_empty.")


def _normalize_lifecycle_view(view: str | None, *, include_deleted: bool = False) -> str:
    normalized = str(view or "current").strip().lower()
    if include_deleted and normalized == "current":
        return "all"
    if normalized in {"current", "recycle_bin", "archived", "review_queue", "special_case", "all"}:
        return normalized
    return "current"


def _apply_file_lifecycle_view(query, lifecycle_view: str):
    if lifecycle_view == "current":
        return query.filter(UploadedFile.lifecycle_state == LIFECYCLE_ACTIVE)
    if lifecycle_view == "recycle_bin":
        return query.filter(UploadedFile.lifecycle_state == LIFECYCLE_RECYCLE_BIN)
    if lifecycle_view == "archived":
        return query.filter(UploadedFile.lifecycle_state == LIFECYCLE_ARCHIVED)
    if lifecycle_view == "special_case":
        return query.filter(UploadedFile.lifecycle_state == LIFECYCLE_SPECIAL_CASE)
    return query


def _apply_record_lifecycle_view(query, lifecycle_view: str):
    if lifecycle_view == "current":
        return query.filter(
            NormalizedRecord.lifecycle_state == LIFECYCLE_ACTIVE,
            UploadedFile.lifecycle_state == LIFECYCLE_ACTIVE,
        )
    if lifecycle_view == "recycle_bin":
        return query.filter(NormalizedRecord.lifecycle_state == LIFECYCLE_RECYCLE_BIN)
    if lifecycle_view == "archived":
        return query.filter(NormalizedRecord.lifecycle_state == LIFECYCLE_ARCHIVED)
    if lifecycle_view == "special_case":
        return query.filter(NormalizedRecord.lifecycle_state == LIFECYCLE_SPECIAL_CASE)
    return query


def _enqueue_lifecycle_recompute(
    *,
    db: Session,
    background_tasks: BackgroundTasks,
    job_ids: list[str],
    created_by: str,
    trigger: str,
    object_type: str,
    object_id: str,
) -> list[str]:
    created_task_ids: list[str] = []
    for job_id in sorted({jid for jid in job_ids if jid}):
        task = TaskRun(
            job_id=job_id,
            task_type="orchestrate",
            status="queued",
            input_json={
                "job_id": job_id,
                "provider": "copaw",
                "trigger": trigger,
                "trigger_object_type": object_type,
                "trigger_object_id": object_id,
            },
            created_by=created_by[:20],
        )
        db.add(task)
        db.flush()
        background_tasks.add_task(process_task_compat, task.id)
        created_task_ids.append(task.id)
    return created_task_ids


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_admin(role: str = Depends(role_dep)) -> str:
    require_admin(role)
    return role


def _body_reason(payload: dict[str, Any]) -> str | None:
    value = str(payload.get("reason") or "").strip()
    return value[:1000] if value else None


def _require_reason(payload: dict[str, Any]) -> str:
    reason = _body_reason(payload)
    if not reason:
        raise HTTPException(status_code=400, detail="reason is required")
    return reason


def _suffix_for_file(f: UploadedFile) -> str:
    if f.filename:
        return Path(f.filename).suffix.lower()
    if f.storage_key:
        return Path(f.storage_key).suffix.lower()
    if f.storage_path:
        return Path(f.storage_path).suffix.lower()
    return ""


def _preview_kind_for_file(f: UploadedFile) -> str:
    suffix = _suffix_for_file(f)
    if suffix in _IMAGE_EXTS:
        return "image"
    if suffix == ".pdf":
        return "pdf"
    if suffix in _TABULAR_EXTS:
        return "tabular"
    if suffix in _TEXT_EXTS:
        return "text"
    return "binary"


def _resolve_storage_path(f: UploadedFile) -> Path:
    if f.storage_path:
        p = Path(f.storage_path)
        if p.exists():
            return p
    if f.storage_key:
        p = STORAGE_DIR / f.storage_key
        if p.exists():
            return p
    if f.filename:
        p = STORAGE_DIR / f.job_id / f.filename
        if p.exists():
            return p
    raise HTTPException(status_code=404, detail="Stored file content not found on disk.")


def _normalize_hash(value: str | None) -> str:
    return str(value or "").strip().lower()


def _get_record_core(record: NormalizedRecord) -> dict[str, Any]:
    payload = record.payload_json or {}
    core = payload.get("core", {}) if isinstance(payload, dict) else {}
    return core if isinstance(core, dict) else {}


def _record_scan_state(record: NormalizedRecord) -> str | None:
    value = _get_record_core(record).get("scan_state")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _scan_stats(records: list[NormalizedRecord]) -> dict[str, int]:
    total = len(records)
    skip = sum(1 for record in records if _record_scan_state(record) == "completed_skip_scan")
    return {
        "total_record_count": int(total),
        "skip_scan_count": int(skip),
        "effective_scan_count": int(max(total - skip, 0)),
    }


def _fmt_scan_reason_value(value: Any) -> str:
    if value is None:
        return "空"
    text = str(value).strip()
    return text if text else "空"


def _record_scan_reason(record: NormalizedRecord) -> str:
    if record.document_type != "order":
        return "当前单据类型不参与 scan_state 计算。"

    core = _get_record_core(record)
    quantity = _fmt_scan_reason_value(core.get("quantity"))
    executed = _fmt_scan_reason_value(core.get("executed_shipped_qty"))
    uninvoiced = _fmt_scan_reason_value(core.get("uninvoiced_qty"))
    if _record_scan_state(record) == "completed_skip_scan":
        return (
            "已完成，跳过扫描："
            f"行已执行已出库数量={executed}，数量={quantity}，行未开票数量={uninvoiced}。"
        )
    return (
        "未命中跳过扫描条件，仍参与提醒判断："
        f"行已执行已出库数量={executed}，数量={quantity}，行未开票数量={uninvoiced}。"
    )


def _normalized_record_item(record: NormalizedRecord) -> dict[str, Any]:
    core = _get_record_core(record)
    governance = get_governance(record.payload_json or {})
    return {
        "record_id": record.id,
        "file_id": record.file_id,
        "job_id": record.job_id,
        "document_type": record.document_type,
        "source_row": int(record.source_row or 0),
        "scan_state": _record_scan_state(record),
        "scan_reason": _record_scan_reason(record),
        "customer_order_no": core.get("customer_order_no"),
        "entry_line_no": core.get("entry_line_no"),
        "biz_date": core.get("biz_date"),
        "item_name": core.get("item_name"),
        "item_code": core.get("item_code"),
        "quantity": core.get("quantity"),
        "executed_shipped_qty": core.get("executed_shipped_qty"),
        "order_unshipped_qty": record.order_unshipped_qty,
        "invoiced_qty": core.get("invoiced_qty"),
        "uninvoiced_qty": core.get("uninvoiced_qty"),
        "due_date": core.get("due_date"),
        "latest_outbound_date": core.get("latest_outbound_date"),
        "change_type": governance.get("change_type"),
        "identity_mode": governance.get("identity_mode"),
        "lifecycle_state": record_lifecycle_state(record),
        "version_status": record_version_status(record),
        "review_status": review_status_code(record),
        "effective_status": effective_status_code(record),
        "is_current_effective": is_current_effective_record(record),
        "duplicate_of_record_id": record.duplicate_of_record_id,
        "superseded_by_record_id": record.superseded_by_record_id,
        "supersedes_record_id": record.supersedes_record_id,
        "governance_reason": governance.get("governance_reason"),
        "delete_origin": record.delete_origin,
        "deleted_at": record.deleted_at,
        "deleted_by": record.deleted_by,
        "delete_reason": record.delete_reason,
        "restored_at": record.restored_at,
        "restored_by": record.restored_by,
        "restore_reason": record.restore_reason,
        "archived_at": record.archived_at,
        "archived_by": record.archived_by,
        "archive_reason": record.archive_reason,
        "special_case_source": special_case_source_code_for_record(record) if record_lifecycle_state(record) == LIFECYCLE_SPECIAL_CASE else None,
    }


def _build_review_queue_reason_payload(record: NormalizedRecord) -> dict[str, Any]:
    reason = build_review_queue_reason(record)
    return {
        "review_record": _normalized_record_item(record),
        "reason_code": reason["reason_code"],
        "reason_label": reason["reason_label"],
        "governance_reason": reason["governance_reason"],
        "missing_required_columns": reason["missing_required_columns"],
        "blank_identity_values": reason["blank_identity_values"],
    }


def _duplicate_risk_code(db: Session, f: UploadedFile) -> str:
    file_hash = _normalize_hash(f.file_hash_sha256)
    if not file_hash:
        return "not_checked"

    active_dup_q = db.query(UploadedFile.id, UploadedFile.job_id).filter(
        UploadedFile.file_hash_sha256 == file_hash,
        UploadedFile.id != f.id,
        UploadedFile.lifecycle_state == LIFECYCLE_ACTIVE,
    )
    if active_dup_q.filter(UploadedFile.job_id == f.job_id).first():
        return "same_job"
    if active_dup_q.first():
        return "global"
    return "none"


def _group_ids_for_records(record_ids: list[str], db: Session) -> list[str]:
    if not record_ids:
        return []
    return [
        gid
        for (gid,) in (
            db.query(GroupRecordLink.group_id)
            .filter(GroupRecordLink.record_id.in_(record_ids))
            .distinct()
            .all()
        )
    ]


def _coerce_preview_value(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


@router.get("/jobs")
def list_jobs(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    job_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    doc_type: str | None = Query(default=None),
    parse_status: str | None = Query(default=None),
    task_status: str | None = Query(default=None),
    alert_status: str | None = Query(default=None),
    batch_view: str | None = Query(default="all"),
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    _ = role
    normalized_batch_view = _normalize_batch_view(batch_view)
    q = db.query(UploadJob)

    if job_id:
        q = q.filter(UploadJob.id.contains(job_id.strip()))
    if status:
        q = q.filter(UploadJob.status == status.strip())
    if date_from:
        q = q.filter(UploadJob.created_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to:
        q = q.filter(UploadJob.created_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
    if doc_type:
        q = q.filter(
            exists().where(
                and_(
                    UploadedFile.job_id == UploadJob.id,
                    UploadedFile.document_type == doc_type.strip().lower(),
                    UploadedFile.lifecycle_state == LIFECYCLE_ACTIVE,
                )
            )
        )
    if parse_status:
        q = q.filter(
            exists().where(
                and_(
                    UploadedFile.job_id == UploadJob.id,
                    UploadedFile.parse_status == parse_status.strip().lower(),
                    UploadedFile.lifecycle_state == LIFECYCLE_ACTIVE,
                )
            )
        )
    if task_status:
        q = q.filter(exists().where(and_(TaskRun.job_id == UploadJob.id, TaskRun.status == task_status.strip().lower())))
    if alert_status:
        q = q.filter(exists().where(and_(Alert.job_id == UploadJob.id, Alert.status == alert_status.strip().lower())))

    rows = q.order_by(UploadJob.created_at.desc()).all()
    items = [build_job_summary(db, j) for j in rows]
    if normalized_batch_view == "non_empty":
        items = [item for item in items if not item.get("is_empty_shell")]
    total = len(items)
    paged_items = items[(page - 1) * size : page * size]
    return {"page": page, "size": size, "total": total, "items": paged_items, "batch_view": normalized_batch_view}


@router.get("/operations/summary")
def get_operations_summary(db: Session = Depends(db_dep), role: str = Depends(_ensure_admin)):
    _ = role
    config = ConfigService(db)
    monitoring_policy = config.get("operations_monitoring_policy")
    runtime_status = config.get("operations_runtime_status")
    retention_policy = config.get("data_retention_policy")
    return build_operations_summary(
        db,
        monitoring_policy=monitoring_policy,
        runtime_status=runtime_status,
        retention_policy=retention_policy,
    )


@router.post("/operations/backup/database/run")
def run_database_backup(role: str = Depends(_ensure_admin)):
    _ = role
    try:
        return run_database_backup_now()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/operations/backup/files/run")
def run_upload_file_backup(role: str = Depends(_ensure_admin)):
    _ = role
    try:
        return run_file_backup_now()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/operations/logs/cleanup/run")
def run_log_cleanup(role: str = Depends(_ensure_admin)):
    _ = role
    try:
        return run_log_cleanup_now(trigger="manual")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/operations/restore-drill/run")
def run_restore_drill(role: str = Depends(_ensure_admin)):
    _ = role
    try:
        return run_restore_drill_now(trigger="manual")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/operations/archive/preview")
def preview_manual_archive(
    body: dict[str, Any] | None = None,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    _ = (body, role)
    config = ConfigService(db)
    monitoring_policy = config.get("operations_monitoring_policy")
    if normalize_archive_mode(monitoring_policy.get("archive_mode")) != "manual":
        raise HTTPException(status_code=409, detail="Archive manual mode is disabled. Switch to manual mode first.")

    preview = build_auto_archive_preview(db, sample_limit=20, include_record_ids=True)
    token_payload = _issue_archive_preview_token(list(preview.get("candidate_record_ids") or []))
    preview.update(token_payload)
    preview.pop("candidate_record_ids", None)
    update_archive_preview_runtime_status(db, preview_result=preview, run_at=datetime.now(timezone.utc))
    db.commit()
    return preview


@router.post("/operations/archive/run")
def run_manual_archive(
    body: dict[str, Any] | None = None,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    _ = role
    config = ConfigService(db)
    monitoring_policy = config.get("operations_monitoring_policy")
    if normalize_archive_mode(monitoring_policy.get("archive_mode")) != "manual":
        raise HTTPException(status_code=409, detail="Archive manual mode is disabled. Switch to manual mode first.")

    preview_token = str((body or {}).get("preview_token") or "").strip()
    payload = _peek_archive_preview_token(preview_token)
    if not payload:
        raise HTTPException(status_code=409, detail="Archive execution requires a fresh archive preview.")

    try:
        result = execute_manual_archive_preview(
            db,
            preview_record_ids=list(payload.get("record_ids") or []),
            actor="admin",
        )
    except HTTPException:
        _consume_archive_preview_token(preview_token)
        raise

    _consume_archive_preview_token(preview_token)
    update_archive_run_runtime_status(
        db,
        archive_result=result,
        run_at=datetime.now(timezone.utc),
        trigger="manual",
    )
    db.commit()
    return result


@router.get("/jobs/{job_id}/files")
def get_job_files(
    job_id: str,
    include_deleted: bool = Query(default=False),
    lifecycle_view: str | None = Query(default=None),
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    _ = role
    job = db.get(UploadJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Upload job not found")

    view = _normalize_lifecycle_view(lifecycle_view, include_deleted=include_deleted)
    q = db.query(UploadedFile).filter(UploadedFile.job_id == job_id)
    if view == "review_queue":
        review_items = build_review_queue_items(db)
        review_file_ids = {
            str(item.get("file_id") or "").strip()
            for item in review_items
            if (item.get("job_id") or "") == job_id and item.get("file_id")
        }
        rows = (
            q.filter(UploadedFile.id.in_(sorted(review_file_ids))).order_by(UploadedFile.created_at.desc()).all()
            if review_file_ids
            else []
        )
    elif view == "special_case":
        special_items = build_special_case_items(db)
        special_file_ids = {
            str(item.get("file_id") or item.get("object_id") or "").strip()
            for item in special_items
            if (item.get("job_id") or "") == job_id and item.get("object_type") in {"file", "record"}
        }
        rows = (
            q.filter(UploadedFile.id.in_(sorted(fid for fid in special_file_ids if fid))).order_by(UploadedFile.created_at.desc()).all()
            if special_file_ids
            else []
        )
    else:
        q = _apply_file_lifecycle_view(q, view)
        rows = q.order_by(UploadedFile.created_at.desc()).all()
    file_review_counts = build_file_review_counts(db, [f.id for f in rows])
    file_anomaly_details = build_file_anomaly_details(db, [f.id for f in rows])

    items = [
        {
            "file_id": f.id,
            "job_id": f.job_id,
            "filename": f.filename,
            "document_type": f.document_type,
            "parse_status": f.parse_status,
            "parse_error": f.parse_error,
            "parsed_count": f.parsed_count,
            "auto_deleted_duplicate_count": int(f.auto_deleted_duplicate_count or 0),
            "review_queue_record_count": int(file_review_counts.get(f.id, {}).get("review_queue_record_count", 0)),
            "review_released_record_count": int(file_review_counts.get(f.id, {}).get("review_released_record_count", 0)),
            "has_anomaly": bool(file_anomaly_details.get(f.id, {}).get("has_anomaly")),
            "anomaly_codes": file_anomaly_details.get(f.id, {}).get("anomaly_codes", []),
            "anomaly_count": int(file_anomaly_details.get(f.id, {}).get("anomaly_count", 0)),
            "uploaded_at": f.created_at,
            "file_size": int(f.file_size or 0),
            "content_type": f.content_type,
            "storage_key": f.storage_key,
            "duplicate_risk": _duplicate_risk_code(db, f),
            "preview_kind": _preview_kind_for_file(f),
            "lifecycle_state": file_lifecycle_state(f),
            "special_case_source": special_case_source_code_for_file(db, f) if file_lifecycle_state(f) == LIFECYCLE_SPECIAL_CASE else None,
            "is_deleted": file_lifecycle_state(f) == LIFECYCLE_RECYCLE_BIN,
            "is_archived": file_lifecycle_state(f) == LIFECYCLE_ARCHIVED,
            "deleted_at": f.deleted_at,
            "deleted_by": f.deleted_by,
            "delete_reason": f.delete_reason,
            "restored_at": f.restored_at,
            "restored_by": f.restored_by,
            "restore_reason": f.restore_reason,
            "archived_at": f.archived_at,
            "archived_by": f.archived_by,
            "archive_reason": f.archive_reason,
        }
        for f in rows
    ]
    return {
        "job_id": job_id,
        "count": len(items),
        "include_deleted": include_deleted,
        "lifecycle_view": view,
        "items": items,
    }


@router.get("/jobs/{job_id}/overview")
def get_job_overview(
    job_id: str,
    include_deleted: bool = Query(default=False),
    lifecycle_view: str | None = Query(default=None),
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    _ = role
    job = db.get(UploadJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Upload job not found")

    view = _normalize_lifecycle_view(lifecycle_view, include_deleted=include_deleted)
    records_q = (
        db.query(NormalizedRecord)
        .join(UploadedFile, UploadedFile.id == NormalizedRecord.file_id)
        .filter(NormalizedRecord.job_id == job_id)
    )
    if view == "review_queue":
        visible_records = [
            record
            for record in records_q.order_by(NormalizedRecord.created_at.asc()).all()
            if is_review_queue_record(record)
        ]
    elif view == "special_case":
        visible_records = _apply_record_lifecycle_view(records_q, view).order_by(NormalizedRecord.created_at.asc()).all()
    else:
        visible_records = _apply_record_lifecycle_view(records_q, view).order_by(NormalizedRecord.created_at.asc()).all()
    normalized_count = len(visible_records)
    scan_stats = _scan_stats(visible_records)
    record_ids = [record.id for record in visible_records]
    group_ids = _group_ids_for_records(record_ids, db)
    match_group_count = len(group_ids)
    if group_ids:
        open_alert_count = db.query(func.count(Alert.id)).filter(Alert.group_id.in_(group_ids), Alert.status == "open").scalar() or 0
        resolved_alert_count = (
            db.query(func.count(Alert.id)).filter(Alert.group_id.in_(group_ids), Alert.status == "resolved").scalar() or 0
        )
    else:
        open_alert_count = 0
        resolved_alert_count = 0
    latest_task = db.query(TaskRun).filter(TaskRun.job_id == job_id).order_by(TaskRun.updated_at.desc()).first()

    return {
        "job_id": job_id,
        "job_status": job.status,
        "normalized_record_count": int(normalized_count),
        **scan_stats,
        "match_group_count": int(match_group_count),
        "open_alert_count": int(open_alert_count),
        "resolved_alert_count": int(resolved_alert_count),
        "latest_task_id": latest_task.id if latest_task else None,
        "latest_task_status": latest_task.status if latest_task else None,
        "latest_task_updated_at": latest_task.updated_at if latest_task else None,
        "latest_task_error": latest_task.error if latest_task else None,
        "include_deleted": include_deleted,
        "lifecycle_view": view,
    }


@router.get("/files/{file_id}")
def get_file_detail(
    file_id: str,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    _ = role
    f = db.get(UploadedFile, file_id)
    if not f:
        raise HTTPException(status_code=404, detail="Uploaded file not found")

    records = (
        db.query(NormalizedRecord)
        .filter(NormalizedRecord.file_id == file_id)
        .order_by(NormalizedRecord.created_at.asc(), NormalizedRecord.source_row.asc())
        .all()
    )
    normalized_count = len(records)
    scan_stats = _scan_stats(records)
    record_ids = [record.id for record in records]
    group_ids = _group_ids_for_records(record_ids, db)
    if group_ids:
        open_alert_count = db.query(func.count(Alert.id)).filter(Alert.group_id.in_(group_ids), Alert.status == "open").scalar() or 0
        resolved_alert_count = (
            db.query(func.count(Alert.id)).filter(Alert.group_id.in_(group_ids), Alert.status == "resolved").scalar() or 0
        )
    else:
        open_alert_count = 0
        resolved_alert_count = 0

    return {
        "file_id": f.id,
        "job_id": f.job_id,
        "filename": f.filename,
        "document_type": f.document_type,
        "parse_status": f.parse_status,
        "parse_error": f.parse_error,
        "parsed_count": f.parsed_count,
        "auto_deleted_duplicate_count": int(f.auto_deleted_duplicate_count or 0),
        "uploaded_at": f.created_at,
        "file_size": int(f.file_size or 0),
        "content_type": f.content_type,
        "storage_key": f.storage_key,
        "meta": f.meta_json or {},
        "file_hash_sha256": f.file_hash_sha256,
        "normalized_record_count": int(normalized_count),
        **scan_stats,
        "match_group_count": len(group_ids),
        "open_alert_count": int(open_alert_count),
        "resolved_alert_count": int(resolved_alert_count),
        "duplicate_risk": _duplicate_risk_code(db, f),
        "preview_kind": _preview_kind_for_file(f),
        "lifecycle_state": file_lifecycle_state(f),
        "special_case_source": special_case_source_code_for_file(db, f) if file_lifecycle_state(f) == LIFECYCLE_SPECIAL_CASE else None,
        "is_deleted": file_lifecycle_state(f) == LIFECYCLE_RECYCLE_BIN,
        "is_archived": file_lifecycle_state(f) == LIFECYCLE_ARCHIVED,
        "deleted_at": f.deleted_at,
        "deleted_by": f.deleted_by,
        "delete_reason": f.delete_reason,
        "restored_at": f.restored_at,
        "restored_by": f.restored_by,
        "restore_reason": f.restore_reason,
        "archived_at": f.archived_at,
        "archived_by": f.archived_by,
        "archive_reason": f.archive_reason,
    }


@router.get("/files/{file_id}/records")
def get_file_records(
    file_id: str,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    _ = role
    f = db.get(UploadedFile, file_id)
    if not f:
        raise HTTPException(status_code=404, detail="Uploaded file not found")

    records = (
        db.query(NormalizedRecord)
        .filter(NormalizedRecord.file_id == file_id)
        .order_by(NormalizedRecord.source_row.asc(), NormalizedRecord.created_at.asc())
        .all()
    )
    scan_stats = _scan_stats(records)
    return {
        "file_id": f.id,
        "job_id": f.job_id,
        "filename": f.filename,
        "count": len(records),
        **scan_stats,
        "items": [_normalized_record_item(record) for record in records],
    }


@router.post("/files/{file_id}/delete-impact")
def get_file_delete_impact(
    file_id: str,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    _ = role
    return build_file_impact(db, file_id)


@router.post("/records/{record_id}/delete-impact")
def get_record_delete_impact(
    record_id: str,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    _ = role
    return build_record_impact(db, record_id)


@router.post("/files/{file_id}/soft-delete")
def soft_delete_file(
    file_id: str,
    background_tasks: BackgroundTasks,
    body: dict[str, Any] | None = None,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    f = db.get(UploadedFile, file_id)
    if not f:
        raise HTTPException(status_code=404, detail="Uploaded file not found")
    payload = body or {}
    reason = _body_reason(payload)
    job_ids = move_file_to_recycle_bin(db, file=f, actor=role, reason=reason)
    task_ids = _enqueue_lifecycle_recompute(
        db=db,
        background_tasks=background_tasks,
        job_ids=job_ids,
        created_by=role,
        trigger="manual_file_recycle_bin",
        object_type="file",
        object_id=f.id,
    )
    db.commit()
    db.refresh(f)
    return {
        "ok": True,
        "file_id": f.id,
        "job_id": f.job_id,
        "lifecycle_state": file_lifecycle_state(f),
        "deleted_at": f.deleted_at,
        "deleted_by": f.deleted_by,
        "recompute_task_ids": task_ids,
    }


@router.post("/records/{record_id}/soft-delete")
def soft_delete_record(
    record_id: str,
    background_tasks: BackgroundTasks,
    body: dict[str, Any] | None = None,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    record = db.get(NormalizedRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Normalized record not found")
    payload = body or {}
    reason = _body_reason(payload)
    job_ids = move_record_to_recycle_bin(db, record=record, actor=role, reason=reason)
    task_ids = _enqueue_lifecycle_recompute(
        db=db,
        background_tasks=background_tasks,
        job_ids=job_ids,
        created_by=role,
        trigger="manual_record_recycle_bin",
        object_type="record",
        object_id=record.id,
    )
    db.commit()
    db.refresh(record)
    return {
        "ok": True,
        "record_id": record.id,
        "job_id": record.job_id,
        "lifecycle_state": record_lifecycle_state(record),
        "version_status": record_version_status(record),
        "deleted_at": record.deleted_at,
        "deleted_by": record.deleted_by,
        "delete_origin": record.delete_origin,
        "recompute_task_ids": task_ids,
    }


@router.post("/files/{file_id}/restore")
def restore_deleted_file(
    file_id: str,
    background_tasks: BackgroundTasks,
    body: dict[str, Any] | None = None,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    f = db.get(UploadedFile, file_id)
    if not f:
        raise HTTPException(status_code=404, detail="Uploaded file not found")
    reason = _body_reason(body or {})
    job_ids = restore_file(db, file=f, actor=role, reason=reason)
    task_ids = _enqueue_lifecycle_recompute(
        db=db,
        background_tasks=background_tasks,
        job_ids=job_ids,
        created_by=role,
        trigger="restore_file_from_recycle_bin",
        object_type="file",
        object_id=f.id,
    )
    db.commit()
    db.refresh(f)
    return {
        "ok": True,
        "file_id": f.id,
        "job_id": f.job_id,
        "lifecycle_state": file_lifecycle_state(f),
        "restored_at": f.restored_at,
        "restored_by": f.restored_by,
        "recompute_task_ids": task_ids,
    }


@router.post("/records/{record_id}/restore")
def restore_deleted_record(
    record_id: str,
    background_tasks: BackgroundTasks,
    body: dict[str, Any] | None = None,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    record = db.get(NormalizedRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Normalized record not found")
    reason = _body_reason(body or {})
    job_ids = restore_record(db, record=record, actor=role, reason=reason)
    task_ids = _enqueue_lifecycle_recompute(
        db=db,
        background_tasks=background_tasks,
        job_ids=job_ids,
        created_by=role,
        trigger="restore_record_from_recycle_bin",
        object_type="record",
        object_id=record.id,
    )
    db.commit()
    db.refresh(record)
    return {
        "ok": True,
        "record_id": record.id,
        "job_id": record.job_id,
        "lifecycle_state": record_lifecycle_state(record),
        "version_status": record_version_status(record),
        "is_current_effective": is_current_effective_record(record),
        "restored_at": record.restored_at,
        "restored_by": record.restored_by,
        "recompute_task_ids": task_ids,
    }


@router.post("/files/{file_id}/archive")
def archive_deleted_file(
    file_id: str,
    body: dict[str, Any] | None = None,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    f = db.get(UploadedFile, file_id)
    if not f:
        raise HTTPException(status_code=404, detail="Uploaded file not found")
    archive_file(db, file=f, actor=role, reason=_body_reason(body or {}))
    db.commit()
    db.refresh(f)
    return {
        "ok": True,
        "file_id": f.id,
        "job_id": f.job_id,
        "lifecycle_state": file_lifecycle_state(f),
        "archived_at": f.archived_at,
        "archived_by": f.archived_by,
    }


@router.post("/records/{record_id}/archive")
def archive_deleted_record(
    record_id: str,
    body: dict[str, Any] | None = None,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    record = db.get(NormalizedRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Normalized record not found")
    archive_record(db, record=record, actor=role, reason=_body_reason(body or {}))
    db.commit()
    db.refresh(record)
    return {
        "ok": True,
        "record_id": record.id,
        "job_id": record.job_id,
        "lifecycle_state": record_lifecycle_state(record),
        "version_status": record_version_status(record),
        "archived_at": record.archived_at,
        "archived_by": record.archived_by,
    }


@router.post("/files/{file_id}/hard-delete-preview")
def preview_file_hard_delete(
    file_id: str,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    _ = role
    preview = build_file_hard_delete_preview(db, file_id)
    preview.update(_issue_hard_delete_preview_token("file", file_id))
    return preview


@router.post("/records/{record_id}/hard-delete-preview")
def preview_record_hard_delete(
    record_id: str,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    _ = role
    preview = build_record_hard_delete_preview(db, record_id)
    preview.update(_issue_hard_delete_preview_token("record", record_id))
    return preview


@router.post("/files/{file_id}/hard-delete")
def execute_file_hard_delete(
    file_id: str,
    body: dict[str, Any] | None = None,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    f = db.get(UploadedFile, file_id)
    if not f:
        raise HTTPException(status_code=404, detail="Uploaded file not found")
    payload = body or {}
    if not _consume_hard_delete_preview_token(payload.get("preview_token"), object_type="file", object_id=file_id):
        raise HTTPException(status_code=409, detail="Hard delete requires a fresh hard-delete preview.")
    hard_delete_file(db, file=f)
    db.commit()
    return {"ok": True, "file_id": file_id, "purged_by": role[:20]}


@router.post("/records/{record_id}/hard-delete")
def execute_record_hard_delete(
    record_id: str,
    body: dict[str, Any] | None = None,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    record = db.get(NormalizedRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Normalized record not found")
    payload = body or {}
    if not _consume_hard_delete_preview_token(payload.get("preview_token"), object_type="record", object_id=record_id):
        raise HTTPException(status_code=409, detail="Hard delete requires a fresh hard-delete preview.")
    hard_delete_record(db, record=record)
    db.commit()
    return {"ok": True, "record_id": record_id, "purged_by": role[:20]}


@router.get("/recycle-bin")
def get_recycle_bin(
    object_type: str = Query(default="all", pattern="^(all|file|record)$"),
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    _ = role
    items = build_recycle_bin_items(db)
    if object_type != "all":
        items = [item for item in items if item.get("object_type") == object_type]
    return {"object_type": object_type, "count": len(items), "items": items}


@router.get("/archived")
def get_archived(
    object_type: str = Query(default="all", pattern="^(all|file|record)$"),
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    _ = role
    items = build_archived_items(db)
    if object_type != "all":
        items = [item for item in items if item.get("object_type") == object_type]
    return {"object_type": object_type, "count": len(items), "items": items}


@router.get("/special-case")
def get_special_case(
    object_type: str = Query(default="all", pattern="^(all|file|record)$"),
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    _ = role
    items = build_special_case_items(db)
    if object_type != "all":
        items = [item for item in items if item.get("object_type") == object_type]
    return {"object_type": object_type, "count": len(items), "items": items}


@router.get("/review-queue")
def get_review_queue(
    object_type: str = Query(default="all", pattern="^(all|file|record)$"),
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    _ = role
    items = build_review_queue_items(db)
    if object_type != "all":
        items = [item for item in items if item.get("object_type") == object_type]
    return {"object_type": object_type, "count": len(items), "items": items}


@router.get("/review-queue/records/{record_id}/compare")
def get_review_queue_record_compare(
    record_id: str,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    _ = role
    record = db.get(NormalizedRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Normalized record not found")
    if not is_review_queue_record(record):
        raise HTTPException(status_code=409, detail="Record is not in review queue.")
    return _build_review_queue_reason_payload(record)


@router.post("/records/{record_id}/special-case")
def mark_record_special_case(
    record_id: str,
    background_tasks: BackgroundTasks,
    body: dict[str, Any] | None = None,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    record = db.get(NormalizedRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Normalized record not found")
    payload = body or {}
    reason = validate_special_case_reason(str(payload.get("special_case_reason") or ""))
    note = str(payload.get("special_case_note") or "").strip() or None
    affected_job_ids = move_record_to_special_case(db, record=record, actor=role, reason=reason, note=note)
    task_ids = _enqueue_lifecycle_recompute(
        db=db,
        background_tasks=background_tasks,
        job_ids=affected_job_ids,
        created_by=role,
        trigger="move_to_special_case",
        object_type="record",
        object_id=record.id,
    )
    db.commit()
    db.refresh(record)
    return {
        "ok": True,
        "record_id": record.id,
        "job_id": record.job_id,
        "lifecycle_state": record_lifecycle_state(record),
        "version_status": record_version_status(record),
        "review_status": review_status_code(record),
        "effective_status": effective_status_code(record),
        "is_current_effective": is_current_effective_record(record),
        "special_case_reason": reason,
        "special_case_note": note,
        "recompute_task_ids": task_ids,
    }


@router.post("/records/{record_id}/return-to-job-list")
def return_record_from_special_case_to_job_list(
    record_id: str,
    background_tasks: BackgroundTasks,
    body: dict[str, Any] | None = None,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    record = db.get(NormalizedRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Normalized record not found")
    reason = _body_reason(body or {})
    job_ids = return_record_to_job_list_from_special_case(db, record=record, actor=role, reason=reason)
    task_ids = _enqueue_lifecycle_recompute(
        db=db,
        background_tasks=background_tasks,
        job_ids=job_ids,
        created_by=role,
        trigger="return_record_from_special_case_to_job_list",
        object_type="record",
        object_id=record.id,
    )
    db.commit()
    db.refresh(record)
    return {
        "ok": True,
        "record_id": record.id,
        "job_id": record.job_id,
        "lifecycle_state": record_lifecycle_state(record),
        "version_status": record_version_status(record),
        "is_current_effective": is_current_effective_record(record),
        "restored_at": record.restored_at,
        "restored_by": record.restored_by,
        "recompute_task_ids": task_ids,
    }


@router.post("/records/{record_id}/return-to-review-queue")
def return_record_from_special_case_to_review_queue(
    record_id: str,
    background_tasks: BackgroundTasks,
    body: dict[str, Any] | None = None,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    record = db.get(NormalizedRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Normalized record not found")
    reason = _body_reason(body or {})
    job_ids = return_record_to_review_queue_from_special_case(db, record=record, actor=role, reason=reason)
    task_ids = _enqueue_lifecycle_recompute(
        db=db,
        background_tasks=background_tasks,
        job_ids=job_ids,
        created_by=role,
        trigger="return_record_from_special_case_to_review_queue",
        object_type="record",
        object_id=record.id,
    )
    db.commit()
    db.refresh(record)
    return {
        "ok": True,
        "record_id": record.id,
        "job_id": record.job_id,
        "lifecycle_state": record_lifecycle_state(record),
        "version_status": record_version_status(record),
        "is_current_effective": is_current_effective_record(record),
        "restored_at": record.restored_at,
        "restored_by": record.restored_by,
        "recompute_task_ids": task_ids,
    }


@router.post("/files/{file_id}/return-to-job-list")
def return_file_from_special_case_to_job_list(
    file_id: str,
    background_tasks: BackgroundTasks,
    body: dict[str, Any] | None = None,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    f = db.get(UploadedFile, file_id)
    if not f:
        raise HTTPException(status_code=404, detail="Uploaded file not found")
    reason = _body_reason(body or {})
    job_ids = return_file_to_job_list_from_special_case(db, file=f, actor=role, reason=reason)
    task_ids = _enqueue_lifecycle_recompute(
        db=db,
        background_tasks=background_tasks,
        job_ids=job_ids,
        created_by=role,
        trigger="return_file_from_special_case_to_job_list",
        object_type="file",
        object_id=f.id,
    )
    db.commit()
    db.refresh(f)
    return {
        "ok": True,
        "file_id": f.id,
        "job_id": f.job_id,
        "lifecycle_state": file_lifecycle_state(f),
        "restored_at": f.restored_at,
        "restored_by": f.restored_by,
        "recompute_task_ids": task_ids,
    }


@router.post("/files/{file_id}/return-to-review-queue")
def return_file_from_special_case_to_review_queue(
    file_id: str,
    background_tasks: BackgroundTasks,
    body: dict[str, Any] | None = None,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    f = db.get(UploadedFile, file_id)
    if not f:
        raise HTTPException(status_code=404, detail="Uploaded file not found")
    reason = _body_reason(body or {})
    job_ids = return_file_to_review_queue_from_special_case(db, file=f, actor=role, reason=reason)
    task_ids = _enqueue_lifecycle_recompute(
        db=db,
        background_tasks=background_tasks,
        job_ids=job_ids,
        created_by=role,
        trigger="return_file_from_special_case_to_review_queue",
        object_type="file",
        object_id=f.id,
    )
    db.commit()
    db.refresh(f)
    return {
        "ok": True,
        "file_id": f.id,
        "job_id": f.job_id,
        "lifecycle_state": file_lifecycle_state(f),
        "restored_at": f.restored_at,
        "restored_by": f.restored_by,
        "recompute_task_ids": task_ids,
    }


@router.get("/files/{file_id}/content")
def get_file_content(
    file_id: str,
    mode: str = Query(default="preview", pattern="^(preview|download)$"),
    rows: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    _ = role
    f = db.get(UploadedFile, file_id)
    if not f:
        raise HTTPException(status_code=404, detail="Uploaded file not found")

    path = _resolve_storage_path(f)
    suffix = _suffix_for_file(f)
    media = f.content_type or "application/octet-stream"

    if mode == "download":
        return FastAPIFileResponse(path=path, media_type=media, filename=f.filename or path.name)

    if suffix in _IMAGE_EXTS:
        return FastAPIFileResponse(path=path, media_type=media or "image/*")
    if suffix == ".pdf":
        return FastAPIFileResponse(path=path, media_type="application/pdf")
    if suffix in _TABULAR_EXTS:
        try:
            if suffix == ".csv":
                df = pd.read_csv(path, nrows=rows + 1)
            else:
                df = pd.read_excel(path, nrows=rows + 1)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"tabular preview failed: {exc}")

        truncated = len(df) > rows
        view = df.head(rows)
        records = []
        for _, row in view.iterrows():
            item = {str(col): _coerce_preview_value(row[col]) for col in view.columns}
            records.append(item)
        return {
            "kind": "tabular",
            "columns": [str(c) for c in view.columns],
            "rows": records,
            "truncated": truncated,
        }

    if suffix in _TEXT_EXTS:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"text preview failed: {exc}")
        max_chars = 12000
        return {"kind": "text", "text": text[:max_chars], "truncated": len(text) > max_chars}

    return {"kind": "binary", "message": "Preview not supported for this file type. Use mode=download."}
