from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.settings import STORAGE_DIR
from app.db.models import Alert, ConfigEntry, GroupRecordLink, MatchGroup, NormalizedRecord, TaskRun, UploadJob, UploadedFile
from app.services.log_retention_service import summarize_log_cleanup_state
from app.services.normalize_service import get_core
from app.services.restore_drill_service import summarize_restore_drill_state
from app.services.order_governance import (
    CHANGE_DUPLICATE,
    CHANGE_UPDATE,
    COMPARE_FIELDS,
    DELETE_ORIGIN_MANUAL_FILE,
    DELETE_ORIGIN_MANUAL_RECORD,
    LIFECYCLE_ACTIVE,
    LIFECYCLE_ARCHIVED,
    LIFECYCLE_RECYCLE_BIN,
    LIFECYCLE_SPECIAL_CASE,
    STATUS_CURRENT,
    STATUS_DUPLICATE_SHADOW,
    STATUS_INACTIVE,
    STATUS_REVIEW_PENDING,
    STATUS_REVIEW_RELEASED,
    STATUS_RESTORED_HISTORY,
    STATUS_SPECIAL_CASE,
    REVIEW_IDENTITY_FIELDS,
    REVIEW_REQUIRED_FIELDS,
    _governance_reason_for_record,
    classify_order_record,
    file_lifecycle_state,
    get_governance,
    is_current_effective_record,
    record_lifecycle_state,
    record_version_status,
    should_hold_for_review,
    sync_record_governance,
)

_FILE_STATUS_ORDER = ("failed", "running", "queued", "succeeded", "unknown")
REVIEW_STATUS_PENDING = "pending_review"
REVIEW_STATUS_REVIEWED_NOT_DUPLICATE = "reviewed_not_duplicate"
REVIEW_STATUS_NONE = "none"

EFFECTIVE_STATUS_CURRENT = "current_effective"
EFFECTIVE_STATUS_RETAINED = "retained_not_effective"
EFFECTIVE_STATUS_INACTIVE = "inactive_old_version"

BATCH_VIEW_ALL = "all"
BATCH_VIEW_NON_EMPTY = "non_empty"

BATCH_ANOMALY_MISSING_IDENTITY_COLUMNS = "missing_identity_columns"
BATCH_ANOMALY_MISSING_STATUS_COLUMNS = "missing_status_columns"
BATCH_ANOMALY_REVIEW_REQUIRED_BLANK_VALUES = "review_required_blank_values"
BATCH_ANOMALY_PARSE_FAILED = "parse_failed"
BATCH_ANOMALY_REVIEW_QUEUE = "review_queue"

REVIEW_STATUS_FIELDS = tuple(field for field in REVIEW_REQUIRED_FIELDS if field not in REVIEW_IDENTITY_FIELDS)
BATCH_ANOMALY_CODE_ORDER = (
    BATCH_ANOMALY_MISSING_IDENTITY_COLUMNS,
    BATCH_ANOMALY_MISSING_STATUS_COLUMNS,
    BATCH_ANOMALY_REVIEW_REQUIRED_BLANK_VALUES,
    BATCH_ANOMALY_PARSE_FAILED,
    BATCH_ANOMALY_REVIEW_QUEUE,
)
ARCHIVE_REASON_AUTO_COMPLETED = "auto_completed_order"
ARCHIVE_REASON_MANUAL_CONFIRMED = "manual_completed_confirmed"
SPECIAL_CASE_REASONS = (
    "数量调整后完成",
    "金额/折扣调整后完成",
    "质量问题协商后完成",
    "客户取消部分后完成",
    "其他特殊完成",
)
DELETE_ORIGIN_ARCHIVED = "manual_archived"
DELETE_ORIGIN_SPECIAL_CASE = "manual_special_case"
_ARCHIVE_FLOAT_EPSILON = 1e-9
ARCHIVE_MODE_AUTO = "auto"
ARCHIVE_MODE_MANUAL = "manual"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_archive_mode(value: Any) -> str:
    mode = str(value or ARCHIVE_MODE_AUTO).strip().lower()
    return ARCHIVE_MODE_MANUAL if mode == ARCHIVE_MODE_MANUAL else ARCHIVE_MODE_AUTO


def _parse_runtime_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _has_meaningful_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        text = str(value).replace(",", "").strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _numbers_equal(left: Any, right: Any) -> bool:
    left_num = _to_float(left)
    right_num = _to_float(right)
    if left_num is None or right_num is None:
        return False
    return abs(left_num - right_num) <= _ARCHIVE_FLOAT_EPSILON


def _active_records_for_file(db: Session, file_id: str) -> list[NormalizedRecord]:
    return (
        db.query(NormalizedRecord)
        .filter(
            NormalizedRecord.file_id == file_id,
            NormalizedRecord.lifecycle_state == LIFECYCLE_ACTIVE,
        )
        .order_by(NormalizedRecord.created_at.asc(), NormalizedRecord.source_row.asc())
        .all()
    )


def _recalculate_file_lifecycle_state(db: Session, file: UploadedFile, *, actor: str, reason: str | None = None) -> None:
    prior_state = file_lifecycle_state(file)
    db.flush()
    rows = (
        db.query(NormalizedRecord.lifecycle_state)
        .filter(NormalizedRecord.file_id == file.id)
        .all()
    )
    states = [str(state or "").strip().lower() for (state,) in rows]
    if LIFECYCLE_ACTIVE in states:
        if prior_state == LIFECYCLE_RECYCLE_BIN:
            _mark_file_restored_out_of_recycle_bin(file, actor=actor, reason=reason)
        file.lifecycle_state = LIFECYCLE_ACTIVE
        return
    if LIFECYCLE_SPECIAL_CASE in states:
        if prior_state == LIFECYCLE_RECYCLE_BIN:
            _mark_file_restored_out_of_recycle_bin(file, actor=actor, reason=reason)
        file.lifecycle_state = LIFECYCLE_SPECIAL_CASE
        if file.archived_at is None:
            file.archived_at = _utcnow()
        file.archived_by = actor[:20]
        file.archive_reason = (reason or "").strip()[:1000] or file.archive_reason
        return
    if LIFECYCLE_ARCHIVED in states:
        if prior_state == LIFECYCLE_RECYCLE_BIN:
            _mark_file_restored_out_of_recycle_bin(file, actor=actor, reason=reason)
        file.lifecycle_state = LIFECYCLE_ARCHIVED
        if file.archived_at is None:
            file.archived_at = _utcnow()
        file.archived_by = actor[:20]
        file.archive_reason = (reason or "").strip()[:1000] or file.archive_reason
        return
    if LIFECYCLE_RECYCLE_BIN in states:
        file.lifecycle_state = LIFECYCLE_RECYCLE_BIN
        return
    if prior_state == LIFECYCLE_RECYCLE_BIN:
        _mark_file_restored_out_of_recycle_bin(file, actor=actor, reason=reason)
    file.lifecycle_state = LIFECYCLE_ACTIVE


def can_direct_archive_active_record(record: NormalizedRecord) -> bool:
    if record.document_type != "order":
        return False
    if record_lifecycle_state(record) != LIFECYCLE_ACTIVE:
        return False
    if not is_current_effective_record(record):
        return False
    if is_review_queue_record(record):
        return False
    return True


def is_auto_archive_candidate_record(record: NormalizedRecord) -> bool:
    if not can_direct_archive_active_record(record):
        return False
    core = get_core(record.payload_json)
    quantity = _to_float(core.get("quantity"))
    executed_shipped_qty = _to_float(core.get("executed_shipped_qty"))
    invoiced_qty = _to_float(core.get("invoiced_qty"))
    if quantity is None or executed_shipped_qty is None or invoiced_qty is None:
        return False
    return _numbers_equal(executed_shipped_qty, quantity) and _numbers_equal(invoiced_qty, quantity)


def can_direct_archive_active_file(db: Session, file: UploadedFile) -> bool:
    _ = (db, file)
    return False


def is_auto_archive_candidate_file(db: Session, file: UploadedFile) -> bool:
    _ = (db, file)
    return False


def _review_required_column_presence(record: NormalizedRecord) -> dict[str, bool]:
    payload = record.payload_json if isinstance(record.payload_json, dict) else {}
    ext = payload.get("ext")
    if not isinstance(ext, dict):
        return {}
    raw = ext.get("review_required_columns_present")
    if not isinstance(raw, dict):
        return {}
    return {str(field): bool(present) for field, present in raw.items()}


def _record_missing_columns(record: NormalizedRecord, fields: tuple[str, ...]) -> list[str]:
    presence = _review_required_column_presence(record)
    return [field for field in fields if presence.get(field) is False]


def _record_blank_review_values(record: NormalizedRecord) -> list[str]:
    core = get_core(record.payload_json)
    presence = _review_required_column_presence(record)
    return [
        field
        for field in REVIEW_REQUIRED_FIELDS
        if presence.get(field) is not False and not _has_meaningful_value(core.get(field))
    ]


def _finalize_batch_anomaly_codes(flags: dict[str, bool]) -> list[str]:
    return [code for code in BATCH_ANOMALY_CODE_ORDER if flags.get(code)]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _hours_since(reference_at: datetime | None) -> float | None:
    if reference_at is None:
        return None
    delta = _utcnow() - reference_at
    return round(delta.total_seconds() / 3600.0, 2)


def _build_runtime_block(raw: Any, *, time_key: str, extra_time_keys: tuple[str, ...] = ()) -> dict[str, Any]:
    data = dict(raw) if isinstance(raw, dict) else {}
    last_at = _parse_runtime_datetime(data.get(time_key))
    result = {
        **data,
        time_key: last_at.isoformat() if last_at else None,
        "hours_since": _hours_since(last_at),
    }
    for extra_key in extra_time_keys:
        extra_at = _parse_runtime_datetime(data.get(extra_key))
        result[extra_key] = extra_at.isoformat() if extra_at else None
    return result


def _build_ops_alert(
    *,
    code: str,
    level: str,
    title: str,
    message: str,
    suggestion: str,
    current_value: Any | None = None,
    threshold_value: Any | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "level": level,
        "title": title,
        "message": message,
        "suggestion": suggestion,
        "current_value": current_value,
        "threshold_value": threshold_value,
    }


def update_archive_preview_runtime_status(
    db: Session,
    *,
    preview_result: dict[str, Any],
    run_at: datetime,
) -> None:
    item = db.get(ConfigEntry, "operations_runtime_status")
    if not item:
        item = ConfigEntry(key="operations_runtime_status", value_json={})
        db.add(item)

    runtime_status = dict(item.value_json or {}) if isinstance(item.value_json, dict) else {}
    archive_preview = dict(runtime_status.get("archive_preview") or {})
    archive_preview.update(
        {
            "last_run_at": run_at.isoformat(),
            "last_status": str(preview_result.get("status") or "unknown"),
            "last_error": str(preview_result.get("error") or ""),
            "last_candidate_file_count": int(preview_result.get("candidate_file_count") or 0),
            "last_candidate_record_count": int(preview_result.get("candidate_record_count") or 0),
            "last_preview_items": list(preview_result.get("items") or [])[:20],
        }
    )
    runtime_status["archive_preview"] = archive_preview
    item.value_json = runtime_status


