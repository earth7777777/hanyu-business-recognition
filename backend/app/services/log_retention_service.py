from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.core.settings import LOG_ROOT_DIR, settings
from app.db.models import ConfigEntry
from app.db.session import SessionLocal
from app.services.config_service import ConfigService


_LOCAL_TZ = ZoneInfo("Asia/Shanghai")
_DEFAULT_SCHEDULE_TIME = "03:00"
_DEFAULT_RETENTION_DAYS = 30
_RUNTIME_KEY = "log_cleanup"
_LOG_CLEANUP_LOCK = threading.Lock()
_SCHEDULER_STOP = threading.Event()
_SCHEDULER_THREAD: threading.Thread | None = None
_LOGGER_NAME = "hanyu.ops"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_schedule_time(value: Any) -> str:
    raw = str(value or "").strip()
    if len(raw) != 5 or raw[2] != ":":
        return _DEFAULT_SCHEDULE_TIME
    try:
        hour = int(raw[:2])
        minute = int(raw[3:])
    except ValueError:
        return _DEFAULT_SCHEDULE_TIME
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return _DEFAULT_SCHEDULE_TIME
    return f"{hour:02d}:{minute:02d}"


def _safe_retention_days(value: Any) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_RETENTION_DAYS
    return max(days, 1)


def _get_policy() -> dict[str, Any]:
    with SessionLocal() as db:
        return ConfigService(db).get("operations_monitoring_policy")


def _get_runtime() -> dict[str, Any]:
    with SessionLocal() as db:
        return ConfigService(db).get("operations_runtime_status")


def _merge_runtime_block(updates: dict[str, Any]) -> dict[str, Any]:
    with SessionLocal() as db:
        item = db.get(ConfigEntry, "operations_runtime_status")
        if not item:
            item = ConfigEntry(key="operations_runtime_status", value_json={})
            db.add(item)
            db.flush()
        runtime_status = dict(item.value_json or {})
        block = dict(runtime_status.get(_RUNTIME_KEY) or {})
        block.update(updates)
        runtime_status[_RUNTIME_KEY] = block
        item.value_json = runtime_status
        db.commit()
        db.refresh(item)
        return dict((item.value_json or {}).get(_RUNTIME_KEY) or {})


def _log_target_dir() -> Path:
    return LOG_ROOT_DIR


def _is_enabled(policy: dict[str, Any]) -> bool:
    return bool(policy.get("log_cleanup_enabled", True))


def _scan_log_dir(target_dir: Path) -> dict[str, Any]:
    if not target_dir.exists():
        return {
            "current_file_count": 0,
            "current_total_size_bytes": 0,
            "latest_file_at": None,
        }
    current_file_count = 0
    current_total_size_bytes = 0
    latest_file_at: datetime | None = None
    for path in target_dir.iterdir():
        if not path.is_file():
            continue
        current_file_count += 1
        stat = path.stat()
        current_total_size_bytes += int(stat.st_size or 0)
        modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        if latest_file_at is None or modified_at > latest_file_at:
            latest_file_at = modified_at
    return {
        "current_file_count": current_file_count,
        "current_total_size_bytes": current_total_size_bytes,
        "latest_file_at": latest_file_at.isoformat() if latest_file_at else None,
    }


def _purge_old_logs(target_dir: Path, retention_days: int) -> tuple[int, int]:
    if retention_days <= 0 or not target_dir.exists():
        return (0, 0)
    cutoff = _utcnow() - timedelta(days=retention_days)
    removed_count = 0
    removed_total_size_bytes = 0
    for path in target_dir.iterdir():
        if not path.is_file():
            continue
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if modified_at < cutoff:
            removed_total_size_bytes += int(path.stat().st_size or 0)
            path.unlink(missing_ok=True)
            removed_count += 1
    return (removed_count, removed_total_size_bytes)


def configure_operations_file_logging() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if any(getattr(handler, "_hanyu_ops_file_handler", False) for handler in logger.handlers):
        return logger
    target_dir = _log_target_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    handler = TimedRotatingFileHandler(
        filename=str(target_dir / "application.log"),
        when="midnight",
        interval=1,
        backupCount=0,
        encoding="utf-8",
    )
    handler.suffix = "%Y-%m-%d"
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    handler._hanyu_ops_file_handler = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    return logger


def get_operations_logger() -> logging.Logger:
    return configure_operations_file_logging()


def summarize_log_cleanup_state(
    *,
    monitoring_policy: dict[str, Any],
    runtime_status: dict[str, Any],
) -> dict[str, Any]:
    log_cleanup = dict(runtime_status.get(_RUNTIME_KEY) or {})
    target_dir = _log_target_dir()
    stats = _scan_log_dir(target_dir)
    schedule_time = _safe_schedule_time(monitoring_policy.get("log_cleanup_schedule_time"))
    retention_days = _safe_retention_days(monitoring_policy.get("log_retention_days"))
    return {
        "enabled": bool(monitoring_policy.get("log_cleanup_enabled", True)),
        "target_path": str(target_dir),
        "schedule_time": schedule_time,
        "retention_days": retention_days,
        "last_success_at": log_cleanup.get("last_success_at"),
        "last_status": str(log_cleanup.get("last_status") or "unknown"),
        "last_error": str(log_cleanup.get("last_error") or ""),
        "last_started_at": log_cleanup.get("last_started_at"),
        "last_finished_at": log_cleanup.get("last_finished_at"),
        "last_removed_file_count": int(log_cleanup.get("last_removed_file_count") or 0),
        "last_removed_total_size_bytes": int(log_cleanup.get("last_removed_total_size_bytes") or 0),
        "last_remaining_file_count": int(log_cleanup.get("last_remaining_file_count") or 0),
        "last_remaining_total_size_bytes": int(log_cleanup.get("last_remaining_total_size_bytes") or 0),
        "last_trigger": str(log_cleanup.get("last_trigger") or "").strip().lower() or "none",
        **stats,
    }