def update_archive_run_runtime_status(
    db: Session,
    *,
    archive_result: dict[str, Any],
    run_at: datetime,
    trigger: str,
) -> None:
    item = db.get(ConfigEntry, "operations_runtime_status")
    if not item:
        item = ConfigEntry(key="operations_runtime_status", value_json={})
        db.add(item)

    runtime_status = dict(item.value_json or {}) if isinstance(item.value_json, dict) else {}
    archive_run = dict(runtime_status.get("archive_run") or {})
    archive_run.update(
        {
            "last_run_at": run_at.isoformat(),
            "last_status": str(archive_result.get("status") or "unknown"),
            "last_error": str(archive_result.get("error") or ""),
            "last_archived_file_count": int(archive_result.get("archived_file_count") or 0),
            "last_archived_record_count": int(archive_result.get("archived_record_count") or 0),
            "last_trigger": str(trigger or "").strip().lower() or ARCHIVE_MODE_AUTO,
        }
    )
    runtime_status["archive_run"] = archive_run
    item.value_json = runtime_status


def update_slow_request_runtime_status(
    db: Session,
    *,
    request_item: dict[str, Any],
    keep_latest: int,
) -> None:
    item = db.get(ConfigEntry, "operations_runtime_status")
    if not item:
        item = ConfigEntry(key="operations_runtime_status", value_json={})
        db.add(item)

    runtime_status = dict(item.value_json or {}) if isinstance(item.value_json, dict) else {}
    slow_requests = dict(runtime_status.get("slow_requests") or {})
    recent_items = list(slow_requests.get("recent_items") or [])
    total_count = int(slow_requests.get("total_count") or 0) + 1
    duration_ms = int(request_item.get("duration_ms") or 0)
    slowest_duration_ms = int(slow_requests.get("slowest_duration_ms") or 0)

    recent_items.insert(0, request_item)
    keep_count = max(int(keep_latest or 0), 1)
    slow_requests.update(
        {
            "last_seen_at": str(request_item.get("observed_at") or _utcnow().isoformat()),
            "total_count": total_count,
            "recent_items": recent_items[:keep_count],
        }
    )
    if duration_ms >= slowest_duration_ms:
        slow_requests.update(
            {
                "slowest_duration_ms": duration_ms,
                "slowest_path": str(request_item.get("path") or ""),
                "slowest_method": str(request_item.get("method") or ""),
                "slowest_status_code": int(request_item.get("status_code") or 0),
                "slowest_query": str(request_item.get("query") or ""),
            }
        )
    runtime_status["slow_requests"] = slow_requests
    item.value_json = runtime_status


def _list_auto_archive_candidate_records(db: Session, *, job_id: str | None = None) -> list[NormalizedRecord]:
    query = (
        db.query(NormalizedRecord)
        .join(UploadedFile, UploadedFile.id == NormalizedRecord.file_id)
        .filter(
            NormalizedRecord.document_type == "order",
            NormalizedRecord.lifecycle_state == LIFECYCLE_ACTIVE,
            UploadedFile.lifecycle_state == LIFECYCLE_ACTIVE,
            NormalizedRecord.is_current_effective.is_(True),
        )
        .order_by(NormalizedRecord.created_at.asc(), NormalizedRecord.source_row.asc())
    )
    if job_id:
        query = query.filter(NormalizedRecord.job_id == job_id)
    rows = query.all()
    return [record for record in rows if is_auto_archive_candidate_record(record)]


def _candidate_auto_archive_file_ids(db: Session, candidate_records: list[NormalizedRecord]) -> set[str]:
    if not candidate_records:
        return set()
    candidate_record_ids = {record.id for record in candidate_records}
    candidate_file_ids: set[str] = set()
    for file_id in {str(record.file_id or "").strip() for record in candidate_records if record.file_id}:
        active_records = _active_records_for_file(db, file_id)
        if active_records and all(record.id in candidate_record_ids for record in active_records):
            candidate_file_ids.add(file_id)
    return candidate_file_ids


def build_auto_archive_preview(
    db: Session,
    *,
    job_id: str | None = None,
    sample_limit: int = 20,
    include_record_ids: bool = False,
) -> dict[str, Any]:
    candidate_records = _list_auto_archive_candidate_records(db, job_id=job_id)
    candidate_file_ids = _candidate_auto_archive_file_ids(db, candidate_records)
    limit = max(int(sample_limit or 0), 0)
    items: list[dict[str, Any]] = []
    for record in candidate_records[:limit]:
        core = get_core(record.payload_json)
        items.append(
            {
                "job_id": record.job_id,
                "file_id": record.file_id,
                "record_id": record.id,
                "customer_order_no": str(core.get("customer_order_no") or "").strip(),
                "product_name": str(core.get("product_name") or "").strip(),
                "source_row": record.source_row,
                "quantity": core.get("quantity"),
                "executed_shipped_qty": core.get("executed_shipped_qty"),
                "invoiced_qty": core.get("invoiced_qty"),
            }
        )
    result = {
        "status": "succeeded",
        "generated_at": _utcnow().isoformat(),
        "candidate_file_count": len(candidate_file_ids),
        "candidate_record_count": len(candidate_records),
        "items": items,
    }
    if include_record_ids:
        result["candidate_record_ids"] = [record.id for record in candidate_records]
    return result


def execute_manual_archive_preview(
    db: Session,
    *,
    preview_record_ids: list[str],
    actor: str,
) -> dict[str, Any]:
    current_candidates = _list_auto_archive_candidate_records(db)
    current_candidate_map = {record.id: record for record in current_candidates}
    current_candidate_ids = set(current_candidate_map.keys())
    snapshot_ids = [str(record_id or "").strip() for record_id in preview_record_ids if str(record_id or "").strip()]

    if current_candidate_ids != set(snapshot_ids):
        raise HTTPException(status_code=409, detail="Archive preview expired or candidates changed. Please preview again.")

    archived_file_ids: set[str] = set()
    archived_record_ids: set[str] = set()
    for record_id in snapshot_ids:
        record = current_candidate_map.get(record_id)
        if record is None:
            continue
        file = getattr(record, "file", None)
        archive_record(db, record=record, actor=actor, reason=ARCHIVE_REASON_MANUAL_CONFIRMED)
        archived_record_ids.add(record.id)
        if isinstance(file, UploadedFile) and file_lifecycle_state(file) == LIFECYCLE_ARCHIVED:
            archived_file_ids.add(file.id)

    return {
        "status": "succeeded",
        "archived_file_count": len(archived_file_ids),
        "archived_record_count": len(archived_record_ids),
    }


def build_operations_summary(
    db: Session,
    *,
    monitoring_policy: dict[str, Any],
    runtime_status: dict[str, Any],
    retention_policy: dict[str, Any],
) -> dict[str, Any]:
    jobs = db.query(UploadJob).order_by(UploadJob.created_at.desc()).all()
    job_summaries = [build_job_summary(db, job) for job in jobs]

    total_jobs = len(job_summaries)
    empty_shell_jobs = sum(1 for item in job_summaries if item.get("is_empty_shell"))
    non_empty_jobs = total_jobs - empty_shell_jobs
    review_queue_jobs = sum(1 for item in job_summaries if int(item.get("review_queue_record_count") or 0) > 0)
    parse_failed_jobs = sum(1 for item in job_summaries if int(item.get("parse_failed_count") or 0) > 0)
    failed_task_jobs = sum(1 for item in job_summaries if str(item.get("latest_task_status") or "").strip().lower() == "failed")
    auto_deleted_duplicate_total = sum(_safe_int(item.get("auto_deleted_duplicate_count")) for item in job_summaries)
    review_queue_record_total = sum(_safe_int(item.get("review_queue_record_count")) for item in job_summaries)
    parse_failed_file_total = sum(_safe_int(item.get("parse_failed_count")) for item in job_summaries)
    recycle_bin_file_total = sum(_safe_int(item.get("recycle_bin_file_count")) for item in job_summaries)
    recycle_bin_record_total = sum(_safe_int(item.get("recycle_bin_record_count")) for item in job_summaries)
    archived_file_total = sum(_safe_int(item.get("archived_file_count")) for item in job_summaries)
    archived_record_total = sum(_safe_int(item.get("archived_record_count")) for item in job_summaries)
    current_effective_record_total = sum(_safe_int(item.get("current_effective_record_count")) for item in job_summaries)

    latest_uploaded_file = db.query(UploadedFile).order_by(UploadedFile.created_at.desc()).first()
    latest_task_run = db.query(TaskRun).order_by(TaskRun.updated_at.desc(), TaskRun.created_at.desc()).first()

    _ = retention_policy
    archive_mode = normalize_archive_mode(monitoring_policy.get("archive_mode"))
    archive_preview_live = build_auto_archive_preview(db, sample_limit=0)
    archive_candidate_record_count = int(archive_preview_live.get("candidate_record_count") or 0)
    archive_candidate_file_count = int(archive_preview_live.get("candidate_file_count") or 0)

    db_backup = _build_runtime_block(
        runtime_status.get("db_backup"),
        time_key="last_success_at",
        extra_time_keys=("last_started_at", "last_finished_at"),
    )
    file_backup = _build_runtime_block(
        runtime_status.get("file_backup"),
        time_key="last_success_at",
        extra_time_keys=("last_started_at", "last_finished_at"),
    )
    slow_requests = _build_runtime_block(runtime_status.get("slow_requests"), time_key="last_seen_at")
    slow_requests["recent_items"] = list(slow_requests.get("recent_items") or [])[:20]
    archive_preview = _build_runtime_block(runtime_status.get("archive_preview"), time_key="last_run_at")
    archive_preview["last_preview_items"] = list(archive_preview.get("last_preview_items") or [])[:20]
    archive_run = _build_runtime_block(runtime_status.get("archive_run"), time_key="last_run_at")
    log_cleanup = summarize_log_cleanup_state(monitoring_policy=monitoring_policy, runtime_status=runtime_status)
    log_cleanup = _build_runtime_block(
        log_cleanup,
        time_key="last_success_at",
        extra_time_keys=("last_started_at", "last_finished_at"),
    )
    restore_drill = summarize_restore_drill_state(runtime_status=runtime_status, monitoring_policy=monitoring_policy)
    restore_drill = _build_runtime_block(
        restore_drill,
        time_key="last_success_at",
        extra_time_keys=("last_started_at", "last_finished_at", "available_db_snapshot_time", "available_file_snapshot_time"),
    )

    db_backup["enabled"] = bool(monitoring_policy.get("db_backup_enabled"))
    db_backup["target_path"] = str(monitoring_policy.get("db_backup_target_path") or "").strip()
    file_backup["enabled"] = bool(monitoring_policy.get("file_backup_enabled"))
    file_backup["target_path"] = str(monitoring_policy.get("file_backup_target_path") or "").strip()
    backup_schedule_time = str(monitoring_policy.get("backup_schedule_time") or "02:00").strip() or "02:00"
    backup_retention_days = max(_safe_int(monitoring_policy.get("backup_retention_days"), 30), 1)
    db_backup["schedule_time"] = backup_schedule_time
    db_backup["retention_days"] = backup_retention_days
    file_backup["schedule_time"] = backup_schedule_time
    file_backup["retention_days"] = backup_retention_days

    backup_overdue_hours = max(_safe_int(monitoring_policy.get("backup_overdue_hours"), 24), 1)
    slow_request_threshold_ms = max(_safe_int(monitoring_policy.get("slow_request_threshold_ms"), 1500), 1)
    slow_request_keep_latest = max(_safe_int(monitoring_policy.get("slow_request_keep_latest"), 10), 1)
    archive_run_overdue_hours = max(_safe_int(monitoring_policy.get("archive_run_overdue_hours"), 24), 1)
    log_cleanup_schedule_time = str(monitoring_policy.get("log_cleanup_schedule_time") or "03:00").strip() or "03:00"
    log_retention_days = max(_safe_int(monitoring_policy.get("log_retention_days"), 30), 1)
    review_queue_warn_threshold = max(_safe_int(monitoring_policy.get("review_queue_warn_threshold"), 10), 1)
    parse_failed_warn_threshold = max(_safe_int(monitoring_policy.get("parse_failed_warn_threshold"), 1), 1)
    failed_task_warn_threshold = max(_safe_int(monitoring_policy.get("failed_task_warn_threshold"), 1), 1)

    db_backup["is_overdue"] = bool(
        db_backup.get("hours_since") is not None and float(db_backup["hours_since"]) > float(backup_overdue_hours)
    )
    file_backup["is_overdue"] = bool(
        file_backup.get("hours_since") is not None and float(file_backup["hours_since"]) > float(backup_overdue_hours)
    )
    archive_run["is_overdue"] = bool(
        archive_mode == ARCHIVE_MODE_AUTO
        and archive_run.get("hours_since") is not None
        and float(archive_run["hours_since"]) > float(archive_run_overdue_hours)
        and archive_candidate_record_count > 0
    )
    archive_preview["mode"] = archive_mode
    archive_run["mode"] = archive_mode
    archive_run["trigger"] = str(archive_run.get("last_trigger") or "").strip().lower()

    alerts: list[dict[str, Any]] = []
    if parse_failed_jobs >= parse_failed_warn_threshold:
        alerts.append(
            _build_ops_alert(
                code="parse_failed_jobs_threshold",
                level="error",
                title="解析失败批次过多",
                message=f"解析失败批次〔{parse_failed_jobs}〕已达到告警线。",
                suggestion="先到批次汇总或审计后台查看解析失败批次。",
                current_value=parse_failed_jobs,
                threshold_value=parse_failed_warn_threshold,
            )
        )
    if review_queue_record_total >= review_queue_warn_threshold:
        alerts.append(
            _build_ops_alert(
                code="review_queue_threshold",
                level="warn",
                title="待复核积压",
                message=f"待复核记录〔{review_queue_record_total}〕已超过阈值〔{review_queue_warn_threshold}〕。",
                suggestion="先看待复核区原因，再决定是否人工处理。",
                current_value=review_queue_record_total,
                threshold_value=review_queue_warn_threshold,
            )
        )
    if failed_task_jobs >= failed_task_warn_threshold:
        alerts.append(
            _build_ops_alert(
                code="failed_task_jobs_threshold",
                level="error",
                title="失败批次过多",
                message=f"失败任务批次〔{failed_task_jobs}〕已超过阈值〔{failed_task_warn_threshold}〕。",
                suggestion="先看任务区和对应批次的最近失败原因。",
                current_value=failed_task_jobs,
                threshold_value=failed_task_warn_threshold,
            )
        )
    if str(db_backup.get("last_status") or "").strip().lower() == "failed":
        alerts.append(
            _build_ops_alert(
                code="db_backup_failed",
                level="error",
                title="数据库备份失败",
                message="数据库备份最近一次执行失败。",
                suggestion="先确认数据库连接和备份目录，再手动补跑数据库备份。",
                current_value=str(db_backup.get("last_status") or "").strip().lower() or "failed",
            )
        )
    elif db_backup.get("is_overdue"):
        alerts.append(
            _build_ops_alert(
                code="db_backup_overdue",
                level="warn",
                title="数据库备份超时",
                message="数据库备份超过设定时限仍未成功。",
                suggestion="先手动执行数据库备份，确认这次能否成功。",
                current_value=db_backup.get("hours_since"),
                threshold_value=backup_overdue_hours,
            )
        )

    if str(file_backup.get("last_status") or "").strip().lower() == "failed":
        alerts.append(
            _build_ops_alert(
                code="file_backup_failed",
                level="error",
                title="上传文件备份失败",
                message="上传文件备份最近一次执行失败。",
                suggestion="先确认上传文件备份目录可写，再手动补跑上传文件备份。",
                current_value=str(file_backup.get("last_status") or "").strip().lower() or "failed",
            )
        )
    elif file_backup.get("is_overdue"):
        alerts.append(
            _build_ops_alert(
                code="file_backup_overdue",
                level="warn",
                title="上传文件备份超时",
                message="上传文件备份超过设定时限仍未成功。",
                suggestion="先手动执行上传文件备份，确认压缩包能否正常生成。",
                current_value=file_backup.get("hours_since"),
                threshold_value=backup_overdue_hours,
            )
        )

    if str(archive_run.get("last_status") or "").strip().lower() == "failed":
        alerts.append(
            _build_ops_alert(
                code="archive_run_failed",
                level="error",
                title="归档任务失败",
                message="归档任务最近一次执行失败。",
                suggestion="先看归档状态里的最近错误，再决定是否人工处理。",
                current_value=str(archive_run.get("last_status") or "").strip().lower() or "failed",
            )
        )
    elif archive_run.get("is_overdue"):
        alerts.append(
            _build_ops_alert(
                code="archive_run_overdue",
                level="warn",
                title="归档任务超时",
                message="已经有归档候选，但归档任务超时未运行。",
                suggestion="先看归档候选数和最近归档时间，再决定是否手动处理。",
                current_value=archive_run.get("hours_since"),
                threshold_value=archive_run_overdue_hours,
            )
        )

    return {
        "generated_at": _utcnow().isoformat(),
        "health": {
            "total_jobs": total_jobs,
            "non_empty_jobs": non_empty_jobs,
            "empty_shell_jobs": empty_shell_jobs,
            "review_queue_jobs": review_queue_jobs,
            "parse_failed_jobs": parse_failed_jobs,
            "failed_task_jobs": failed_task_jobs,
            "auto_deleted_duplicate_total": auto_deleted_duplicate_total,
            "review_queue_record_total": review_queue_record_total,
            "parse_failed_file_total": parse_failed_file_total,
            "recycle_bin_file_total": recycle_bin_file_total,
            "recycle_bin_record_total": recycle_bin_record_total,
            "archived_file_total": archived_file_total,
            "archived_record_total": archived_record_total,
            "current_effective_record_total": current_effective_record_total,
            "latest_upload_at": latest_uploaded_file.created_at if latest_uploaded_file else None,
            "latest_upload_job_id": latest_uploaded_file.job_id if latest_uploaded_file else None,
            "latest_task_at": latest_task_run.updated_at if latest_task_run else None,
            "latest_task_status": latest_task_run.status if latest_task_run else None,
            "latest_task_job_id": latest_task_run.job_id if latest_task_run else None,
        },
        "backup": {
            "db_backup": db_backup,
            "file_backup": file_backup,
            "backup_overdue_hours": backup_overdue_hours,
            "backup_schedule_time": backup_schedule_time,
            "backup_retention_days": backup_retention_days,
        },
        "performance": {
            "slow_requests": slow_requests,
            "slow_request_threshold_ms": slow_request_threshold_ms,
            "slow_request_keep_latest": slow_request_keep_latest,
        },
        "logs": {
            "log_cleanup": log_cleanup,
            "log_cleanup_schedule_time": log_cleanup_schedule_time,
            "log_retention_days": log_retention_days,
        },
        "restore_drill": {
            "restore_drill": restore_drill,
        },
        "archive": {
            "mode": archive_mode,
            "candidate_file_count": int(archive_candidate_file_count),
            "candidate_record_count": int(archive_candidate_record_count),
            "auto_archive_rule": "当前有效订单且发齐=数量、开齐=数量",
            "archive_preview": archive_preview,
            "archive_run": archive_run,
            "archive_run_overdue_hours": archive_run_overdue_hours,
        },
        "alerts": alerts,
        "monitoring_policy": {
            "review_queue_warn_threshold": review_queue_warn_threshold,
            "parse_failed_warn_threshold": parse_failed_warn_threshold,
            "failed_task_warn_threshold": failed_task_warn_threshold,
            "backup_overdue_hours": backup_overdue_hours,
            "archive_run_overdue_hours": archive_run_overdue_hours,
        },
    }


def build_file_anomaly_details(db: Session, file_ids: list[str]) -> dict[str, dict[str, Any]]:
    details = {
        file_id: {
            "has_anomaly": False,
            "anomaly_codes": [],
            "anomaly_count": 0,
        }
        for file_id in file_ids
    }
    if not file_ids:
        return details

    files = db.query(UploadedFile).filter(UploadedFile.id.in_(file_ids)).all()
    flags_by_file = {
        file_id: {
            BATCH_ANOMALY_MISSING_IDENTITY_COLUMNS: False,
            BATCH_ANOMALY_MISSING_STATUS_COLUMNS: False,
            BATCH_ANOMALY_REVIEW_REQUIRED_BLANK_VALUES: False,
            BATCH_ANOMALY_PARSE_FAILED: False,
            BATCH_ANOMALY_REVIEW_QUEUE: False,
        }
        for file_id in file_ids
    }

    for item in files:
        if file_lifecycle_state(item) != LIFECYCLE_ACTIVE:
            continue
        if str(item.parse_status or "").strip().lower() == "failed":
            flags_by_file[item.id][BATCH_ANOMALY_PARSE_FAILED] = True

    rows = (
        db.query(NormalizedRecord)
        .join(UploadedFile, UploadedFile.id == NormalizedRecord.file_id)
        .filter(
            NormalizedRecord.file_id.in_(file_ids),
            NormalizedRecord.document_type == "order",
            NormalizedRecord.lifecycle_state == LIFECYCLE_ACTIVE,
            UploadedFile.lifecycle_state == LIFECYCLE_ACTIVE,
        )
        .all()
    )
    for row in rows:
        bucket = flags_by_file.setdefault(
            row.file_id,
            {
                BATCH_ANOMALY_MISSING_IDENTITY_COLUMNS: False,
                BATCH_ANOMALY_MISSING_STATUS_COLUMNS: False,
                BATCH_ANOMALY_REVIEW_REQUIRED_BLANK_VALUES: False,
                BATCH_ANOMALY_PARSE_FAILED: False,
                BATCH_ANOMALY_REVIEW_QUEUE: False,
            },
        )
        if _record_missing_columns(row, REVIEW_IDENTITY_FIELDS):
            bucket[BATCH_ANOMALY_MISSING_IDENTITY_COLUMNS] = True
        if _record_missing_columns(row, REVIEW_STATUS_FIELDS):
            bucket[BATCH_ANOMALY_MISSING_STATUS_COLUMNS] = True
        if any(field in REVIEW_IDENTITY_FIELDS for field in _record_blank_review_values(row)):
            bucket[BATCH_ANOMALY_REVIEW_REQUIRED_BLANK_VALUES] = True
        if is_review_queue_record(row):
            bucket[BATCH_ANOMALY_REVIEW_QUEUE] = True

    for file_id, flags in flags_by_file.items():
        codes = _finalize_batch_anomaly_codes(flags)
        details[file_id] = {
            "has_anomaly": bool(codes),
            "anomaly_codes": codes,
            "anomaly_count": len(codes),
        }
    return details


def _review_governance(record: NormalizedRecord) -> dict[str, Any]:
    payload = record.payload_json if isinstance(record.payload_json, dict) else {}
    governance = payload.get("governance")
    return governance if isinstance(governance, dict) else {}


def _set_review_decision(
    *,
    record: NormalizedRecord,
    actor: str,
    reason: str,
    decision: str,
) -> None:
    payload = dict(record.payload_json) if isinstance(record.payload_json, dict) else {}
    governance = payload.get("governance")
    if not isinstance(governance, dict):
        governance = {}
    governance["review_decision"] = decision
    governance["review_reason"] = reason[:1000]
    governance["reviewed_by"] = actor[:20]
    governance["reviewed_at"] = _utcnow().isoformat()
    payload["governance"] = governance
    record.payload_json = payload