def run_log_cleanup_now(*, trigger: str = "manual") -> dict[str, Any]:
    if not _LOG_CLEANUP_LOCK.acquire(blocking=False):
        raise RuntimeError("日志清理正在执行，请稍后再试。")
    logger = get_operations_logger()
    try:
        policy = _get_policy()
        target_dir = _log_target_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        retention_days = _safe_retention_days(policy.get("log_retention_days"))
        started_at = _utcnow()
        _merge_runtime_block(
            {
                "last_status": "running",
                "last_error": "",
                "last_started_at": started_at.isoformat(),
                "last_finished_at": None,
                "last_trigger": str(trigger or "").strip().lower() or "manual",
            }
        )
        try:
            removed_count, removed_total_size_bytes = _purge_old_logs(target_dir, retention_days)
            current_stats = _scan_log_dir(target_dir)
            finished_at = _utcnow()
            _merge_runtime_block(
                {
                    "last_success_at": finished_at.isoformat(),
                    "last_status": "succeeded",
                    "last_error": "",
                    "last_finished_at": finished_at.isoformat(),
                    "last_removed_file_count": removed_count,
                    "last_removed_total_size_bytes": removed_total_size_bytes,
                    "last_remaining_file_count": int(current_stats.get("current_file_count") or 0),
                    "last_remaining_total_size_bytes": int(current_stats.get("current_total_size_bytes") or 0),
                    "last_trigger": str(trigger or "").strip().lower() or "manual",
                }
            )
            logger.info(
                "日志清理完成：trigger=%s removed_count=%s remaining_count=%s retention_days=%s",
                str(trigger or "").strip().lower() or "manual",
                removed_count,
                int(current_stats.get("current_file_count") or 0),
                retention_days,
            )
            return {
                "status": "succeeded",
                "trigger": str(trigger or "").strip().lower() or "manual",
                "target_dir": str(target_dir),
                "retention_days": retention_days,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "removed_file_count": removed_count,
                "removed_total_size_bytes": removed_total_size_bytes,
                "remaining_file_count": int(current_stats.get("current_file_count") or 0),
                "remaining_total_size_bytes": int(current_stats.get("current_total_size_bytes") or 0),
            }
        except Exception as exc:
            finished_at = _utcnow()
            _merge_runtime_block(
                {
                    "last_status": "failed",
                    "last_error": str(exc),
                    "last_finished_at": finished_at.isoformat(),
                    "last_trigger": str(trigger or "").strip().lower() or "manual",
                }
            )
            logger.exception("日志清理失败：trigger=%s", str(trigger or "").strip().lower() or "manual")
            raise
    finally:
        _LOG_CLEANUP_LOCK.release()


def _scheduled_due(*, now_utc: datetime, policy: dict[str, Any], runtime: dict[str, Any]) -> bool:
    if not _is_enabled(policy):
        return False
    local_now = now_utc.astimezone(_LOCAL_TZ)
    schedule_time = _safe_schedule_time(policy.get("log_cleanup_schedule_time"))
    hour = int(schedule_time[:2])
    minute = int(schedule_time[3:])
    scheduled_local = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if local_now < scheduled_local:
        return False
    block = dict(runtime.get(_RUNTIME_KEY) or {})
    raw_last_finished = block.get("last_finished_at") or block.get("last_success_at") or block.get("last_started_at")
    if not raw_last_finished:
        return True
    try:
        last_finished = datetime.fromisoformat(str(raw_last_finished).replace("Z", "+00:00"))
    except ValueError:
        return True
    if last_finished.tzinfo is None:
        last_finished = last_finished.replace(tzinfo=timezone.utc)
    return last_finished.astimezone(_LOCAL_TZ) < scheduled_local


def _scheduler_loop() -> None:
    logger = get_operations_logger()
    while not _SCHEDULER_STOP.wait(60):
        try:
            policy = _get_policy()
            runtime = _get_runtime()
            now_utc = _utcnow()
            if _scheduled_due(now_utc=now_utc, policy=policy, runtime=runtime):
                try:
                    run_log_cleanup_now(trigger="auto")
                except Exception:
                    logger.exception("自动日志清理失败")
        except Exception:
            logger.exception("日志清理调度检查失败")


def start_log_cleanup_scheduler() -> None:
    global _SCHEDULER_THREAD
    if settings.database_url.startswith("sqlite"):
        return
    if _SCHEDULER_THREAD and _SCHEDULER_THREAD.is_alive():
        return
    _SCHEDULER_STOP.clear()
    _SCHEDULER_THREAD = threading.Thread(target=_scheduler_loop, name="log-cleanup-scheduler", daemon=True)
    _SCHEDULER_THREAD.start()


def stop_log_cleanup_scheduler() -> None:
    _SCHEDULER_STOP.set()