def _record_meta(record: NormalizedRecord) -> dict[str, Any]:
    payload = dict(record.payload_json) if isinstance(record.payload_json, dict) else {}
    meta = payload.get("lifecycle_meta")
    if not isinstance(meta, dict):
        meta = {}
    payload["lifecycle_meta"] = meta
    record.payload_json = payload
    return meta


def _file_meta(file: UploadedFile) -> dict[str, Any]:
    meta = dict(file.meta_json) if isinstance(file.meta_json, dict) else {}
    file.meta_json = meta
    return meta


def _set_pre_delete_lifecycle_state(record: NormalizedRecord, state: str) -> None:
    meta = _record_meta(record)
    meta["pre_delete_lifecycle_state"] = state


def _get_pre_delete_lifecycle_state(record: NormalizedRecord) -> str:
    return str(_record_meta(record).get("pre_delete_lifecycle_state") or "").strip().lower()


def _set_file_pre_delete_lifecycle_state(file: UploadedFile, state: str) -> None:
    meta = _file_meta(file)
    meta["pre_delete_lifecycle_state"] = state


def _get_file_pre_delete_lifecycle_state(file: UploadedFile) -> str:
    return str(_file_meta(file).get("pre_delete_lifecycle_state") or "").strip().lower()


def _clear_record_delete_metadata(record: NormalizedRecord) -> None:
    record.deleted_at = None
    record.deleted_by = None
    record.delete_reason = None
    record.delete_origin = None


def _clear_file_delete_metadata(file: UploadedFile) -> None:
    file.deleted_at = None
    file.deleted_by = None
    file.delete_reason = None


def _mark_file_restored_out_of_recycle_bin(file: UploadedFile, *, actor: str, reason: str | None) -> None:
    _clear_file_delete_metadata(file)
    meta = _file_meta(file)
    meta.pop("pre_delete_lifecycle_state", None)
    file.restored_at = _utcnow()
    file.restored_by = actor[:20]
    file.restore_reason = reason[:1000] if reason else None


def _set_special_case_metadata(
    *,
    record: NormalizedRecord,
    actor: str,
    reason: str,
    note: str | None,
    source: str,
) -> None:
    payload = dict(record.payload_json) if isinstance(record.payload_json, dict) else {}
    governance = payload.get("governance")
    if not isinstance(governance, dict):
        governance = {}
    governance["special_case_reason"] = reason
    governance["special_case_note"] = (note or "").strip()[:1000] or None
    governance["special_case_by"] = actor[:20]
    governance["special_case_at"] = _utcnow().isoformat()
    governance["special_case_source"] = source
    payload["governance"] = governance
    record.payload_json = payload


def _clear_special_case_metadata(record: NormalizedRecord) -> None:
    if not isinstance(record.payload_json, dict):
        return
    payload = dict(record.payload_json)
    governance = payload.get("governance")
    if not isinstance(governance, dict):
        return
    governance = dict(governance)
    for key in ("special_case_reason", "special_case_note", "special_case_by", "special_case_at", "special_case_source"):
        governance.pop(key, None)
    payload["governance"] = governance
    record.payload_json = payload


def _record_rank(record: NormalizedRecord) -> tuple[float, int]:
    created = getattr(record, "created_at", None)
    created_ord = float(created.timestamp()) if created is not None else -1.0
    return (created_ord, int(getattr(record, "source_row", 0) or 0))


def _special_case_reason(record: NormalizedRecord) -> str | None:
    return str(_review_governance(record).get("special_case_reason") or "").strip() or None


def _special_case_note(record: NormalizedRecord) -> str | None:
    return str(_review_governance(record).get("special_case_note") or "").strip() or None


def special_case_source_code_for_record(record: NormalizedRecord) -> str:
    source = str(_review_governance(record).get("special_case_source") or "").strip().lower()
    if source in {"review_queue", "current"}:
        return source
    file = getattr(record, "file", None)
    actions = _file_meta(file).get("review_queue_actions") if isinstance(file, UploadedFile) else None
    if isinstance(actions, list):
        for action in reversed(actions):
            if not isinstance(action, dict):
                continue
            if action.get("record_id") == record.id and str(action.get("action") or "").strip().lower() == "move_to_special_case":
                return "review_queue"
    return "current"


def special_case_source_code_for_file(db: Session, file: UploadedFile) -> str | None:
    records = (
        db.query(NormalizedRecord)
        .filter(
            NormalizedRecord.file_id == file.id,
            NormalizedRecord.lifecycle_state == LIFECYCLE_SPECIAL_CASE,
            NormalizedRecord.version_status == STATUS_SPECIAL_CASE,
        )
        .all()
    )
    if not records:
        return None
    sources = {special_case_source_code_for_record(record) for record in records}
    if len(sources) == 1:
        return next(iter(sources))
    return "mixed"


def review_status_code(record: NormalizedRecord) -> str:
    if is_review_queue_record(record):
        return REVIEW_STATUS_PENDING
    decision = str(_review_governance(record).get("review_decision") or "").strip().lower()
    if decision.startswith("not_duplicate"):
        return REVIEW_STATUS_REVIEWED_NOT_DUPLICATE
    return REVIEW_STATUS_NONE


def effective_status_code(record: NormalizedRecord) -> str:
    if is_current_effective_record(record):
        return EFFECTIVE_STATUS_CURRENT
    if record_version_status(record) == STATUS_INACTIVE:
        return EFFECTIVE_STATUS_INACTIVE
    return EFFECTIVE_STATUS_RETAINED


def _append_review_queue_action(
    *,
    file: UploadedFile | None,
    record_id: str,
    action: str,
    actor: str,
    reason: str,
) -> None:
    if not isinstance(file, UploadedFile):
        return
    meta = dict(file.meta_json) if isinstance(file.meta_json, dict) else {}
    actions = meta.get("review_queue_actions")
    if not isinstance(actions, list):
        actions = []
    actions.append(
        {
            "record_id": record_id,
            "action": action,
            "actor": actor[:20],
            "reason": reason[:1000],
            "at": _utcnow().isoformat(),
        }
    )
    meta["review_queue_actions"] = actions[-100:]
    file.meta_json = meta


def _map_parse_status_to_file_status(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    if raw == "failed":
        return "failed"
    if raw == "parsing":
        return "running"
    if raw == "pending":
        return "queued"
    if raw == "parsed":
        return "succeeded"
    return "unknown"


def _build_file_status_summary(rows: list[tuple[str | None, int]]) -> str:
    if not rows:
        return "no_files"
    grouped: dict[str, int] = {k: 0 for k in _FILE_STATUS_ORDER}
    for parse_status, count in rows:
        grouped[_map_parse_status_to_file_status(parse_status)] += int(count or 0)
    active = [(status, grouped[status]) for status in _FILE_STATUS_ORDER if grouped[status] > 0]
    if not active:
        return "no_files"
    if len(active) == 1:
        return active[0][0]
    return " | ".join(f"{status}({count})" for status, count in active)


def _resolve_storage_path(f: UploadedFile) -> Path | None:
    candidates: list[Path] = []
    if f.storage_path:
        candidates.append(Path(f.storage_path))
    if f.storage_key:
        candidates.append(STORAGE_DIR / f.storage_key)
    if f.filename:
        candidates.append(STORAGE_DIR / f.job_id / f.filename)
    for path in candidates:
        if path.exists():
            return path
    return candidates[0] if candidates else None


def _record_group_ids(db: Session, record_ids: list[str]) -> list[str]:
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


def _cleanup_empty_groups(db: Session, group_ids: list[str]) -> None:
    if not group_ids:
        return
    for group_id in group_ids:
        remaining = db.query(func.count(GroupRecordLink.id)).filter(GroupRecordLink.group_id == group_id).scalar() or 0
        if remaining > 0:
            continue
        db.query(Alert).filter(Alert.group_id == group_id).delete(synchronize_session=False)
        group = db.get(MatchGroup, group_id)
        if group:
            db.delete(group)


def _resolve_open_alerts_for_record(
    db: Session,
    *,
    record: NormalizedRecord,
    reason: str,
) -> None:
    group_ids = _record_group_ids(db, [record.id])
    if not group_ids:
        return
    rows = db.query(Alert).filter(Alert.group_id.in_(group_ids), Alert.status == "open").all()
    for item in rows:
        payload = dict(item.payload_json or {})
        payload["resolution_reason"] = reason
        resolved_at = _utcnow().isoformat()
        payload["resolved_at"] = resolved_at
        payload["last_changed_at"] = resolved_at
        payload["resolved_by_review_action"] = True
        item.payload_json = payload
        item.status = "resolved"


def build_file_impact(db: Session, file_id: str) -> dict[str, Any]:
    f = db.get(UploadedFile, file_id)
    if not f:
        raise HTTPException(status_code=404, detail="Uploaded file not found")

    record_ids = [rid for (rid,) in db.query(NormalizedRecord.id).filter(NormalizedRecord.file_id == file_id).all()]
    group_ids = _record_group_ids(db, record_ids)
    alert_total = db.query(func.count(Alert.id)).filter(Alert.group_id.in_(group_ids)).scalar() or 0 if group_ids else 0
    open_alert_count = (
        db.query(func.count(Alert.id)).filter(Alert.group_id.in_(group_ids), Alert.status == "open").scalar() or 0
        if group_ids
        else 0
    )
    resolved_alert_count = (
        db.query(func.count(Alert.id)).filter(Alert.group_id.in_(group_ids), Alert.status == "resolved").scalar() or 0
        if group_ids
        else 0
    )
    return {
        "file_id": f.id,
        "filename": f.filename,
        "job_id": f.job_id,
        "document_type": f.document_type,
        "lifecycle_state": file_lifecycle_state(f),
        "normalized_record_count": len(record_ids),
        "group_link_count": db.query(func.count(GroupRecordLink.id)).filter(GroupRecordLink.record_id.in_(record_ids)).scalar() or 0
        if record_ids
        else 0,
        "match_group_count": len(group_ids),
        "alert_total": int(alert_total),
        "open_alert_count": int(open_alert_count),
        "resolved_alert_count": int(resolved_alert_count),
    }


def build_job_summary(db: Session, job: UploadJob) -> dict[str, Any]:
    uploaded_product_row_count = (
        db.query(
            func.coalesce(
                func.sum(
                    func.coalesce(UploadedFile.parsed_count, 0)
                    + func.coalesce(UploadedFile.auto_deleted_duplicate_count, 0)
                ),
                0,
            )
        )
        .filter(UploadedFile.job_id == job.id)
        .scalar()
        or 0
    )
    active_file_count = (
        db.query(func.count(UploadedFile.id))
        .filter(UploadedFile.job_id == job.id, UploadedFile.lifecycle_state == LIFECYCLE_ACTIVE)
        .scalar()
        or 0
    )
    recycle_bin_file_count = (
        db.query(func.count(UploadedFile.id))
        .filter(UploadedFile.job_id == job.id, UploadedFile.lifecycle_state == LIFECYCLE_RECYCLE_BIN)
        .scalar()
        or 0
    )
    archived_file_count = (
        db.query(func.count(UploadedFile.id))
        .filter(UploadedFile.job_id == job.id, UploadedFile.lifecycle_state == LIFECYCLE_ARCHIVED)
        .scalar()
        or 0
    )
    special_case_file_count = (
        db.query(func.count(UploadedFile.id))
        .filter(UploadedFile.job_id == job.id, UploadedFile.lifecycle_state == LIFECYCLE_SPECIAL_CASE)
        .scalar()
        or 0
    )
    auto_deleted_duplicate_count = (
        db.query(func.coalesce(func.sum(UploadedFile.auto_deleted_duplicate_count), 0))
        .filter(UploadedFile.job_id == job.id)
        .scalar()
        or 0
    )
    latest_file = (
        db.query(UploadedFile)
        .filter(UploadedFile.job_id == job.id, UploadedFile.lifecycle_state == LIFECYCLE_ACTIVE)
        .order_by(UploadedFile.created_at.desc())
        .first()
    )
    parse_failed_count = (
        db.query(func.count(UploadedFile.id))
        .filter(
            UploadedFile.job_id == job.id,
            UploadedFile.parse_status == "failed",
            UploadedFile.lifecycle_state == LIFECYCLE_ACTIVE,
        )
        .scalar()
        or 0
    )
    latest_task = (
        db.query(TaskRun)
        .filter(TaskRun.job_id == job.id)
        .order_by(TaskRun.updated_at.desc())
        .first()
    )
    file_status_rows = (
        db.query(UploadedFile.parse_status, func.count(UploadedFile.id))
        .filter(UploadedFile.job_id == job.id, UploadedFile.lifecycle_state == LIFECYCLE_ACTIVE)
        .group_by(UploadedFile.parse_status)
        .all()
    )
    open_alert_count = db.query(func.count(Alert.id)).filter(Alert.job_id == job.id, Alert.status == "open").scalar() or 0
    resolved_alert_count = (
        db.query(func.count(Alert.id)).filter(Alert.job_id == job.id, Alert.status == "resolved").scalar() or 0
    )
    active_record_count = (
        db.query(func.count(NormalizedRecord.id))
        .filter(NormalizedRecord.job_id == job.id, NormalizedRecord.lifecycle_state == LIFECYCLE_ACTIVE)
        .scalar()
        or 0
    )
    recycle_bin_record_count = (
        db.query(func.count(NormalizedRecord.id))
        .filter(NormalizedRecord.job_id == job.id, NormalizedRecord.lifecycle_state == LIFECYCLE_RECYCLE_BIN)
        .scalar()
        or 0
    )
    archived_record_count = (
        db.query(func.count(NormalizedRecord.id))
        .filter(NormalizedRecord.job_id == job.id, NormalizedRecord.lifecycle_state == LIFECYCLE_ARCHIVED)
        .scalar()
        or 0
    )
    special_case_record_count = (
        db.query(func.count(NormalizedRecord.id))
        .filter(NormalizedRecord.job_id == job.id, NormalizedRecord.lifecycle_state == LIFECYCLE_SPECIAL_CASE)
        .scalar()
        or 0
    )
    total_record_count = int(
        active_record_count + recycle_bin_record_count + archived_record_count + special_case_record_count
    )
    current_effective_record_count = (
        db.query(func.count(NormalizedRecord.id))
        .filter(
            NormalizedRecord.job_id == job.id,
            NormalizedRecord.lifecycle_state == LIFECYCLE_ACTIVE,
            NormalizedRecord.is_current_effective.is_(True),
        )
        .scalar()
        or 0
    )
    active_order_rows = (
        db.query(NormalizedRecord)
        .join(UploadedFile, UploadedFile.id == NormalizedRecord.file_id)
        .filter(
            NormalizedRecord.job_id == job.id,
            NormalizedRecord.document_type == "order",
            NormalizedRecord.lifecycle_state == LIFECYCLE_ACTIVE,
            UploadedFile.lifecycle_state == LIFECYCLE_ACTIVE,
        )
        .all()
    )
    review_queue_record_count = sum(1 for row in active_order_rows if is_review_queue_record(row))
    review_released_record_count = sum(
        1 for row in active_order_rows if record_version_status(row) == STATUS_REVIEW_RELEASED
    )
    total_file_count = int(active_file_count + recycle_bin_file_count + archived_file_count + special_case_file_count)
    anomaly_flags = {
        BATCH_ANOMALY_MISSING_IDENTITY_COLUMNS: False,
        BATCH_ANOMALY_MISSING_STATUS_COLUMNS: False,
        BATCH_ANOMALY_REVIEW_REQUIRED_BLANK_VALUES: False,
        BATCH_ANOMALY_PARSE_FAILED: int(parse_failed_count) > 0,
        BATCH_ANOMALY_REVIEW_QUEUE: int(review_queue_record_count) > 0,
    }
    for row in active_order_rows:
        if _record_missing_columns(row, REVIEW_IDENTITY_FIELDS):
            anomaly_flags[BATCH_ANOMALY_MISSING_IDENTITY_COLUMNS] = True
        if _record_missing_columns(row, REVIEW_STATUS_FIELDS):
            anomaly_flags[BATCH_ANOMALY_MISSING_STATUS_COLUMNS] = True
        if any(field in REVIEW_IDENTITY_FIELDS for field in _record_blank_review_values(row)):
            anomaly_flags[BATCH_ANOMALY_REVIEW_REQUIRED_BLANK_VALUES] = True
    anomaly_codes = _finalize_batch_anomaly_codes(anomaly_flags)

    return {
        "job_id": job.id,
        "status": job.status,
        "created_by": job.created_by,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "uploaded_product_row_count": int(uploaded_product_row_count),
        "file_count": int(active_file_count),
        "active_file_count": int(active_file_count),
        "recycle_bin_file_count": int(recycle_bin_file_count),
        "archived_file_count": int(archived_file_count),
        "special_case_file_count": int(special_case_file_count),
        "total_file_count": total_file_count,
        "auto_deleted_duplicate_count": int(auto_deleted_duplicate_count),
        "latest_filename": latest_file.filename if latest_file else None,
        "last_upload_at": latest_file.created_at if latest_file else None,
        "latest_task_id": latest_task.id if latest_task else None,
        "latest_task_status": latest_task.status if latest_task else None,
        "file_status_summary": _build_file_status_summary(file_status_rows),
        "latest_task_updated_at": latest_task.updated_at if latest_task else None,
        "open_alert_count": int(open_alert_count),
        "resolved_alert_count": int(resolved_alert_count),
        "parse_failed_count": int(parse_failed_count),
        "active_record_count": int(active_record_count),
        "recycle_bin_record_count": int(recycle_bin_record_count),
        "archived_record_count": int(archived_record_count),
        "special_case_record_count": int(special_case_record_count),
        "total_record_count": total_record_count,
        "current_effective_record_count": int(current_effective_record_count),
        "review_queue_record_count": int(review_queue_record_count),
        "review_released_record_count": int(review_released_record_count),
        "is_empty_shell": total_file_count == 0 and total_record_count == 0,
        "has_anomaly": bool(anomaly_codes),
        "anomaly_codes": anomaly_codes,
        "anomaly_count": len(anomaly_codes),
    }


def is_review_queue_record(record: NormalizedRecord) -> bool:
    if record.document_type != "order":
        return False
    if record_lifecycle_state(record) != LIFECYCLE_ACTIVE:
        return False
    file = getattr(record, "file", None)
    if isinstance(file, UploadedFile) and file_lifecycle_state(file) != LIFECYCLE_ACTIVE:
        return False
    if record_version_status(record) != STATUS_REVIEW_PENDING:
        return False
    return not is_current_effective_record(record)


def build_review_queue_items(db: Session) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    file_items: dict[str, dict[str, Any]] = {}
    rows = (
        db.query(NormalizedRecord)
        .join(UploadedFile, UploadedFile.id == NormalizedRecord.file_id)
        .filter(
            NormalizedRecord.document_type == "order",
            NormalizedRecord.lifecycle_state == LIFECYCLE_ACTIVE,
            UploadedFile.lifecycle_state == LIFECYCLE_ACTIVE,
            NormalizedRecord.version_status == STATUS_REVIEW_PENDING,
            NormalizedRecord.is_current_effective.is_(False),
        )
        .order_by(NormalizedRecord.created_at.desc(), NormalizedRecord.source_row.asc())
        .all()
    )
    for record in rows:
        if not is_review_queue_record(record):
            continue
        file = record.file if isinstance(getattr(record, "file", None), UploadedFile) else None
        if file is not None and file.id not in file_items:
            file_items[file.id] = {
                "object_type": "file",
                "object_id": file.id,
                "job_id": file.job_id,
                "file_id": file.id,
                "document_type": file.document_type,
                "filename": file.filename,
                "lifecycle_state": file_lifecycle_state(file),
                "review_status": "pending_review",
                "effective_status": "",
                "version_status": "",
                "identity_mode": "",
                "governance_reason": "该文件包含待复核记录",
                "created_at": file.created_at,
            }
        core = get_core(record.payload_json)
        governance = record.payload_json.get("governance") if isinstance(record.payload_json, dict) else {}
        if not isinstance(governance, dict):
            governance = {}
        items.append(
            {
                "object_type": "record",
                "object_id": record.id,
                "job_id": record.job_id,
                "file_id": record.file_id,
                "document_type": record.document_type,
                "source_row": int(record.source_row or 0),
                "customer_order_no": core.get("customer_order_no"),
                "entry_line_no": core.get("entry_line_no"),
                "biz_date": core.get("biz_date"),
                "item_code": core.get("item_code"),
                "filename": record.file.filename if record.file else None,
                "lifecycle_state": record_lifecycle_state(record),
                "version_status": record_version_status(record),
                "review_status": review_status_code(record),
                "effective_status": effective_status_code(record),
                "identity_mode": governance.get("identity_mode"),
                "governance_reason": governance.get("governance_reason"),
                "created_at": record.created_at,
            }
        )
    items = list(file_items.values()) + items
    items.sort(
        key=lambda item: (
            str(item.get("created_at") or ""),
            1 if item.get("object_type") == "record" else 2,
        ),
        reverse=True,
    )
    return items


def resolve_review_queue_primary_record(db: Session, *, record: NormalizedRecord) -> NormalizedRecord | None:
    if not is_review_queue_record(record):
        raise HTTPException(status_code=409, detail="Record is not in review queue.")
    decision = classify_order_record(db, payload_json=record.payload_json or {}, exclude_record_id=record.id)
    return decision.candidate


def build_review_queue_compare(db: Session, *, record: NormalizedRecord) -> dict[str, Any]:
    primary = resolve_review_queue_primary_record(db, record=record)
    review_core = get_core(record.payload_json)
    primary_core = get_core(primary.payload_json) if primary else {}
    identity_fields = ("customer_order_no", "entry_line_no", "biz_date", "item_code")
    identity_compare = {
        field: {
            "review": review_core.get(field),
            "primary": primary_core.get(field),
            "same": review_core.get(field) == primary_core.get(field),
        }
        for field in identity_fields
    }
    status_compare = {
        field: {
            "review": review_core.get(field),
            "primary": primary_core.get(field),
            "same": review_core.get(field) == primary_core.get(field),
        }
        for field in COMPARE_FIELDS
    }
    return {
        "record_id": record.id,
        "primary_record_id": primary.id if primary else None,
        "identity_compare": identity_compare,
        "status_compare": status_compare,
    }


def build_review_queue_reason(record: NormalizedRecord) -> dict[str, Any]:
    governance = get_governance(record.payload_json)
    governance_reason = str(governance.get("governance_reason") or "").strip()
    missing_required_columns = _record_missing_columns(record, REVIEW_REQUIRED_FIELDS)
    blank_identity_values = [
        field for field in REVIEW_IDENTITY_FIELDS if field in _record_blank_review_values(record)
    ]

    if governance_reason.startswith("pending_review_missing_required_columns:"):
        reason_code = "missing_required_columns"
        reason_label = "缺少关键列，系统先放入待复核区"
    elif governance_reason.startswith("pending_review_missing_identity_values:"):
        reason_code = "missing_identity_values"
        reason_label = "认人字段有空值，系统先放入待复核区"
    elif governance_reason in {"pending_review_legacy_fallback_match", "pending_review_legacy_bridge_match"}:
        reason_code = "legacy_hold"
        reason_label = "命中待复核规则，系统先冻结，等待人工处理"
    elif governance_reason == "restored_to_review_queue_from_special_case":
        reason_code = "returned_from_special_case"
        reason_label = "该记录从特殊情况区放回待复核区，等待重新处理"
    else:
        reason_code = "review_queue_hold"
        reason_label = "命中待复核规则，系统先放入待复核区"

    return {
        "record_id": record.id,
        "reason_code": reason_code,
        "reason_label": reason_label,
        "governance_reason": governance_reason,
        "missing_required_columns": missing_required_columns,
        "blank_identity_values": blank_identity_values,
    }


def validate_special_case_reason(reason: str) -> str:
    normalized = str(reason or "").strip()
    if not normalized:
        return "其他特殊完成"
    if normalized in SPECIAL_CASE_REASONS:
        return normalized
    # Allow free-form reason text from audit UI while keeping a bounded payload size.
    return normalized[:1000]


def move_record_to_special_case(
    db: Session,
    *,
    record: NormalizedRecord,
    actor: str,
    reason: str,
    note: str | None,
) -> list[str]:
    if record.document_type != "order":
        raise HTTPException(status_code=409, detail="Only order records can enter special-case zone.")
    if record_lifecycle_state(record) != LIFECYCLE_ACTIVE:
        raise HTTPException(status_code=409, detail="Only current-data or review-queue order records can enter special-case zone.")

    validated_reason = validate_special_case_reason(reason)
    file = getattr(record, "file", None)
    source = "review_queue" if is_review_queue_record(record) else "current"
    if source == "review_queue":
        _append_review_queue_action(
            file=file if isinstance(file, UploadedFile) else None,
            record_id=record.id,
            action="move_to_special_case",
            actor=actor,
            reason=validated_reason if not note else f"{validated_reason} / {note.strip()[:1000]}",
        )

    record.lifecycle_state = LIFECYCLE_SPECIAL_CASE
    _clear_record_delete_metadata(record)
    record.version_status = STATUS_SPECIAL_CASE
    record.is_current_effective = False
    record.duplicate_of_record_id = None
    record.superseded_by_record_id = None
    record.supersedes_record_id = None
    record.archived_at = _utcnow()
    record.archived_by = actor[:20]
    record.archive_reason = validated_reason
    _set_review_decision(record=record, actor=actor, reason=validated_reason if not note else f"{validated_reason} / {note.strip()[:1000]}", decision="moved_to_special_case")
    _set_special_case_metadata(record=record, actor=actor, reason=validated_reason, note=note, source=source)
    db.flush()
    _resolve_open_alerts_for_record(db, record=record, reason="moved_to_special_case")
    sync_record_governance(record, governance_reason="moved_to_special_case")

    if isinstance(file, UploadedFile):
        _recalculate_file_lifecycle_state(db, file, actor=actor, reason=validated_reason)

    return [record.job_id]


def return_record_to_job_list_from_special_case(
    db: Session,
    *,
    record: NormalizedRecord,
    actor: str,
    reason: str | None,
    recalculate_file: bool = True,
) -> list[str]:
    if record_lifecycle_state(record) != LIFECYCLE_SPECIAL_CASE:
        raise HTTPException(status_code=409, detail="Record is not in special-case zone.")

    _clear_special_case_metadata(record)
    record.lifecycle_state = LIFECYCLE_ACTIVE
    _clear_record_delete_metadata(record)
    record.archived_at = None
    record.archived_by = None
    record.archive_reason = None
    record.pre_delete_version_status = None
    record.pre_delete_is_current_effective = None

    if record.document_type == "order":
        decision = classify_order_record(db, payload_json=record.payload_json or {}, exclude_record_id=record.id)
        governance_reason = _governance_reason_for_record(decision)
        record.duplicate_of_record_id = None
        record.superseded_by_record_id = None
        record.supersedes_record_id = None
        if should_hold_for_review(decision):
            record.version_status = STATUS_REVIEW_PENDING
            record.is_current_effective = False
        elif decision.change_type == CHANGE_DUPLICATE:
            record.version_status = STATUS_DUPLICATE_SHADOW
            record.is_current_effective = False
            record.duplicate_of_record_id = decision.candidate.id if decision.candidate else None
        elif decision.candidate is None:
            record.version_status = STATUS_CURRENT
            record.is_current_effective = True
        elif decision.change_type == CHANGE_UPDATE and _record_rank(record) > _record_rank(decision.candidate):
            record.version_status = STATUS_CURRENT
            record.is_current_effective = True
            record.supersedes_record_id = decision.candidate.id
            decision.candidate.lifecycle_state = LIFECYCLE_ACTIVE
            decision.candidate.version_status = STATUS_INACTIVE
            decision.candidate.is_current_effective = False
            decision.candidate.duplicate_of_record_id = None
            decision.candidate.supersedes_record_id = None
            decision.candidate.superseded_by_record_id = record.id
            sync_record_governance(
                decision.candidate,
                governance_reason="superseded_by_returned_special_case_record",
            )
        else:
            record.version_status = STATUS_RESTORED_HISTORY
            record.is_current_effective = False
            if decision.candidate is not None:
                record.superseded_by_record_id = decision.candidate.id

        sync_record_governance(
            record,
            change_type=decision.change_type,
            identity_mode=decision.identity_mode,
            governance_reason=governance_reason,
            compared_fields_snapshot=decision.compared_fields_snapshot,
        )
    else:
        record.is_current_effective = bool(record.pre_delete_is_current_effective)

    record.restored_at = _utcnow()
    record.restored_by = actor[:20]
    record.restore_reason = reason[:1000] if reason else None

    file = getattr(record, "file", None)
    if recalculate_file and isinstance(file, UploadedFile):
        _recalculate_file_lifecycle_state(db, file, actor=actor, reason=reason)
    return [record.job_id]


def return_record_to_review_queue_from_special_case(
    db: Session,
    *,
    record: NormalizedRecord,
    actor: str,
    reason: str | None,
    recalculate_file: bool = True,
) -> list[str]:
    if record_lifecycle_state(record) != LIFECYCLE_SPECIAL_CASE:
        raise HTTPException(status_code=409, detail="Record is not in special-case zone.")
    if special_case_source_code_for_record(record) != "review_queue":
        raise HTTPException(status_code=409, detail="Only special-case records originating from review queue can return there.")

    _clear_special_case_metadata(record)
    record.lifecycle_state = LIFECYCLE_ACTIVE
    record.version_status = STATUS_REVIEW_PENDING
    record.is_current_effective = False
    record.duplicate_of_record_id = None
    record.superseded_by_record_id = None
    record.supersedes_record_id = None
    _clear_record_delete_metadata(record)
    record.archived_at = None
    record.archived_by = None
    record.archive_reason = None
    record.pre_delete_version_status = None
    record.pre_delete_is_current_effective = None
    record.restored_at = _utcnow()
    record.restored_by = actor[:20]
    record.restore_reason = reason[:1000] if reason else None
    sync_record_governance(record, governance_reason="restored_to_review_queue_from_special_case")

    file = getattr(record, "file", None)
    if recalculate_file and isinstance(file, UploadedFile):
        _recalculate_file_lifecycle_state(db, file, actor=actor, reason=reason)
    return [record.job_id]


def return_file_to_job_list_from_special_case(
    db: Session,
    *,
    file: UploadedFile,
    actor: str,
    reason: str | None,
) -> list[str]:
    if file_lifecycle_state(file) != LIFECYCLE_SPECIAL_CASE:
        raise HTTPException(status_code=409, detail="File is not in special-case zone.")

    records = (
        db.query(NormalizedRecord)
        .filter(NormalizedRecord.file_id == file.id)
        .order_by(NormalizedRecord.created_at.asc(), NormalizedRecord.source_row.asc())
        .all()
    )
    for record in records:
        if record_lifecycle_state(record) != LIFECYCLE_SPECIAL_CASE:
            continue
        return_record_to_job_list_from_special_case(
            db,
            record=record,
            actor=actor,
            reason=reason,
            recalculate_file=False,
        )

    file.lifecycle_state = LIFECYCLE_ACTIVE
    file.deleted_at = None
    file.deleted_by = None
    file.delete_reason = None
    file.archived_at = None
    file.archived_by = None
    file.archive_reason = None
    file.restored_at = _utcnow()
    file.restored_by = actor[:20]
    file.restore_reason = reason[:1000] if reason else None
    _recalculate_file_lifecycle_state(db, file, actor=actor, reason=reason)
    return [file.job_id]


def return_file_to_review_queue_from_special_case(
    db: Session,
    *,
    file: UploadedFile,
    actor: str,
    reason: str | None,
) -> list[str]:
    if file_lifecycle_state(file) != LIFECYCLE_SPECIAL_CASE:
        raise HTTPException(status_code=409, detail="File is not in special-case zone.")
    if special_case_source_code_for_file(db, file) != "review_queue":
        raise HTTPException(status_code=409, detail="Only special-case files originating from review queue can return there.")

    records = (
        db.query(NormalizedRecord)
        .filter(NormalizedRecord.file_id == file.id)
        .order_by(NormalizedRecord.created_at.asc(), NormalizedRecord.source_row.asc())
        .all()
    )
    for record in records:
        if record_lifecycle_state(record) != LIFECYCLE_SPECIAL_CASE:
            continue
        return_record_to_review_queue_from_special_case(
            db,
            record=record,
            actor=actor,
            reason=reason,
            recalculate_file=False,
        )

    file.lifecycle_state = LIFECYCLE_ACTIVE
    file.deleted_at = None
    file.deleted_by = None
    file.delete_reason = None
    file.delete_origin = None
    file.archived_at = None
    file.archived_by = None
    file.archive_reason = None
    file.pre_delete_lifecycle_state = None
    file.restored_at = _utcnow()
    file.restored_by = actor[:20]
    file.restore_reason = reason[:1000] if reason else None
    _recalculate_file_lifecycle_state(db, file, actor=actor, reason=reason)
    return [file.job_id]


def build_file_review_counts(db: Session, file_ids: list[str]) -> dict[str, dict[str, int]]:
    counts = {
        file_id: {
            "review_queue_record_count": 0,
            "review_released_record_count": 0,
        }
        for file_id in file_ids
    }
    if not file_ids:
        return counts

    rows = (
        db.query(NormalizedRecord)
        .join(UploadedFile, UploadedFile.id == NormalizedRecord.file_id)
        .filter(
            NormalizedRecord.file_id.in_(file_ids),
            NormalizedRecord.document_type == "order",
            NormalizedRecord.lifecycle_state == LIFECYCLE_ACTIVE,
            UploadedFile.lifecycle_state == LIFECYCLE_ACTIVE,
        )
        .all()
    )
    for row in rows:
        bucket = counts.setdefault(
            row.file_id,
            {"review_queue_record_count": 0, "review_released_record_count": 0},
        )
        if is_review_queue_record(row):
            bucket["review_queue_record_count"] += 1
        elif record_version_status(row) == STATUS_REVIEW_RELEASED:
            bucket["review_released_record_count"] += 1
    return counts


def build_record_impact(db: Session, record_id: str) -> dict[str, Any]:
    record = db.get(NormalizedRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Normalized record not found")

    group_ids = _record_group_ids(db, [record_id])
    alert_total = db.query(func.count(Alert.id)).filter(Alert.group_id.in_(group_ids)).scalar() or 0 if group_ids else 0
    open_alert_count = (
        db.query(func.count(Alert.id)).filter(Alert.group_id.in_(group_ids), Alert.status == "open").scalar() or 0
        if group_ids
        else 0
    )
    resolved_alert_count = (
        db.query(func.count(Alert.id)).filter(Alert.group_id.in_(group_ids), Alert.status == "resolved").scalar() or 0
        if group_ids
        else 0
    )
    core = get_core(record.payload_json)
    return {
        "record_id": record.id,
        "job_id": record.job_id,
        "file_id": record.file_id,
        "document_type": record.document_type,
        "source_row": int(record.source_row or 0),
        "customer_order_no": core.get("customer_order_no"),
        "entry_line_no": core.get("entry_line_no"),
        "biz_date": core.get("biz_date"),
        "item_code": core.get("item_code"),
        "lifecycle_state": record_lifecycle_state(record),
        "version_status": record_version_status(record),
        "is_current_effective": is_current_effective_record(record),
        "match_group_count": len(group_ids),
        "alert_total": int(alert_total),
        "open_alert_count": int(open_alert_count),
        "resolved_alert_count": int(resolved_alert_count),
    }


def _move_record_to_recycle_bin(
    db: Session,
    record: NormalizedRecord,
    *,
    actor: str,
    reason: str | None,
    origin: str,
) -> None:
    prior_lifecycle = record_lifecycle_state(record)
    if prior_lifecycle not in {LIFECYCLE_ACTIVE, LIFECYCLE_ARCHIVED, LIFECYCLE_SPECIAL_CASE}:
        raise HTTPException(status_code=409, detail="Record is not in a deletable state.")
    record.pre_delete_version_status = record_version_status(record)
    record.pre_delete_is_current_effective = is_current_effective_record(record)
    _set_pre_delete_lifecycle_state(record, prior_lifecycle)
    record.lifecycle_state = LIFECYCLE_RECYCLE_BIN
    record.deleted_at = _utcnow()
    record.deleted_by = actor[:20]
    record.delete_reason = reason[:1000] if reason else None
    record.delete_origin = origin
    record.is_current_effective = False
    sync_record_governance(record, governance_reason=f"{origin}_recycle_bin")
    file = getattr(record, "file", None)
    if isinstance(file, UploadedFile):
        _recalculate_file_lifecycle_state(db, file, actor=actor, reason=reason)


def soft_delete_record(
    db: Session,
    *,
    record: NormalizedRecord,
    actor: str,
    reason: str | None,
) -> list[str]:
    origin = DELETE_ORIGIN_MANUAL_RECORD
    prior_lifecycle = record_lifecycle_state(record)
    if prior_lifecycle == LIFECYCLE_ARCHIVED:
        origin = DELETE_ORIGIN_ARCHIVED
    elif prior_lifecycle == LIFECYCLE_SPECIAL_CASE:
        origin = DELETE_ORIGIN_SPECIAL_CASE
    _move_record_to_recycle_bin(db, record, actor=actor, reason=reason, origin=origin)
    return [record.job_id]


def soft_delete_file(
    db: Session,
    *,
    file: UploadedFile,
    actor: str,
    reason: str | None,
) -> list[str]:
    prior_lifecycle = file_lifecycle_state(file)
    if prior_lifecycle not in {LIFECYCLE_ACTIVE, LIFECYCLE_ARCHIVED, LIFECYCLE_SPECIAL_CASE}:
        raise HTTPException(status_code=409, detail="File is not in a deletable state.")
    _set_file_pre_delete_lifecycle_state(file, prior_lifecycle)
    file.lifecycle_state = LIFECYCLE_RECYCLE_BIN
    file.deleted_at = _utcnow()
    file.deleted_by = actor[:20]
    file.delete_reason = reason[:1000] if reason else None
    records = (
        db.query(NormalizedRecord)
        .filter(NormalizedRecord.file_id == file.id)
        .order_by(NormalizedRecord.created_at.asc(), NormalizedRecord.source_row.asc())
        .all()
    )
    for record in records:
        if record_lifecycle_state(record) not in {LIFECYCLE_ACTIVE, LIFECYCLE_ARCHIVED, LIFECYCLE_SPECIAL_CASE}:
            continue
        origin = DELETE_ORIGIN_MANUAL_FILE
        if record_lifecycle_state(record) == LIFECYCLE_ARCHIVED:
            origin = DELETE_ORIGIN_ARCHIVED
        elif record_lifecycle_state(record) == LIFECYCLE_SPECIAL_CASE:
            origin = DELETE_ORIGIN_SPECIAL_CASE
        _move_record_to_recycle_bin(db, record, actor=actor, reason=reason, origin=origin)
    return [file.job_id]


def _restore_active_order_record(db: Session, record: NormalizedRecord) -> None:
    prior_version = (record.pre_delete_version_status or record_version_status(record) or STATUS_CURRENT).strip()
    prior_current = (
        bool(record.pre_delete_is_current_effective)
        if record.pre_delete_is_current_effective is not None
        else prior_version == STATUS_CURRENT
    )
    if not prior_current:
        record.version_status = prior_version
        record.is_current_effective = False
        sync_record_governance(record, governance_reason="restored_non_current_history")
        return

    decision = classify_order_record(db, payload_json=record.payload_json or {}, exclude_record_id=record.id)
    if decision.candidate is None:
        record.version_status = STATUS_CURRENT
        record.is_current_effective = True
        record.superseded_by_record_id = None
        sync_record_governance(record, governance_reason="restored_as_current")
        return

    record.version_status = STATUS_RESTORED_HISTORY
    record.is_current_effective = False
    sync_record_governance(record, governance_reason="restored_history_due_to_current_conflict")


def _restore_record_to_original_completed_zone(
    record: NormalizedRecord,
    *,
    actor: str,
    reason: str | None,
    lifecycle_state: str,
) -> None:
    record.lifecycle_state = lifecycle_state
    _clear_record_delete_metadata(record)
    record.restored_at = _utcnow()
    record.restored_by = actor[:20]
    record.restore_reason = reason[:1000] if reason else None
    if record.pre_delete_version_status:
        record.version_status = record.pre_delete_version_status
    elif lifecycle_state == LIFECYCLE_SPECIAL_CASE:
        record.version_status = STATUS_SPECIAL_CASE
    record.is_current_effective = False
    governance_reason = (
        "restored_to_original_archived_zone"
        if lifecycle_state == LIFECYCLE_ARCHIVED
        else "restored_to_original_special_case_zone"
    )
    sync_record_governance(record, governance_reason=governance_reason)


def restore_record(
    db: Session,
    *,
    record: NormalizedRecord,
    actor: str,
    reason: str | None,
) -> list[str]:
    if record_lifecycle_state(record) != LIFECYCLE_RECYCLE_BIN:
        raise HTTPException(status_code=409, detail="Record is not in recycle bin.")
    prior_lifecycle = _get_pre_delete_lifecycle_state(record)
    file = getattr(record, "file", None)
    if prior_lifecycle in {LIFECYCLE_ARCHIVED, LIFECYCLE_SPECIAL_CASE}:
        _restore_record_to_original_completed_zone(
            record,
            actor=actor,
            reason=reason,
            lifecycle_state=prior_lifecycle,
        )
        if isinstance(file, UploadedFile):
            _recalculate_file_lifecycle_state(db, file, actor=actor, reason=reason)
        return []

    record.lifecycle_state = LIFECYCLE_ACTIVE
    _clear_record_delete_metadata(record)
    record.restored_at = _utcnow()
    record.restored_by = actor[:20]
    record.restore_reason = reason[:1000] if reason else None

    if record.document_type == "order":
        _restore_active_order_record(db, record)
    else:
        if record.pre_delete_version_status:
            record.version_status = record.pre_delete_version_status
        if record.pre_delete_is_current_effective is not None:
            record.is_current_effective = bool(record.pre_delete_is_current_effective)
        sync_record_governance(record, governance_reason="restored_non_order_record")

    if isinstance(file, UploadedFile):
        _recalculate_file_lifecycle_state(db, file, actor=actor, reason=reason)
    return [record.job_id]


def restore_file(
    db: Session,
    *,
    file: UploadedFile,
    actor: str,
    reason: str | None,
) -> list[str]:
    if file_lifecycle_state(file) != LIFECYCLE_RECYCLE_BIN:
        raise HTTPException(status_code=409, detail="File is not in recycle bin.")
    prior_lifecycle = _get_file_pre_delete_lifecycle_state(file)
    restore_to_original_completed_zone = prior_lifecycle in {LIFECYCLE_ARCHIVED, LIFECYCLE_SPECIAL_CASE}
    file.lifecycle_state = prior_lifecycle if restore_to_original_completed_zone else LIFECYCLE_ACTIVE
    _clear_file_delete_metadata(file)
    file.restored_at = _utcnow()
    file.restored_by = actor[:20]
    file.restore_reason = reason[:1000] if reason else None
    records = (
        db.query(NormalizedRecord)
        .filter(NormalizedRecord.file_id == file.id)
        .order_by(
            NormalizedRecord.is_current_effective.asc(),
            NormalizedRecord.created_at.asc(),
            NormalizedRecord.source_row.asc(),
        )
        .all()
    )
    for record in records:
        if record_lifecycle_state(record) != LIFECYCLE_RECYCLE_BIN:
            continue
        restore_record(db, record=record, actor=actor, reason=reason)
    if records:
        _recalculate_file_lifecycle_state(db, file, actor=actor, reason=reason)
    if restore_to_original_completed_zone:
        return []
    return [file.job_id]


def _finalize_archive_file_state(
    file: UploadedFile,
    *,
    actor: str,
    reason: str | None,
) -> None:
    file.lifecycle_state = LIFECYCLE_ARCHIVED
    file.archived_at = _utcnow()
    file.archived_by = actor[:20]
    file.archive_reason = reason[:1000] if reason else None


def _archive_active_record(
    db: Session,
    *,
    record: NormalizedRecord,
    actor: str,
    reason: str | None,
    governance_reason: str,
    resolution_reason: str,
) -> None:
    record.lifecycle_state = LIFECYCLE_ARCHIVED
    record.archived_at = _utcnow()
    record.archived_by = actor[:20]
    record.archive_reason = reason[:1000] if reason else None
    record.is_current_effective = False
    db.flush()
    _resolve_open_alerts_for_record(db, record=record, reason=resolution_reason)
    sync_record_governance(record, governance_reason=governance_reason)
    file = getattr(record, "file", None)
    if isinstance(file, UploadedFile) and file_lifecycle_state(file) == LIFECYCLE_ACTIVE:
        remaining = _active_records_for_file(db, file.id)
        if not remaining:
            _finalize_archive_file_state(file, actor=actor, reason=reason)


def archive_record(
    db: Session,
    *,
    record: NormalizedRecord,
    actor: str,
    reason: str | None,
) -> None:
    lifecycle_state = record_lifecycle_state(record)
    if lifecycle_state == LIFECYCLE_RECYCLE_BIN:
        record.lifecycle_state = LIFECYCLE_ARCHIVED
        record.archived_at = _utcnow()
        record.archived_by = actor[:20]
        record.archive_reason = reason[:1000] if reason else None
        record.is_current_effective = False
        sync_record_governance(record, governance_reason="archived_from_recycle_bin")
        return
    if lifecycle_state != LIFECYCLE_ACTIVE or not is_auto_archive_candidate_record(record):
        raise HTTPException(
            status_code=409,
            detail="Only recycle-bin records or completed current-effective order records can be archived.",
        )
    governance_reason = (
        "archived_auto_completed_order"
        if str(reason or "").strip() == ARCHIVE_REASON_AUTO_COMPLETED
        else "archived_manual_completed_confirmed"
    )
    resolution_reason = (
        "archived_auto_completed_order"
        if str(reason or "").strip() == ARCHIVE_REASON_AUTO_COMPLETED
        else "archived_manual_completed_confirmed"
    )
    _archive_active_record(
        db,
        record=record,
        actor=actor,
        reason=reason,
        governance_reason=governance_reason,
        resolution_reason=resolution_reason,
    )


def archive_file(
    db: Session,
    *,
    file: UploadedFile,
    actor: str,
    reason: str | None,
) -> None:
    lifecycle_state = file_lifecycle_state(file)
    if lifecycle_state == LIFECYCLE_RECYCLE_BIN:
        _finalize_archive_file_state(file, actor=actor, reason=reason)
        records = db.query(NormalizedRecord).filter(NormalizedRecord.file_id == file.id).all()
        for record in records:
            if record_lifecycle_state(record) != LIFECYCLE_RECYCLE_BIN:
                continue
            archive_record(db, record=record, actor=actor, reason=reason)
        return
    if lifecycle_state == LIFECYCLE_ACTIVE and file.document_type == "order":
        raise HTTPException(
            status_code=409,
            detail="Current-data archive is record-based. Please archive completed order records instead.",
        )
    raise HTTPException(status_code=409, detail="Only recycle-bin files can be archived.")


def auto_archive_completed_orders_for_job(
    db: Session,
    *,
    job_id: str,
    actor: str = "system",
) -> dict[str, int]:
    archived_file_ids: set[str] = set()
    archived_record_ids: set[str] = set()

    remaining_records = (
        db.query(NormalizedRecord)
        .join(UploadedFile, UploadedFile.id == NormalizedRecord.file_id)
        .filter(
            NormalizedRecord.job_id == job_id,
            NormalizedRecord.document_type == "order",
            NormalizedRecord.lifecycle_state == LIFECYCLE_ACTIVE,
            UploadedFile.lifecycle_state == LIFECYCLE_ACTIVE,
            NormalizedRecord.is_current_effective.is_(True),
        )
        .order_by(NormalizedRecord.created_at.asc(), NormalizedRecord.source_row.asc())
        .all()
    )
    for record in remaining_records:
        if record.id in archived_record_ids:
            continue
        if not is_auto_archive_candidate_record(record):
            continue
        file = getattr(record, "file", None)
        archive_record(db, record=record, actor=actor, reason=ARCHIVE_REASON_AUTO_COMPLETED)
        archived_record_ids.add(record.id)
        if isinstance(file, UploadedFile) and file_lifecycle_state(file) == LIFECYCLE_ARCHIVED:
            archived_file_ids.add(file.id)

    return {
        "archived_file_count": len(archived_file_ids),
        "archived_record_count": len(archived_record_ids),
    }


def build_record_hard_delete_preview(db: Session, record_id: str) -> dict[str, Any]:
    impact = build_record_impact(db, record_id)
    record = db.get(NormalizedRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Normalized record not found")
    impact["eligible"] = record_lifecycle_state(record) == LIFECYCLE_RECYCLE_BIN
    return impact


def build_file_hard_delete_preview(db: Session, file_id: str) -> dict[str, Any]:
    impact = build_file_impact(db, file_id)
    file = db.get(UploadedFile, file_id)
    if not file:
        raise HTTPException(status_code=404, detail="Uploaded file not found")
    impact["eligible"] = file_lifecycle_state(file) == LIFECYCLE_RECYCLE_BIN
    return impact


def hard_delete_record(db: Session, *, record: NormalizedRecord) -> None:
    if record_lifecycle_state(record) != LIFECYCLE_RECYCLE_BIN:
        raise HTTPException(status_code=409, detail="Only recycle-bin records can be hard-deleted.")
    group_ids = _record_group_ids(db, [record.id])
    db.delete(record)
    db.flush()
    _cleanup_empty_groups(db, group_ids)


def hard_delete_file(db: Session, *, file: UploadedFile) -> None:
    if file_lifecycle_state(file) != LIFECYCLE_RECYCLE_BIN:
        raise HTTPException(status_code=409, detail="Only recycle-bin files can be hard-deleted.")
    record_ids = [rid for (rid,) in db.query(NormalizedRecord.id).filter(NormalizedRecord.file_id == file.id).all()]
    group_ids = _record_group_ids(db, record_ids)
    path = _resolve_storage_path(file)
    db.delete(file)
    db.flush()
    _cleanup_empty_groups(db, group_ids)
    if path:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


def build_recycle_bin_items(db: Session) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for file in (
        db.query(UploadedFile)
        .filter(UploadedFile.lifecycle_state == LIFECYCLE_RECYCLE_BIN)
        .order_by(UploadedFile.deleted_at.desc(), UploadedFile.created_at.desc())
        .all()
    ):
        items.append(
            {
                "object_type": "file",
                "object_id": file.id,
                "job_id": file.job_id,
                "document_type": file.document_type,
                "filename": file.filename,
                "lifecycle_state": file_lifecycle_state(file),
                "delete_origin": DELETE_ORIGIN_MANUAL_FILE,
                "deleted_at": file.deleted_at,
                "deleted_by": file.deleted_by,
                "delete_reason": file.delete_reason,
                "restored_at": file.restored_at,
                "archived_at": file.archived_at,
            }
        )
    for record in (
        db.query(NormalizedRecord)
        .filter(NormalizedRecord.lifecycle_state == LIFECYCLE_RECYCLE_BIN)
        .order_by(NormalizedRecord.deleted_at.desc(), NormalizedRecord.created_at.desc())
        .all()
    ):
        core = get_core(record.payload_json)
        items.append(
            {
                "object_type": "record",
                "object_id": record.id,
                "job_id": record.job_id,
                "file_id": record.file_id,
                "document_type": record.document_type,
                "source_row": int(record.source_row or 0),
                "customer_order_no": core.get("customer_order_no"),
                "entry_line_no": core.get("entry_line_no"),
                "biz_date": core.get("biz_date"),
                "item_code": core.get("item_code"),
                "filename": record.file.filename if record.file else None,
                "lifecycle_state": record_lifecycle_state(record),
                "version_status": record_version_status(record),
                "delete_origin": record.delete_origin,
                "deleted_at": record.deleted_at,
                "deleted_by": record.deleted_by,
                "delete_reason": record.delete_reason,
                "restored_at": record.restored_at,
                "archived_at": record.archived_at,
            }
        )
    items.sort(key=lambda item: str(item.get("deleted_at") or ""), reverse=True)
    return items


def build_archived_items(db: Session) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for file in (
        db.query(UploadedFile)
        .filter(UploadedFile.lifecycle_state == LIFECYCLE_ARCHIVED)
        .order_by(UploadedFile.archived_at.desc(), UploadedFile.created_at.desc())
        .all()
    ):
        items.append(
            {
                "object_type": "file",
                "object_id": file.id,
                "job_id": file.job_id,
                "document_type": file.document_type,
                "filename": file.filename,
                "lifecycle_state": file_lifecycle_state(file),
                "archived_at": file.archived_at,
                "archived_by": file.archived_by,
                "archive_reason": file.archive_reason,
            }
        )
    for record in (
        db.query(NormalizedRecord)
        .filter(
            NormalizedRecord.lifecycle_state == LIFECYCLE_ARCHIVED,
            NormalizedRecord.version_status != STATUS_SPECIAL_CASE,
        )
        .order_by(NormalizedRecord.archived_at.desc(), NormalizedRecord.created_at.desc())
        .all()
    ):
        core = get_core(record.payload_json)
        items.append(
            {
                "object_type": "record",
                "object_id": record.id,
                "job_id": record.job_id,
                "file_id": record.file_id,
                "document_type": record.document_type,
                "source_row": int(record.source_row or 0),
                "customer_order_no": core.get("customer_order_no"),
                "entry_line_no": core.get("entry_line_no"),
                "biz_date": core.get("biz_date"),
                "item_code": core.get("item_code"),
                "filename": record.file.filename if record.file else None,
                "lifecycle_state": record_lifecycle_state(record),
                "version_status": record_version_status(record),
                "delete_origin": record.delete_origin,
                "archived_at": record.archived_at,
                "archived_by": record.archived_by,
                "archive_reason": record.archive_reason,
            }
        )
    items.sort(key=lambda item: str(item.get("archived_at") or ""), reverse=True)
    return items


def build_special_case_items(db: Session) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    job_latest_archived: dict[str, str] = {}
    for file in (
        db.query(UploadedFile)
        .filter(UploadedFile.lifecycle_state == LIFECYCLE_SPECIAL_CASE)
        .order_by(UploadedFile.archived_at.desc(), UploadedFile.created_at.desc())
        .all()
    ):
        items.append(
            {
                "object_type": "file",
                "object_id": file.id,
                "job_id": file.job_id,
                "document_type": file.document_type,
                "filename": file.filename,
                "lifecycle_state": file_lifecycle_state(file),
                "special_case_source": special_case_source_code_for_file(db, file),
                "archived_at": file.archived_at,
                "archived_by": file.archived_by,
                "archive_reason": file.archive_reason,
            }
        )
        job_key = str(file.job_id or "")
        archived_key = str(file.archived_at or "")
        if archived_key > job_latest_archived.get(job_key, ""):
            job_latest_archived[job_key] = archived_key
    for record in (
        db.query(NormalizedRecord)
        .filter(
            NormalizedRecord.lifecycle_state == LIFECYCLE_SPECIAL_CASE,
            NormalizedRecord.version_status == STATUS_SPECIAL_CASE,
        )
        .order_by(NormalizedRecord.archived_at.desc(), NormalizedRecord.created_at.desc())
        .all()
    ):
        core = get_core(record.payload_json)
        items.append(
            {
                "object_type": "record",
                "object_id": record.id,
                "job_id": record.job_id,
                "file_id": record.file_id,
                "document_type": record.document_type,
                "source_row": int(record.source_row or 0),
                "customer_order_no": core.get("customer_order_no"),
                "entry_line_no": core.get("entry_line_no"),
                "biz_date": core.get("biz_date"),
                "item_code": core.get("item_code"),
                "filename": record.file.filename if record.file else None,
                "lifecycle_state": record_lifecycle_state(record),
                "version_status": record_version_status(record),
                "review_status": review_status_code(record),
                "effective_status": effective_status_code(record),
                "special_case_source": special_case_source_code_for_record(record),
                "special_case_reason": _special_case_reason(record),
                "special_case_note": _special_case_note(record),
                "archived_at": record.archived_at,
                "archived_by": record.archived_by,
            }
        )
        job_key = str(record.job_id or "")
        archived_key = str(record.archived_at or "")
        if archived_key > job_latest_archived.get(job_key, ""):
            job_latest_archived[job_key] = archived_key
    items.sort(
        key=lambda item: (
            job_latest_archived.get(str(item.get("job_id") or ""), ""),
            1 if item.get("object_type") == "file" else 0,
            str(item.get("archived_at") or ""),
        ),
        reverse=True,
    )
    return items
