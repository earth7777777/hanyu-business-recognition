from __future__ import annotations

import gzip
import os
import shutil
import subprocess
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.engine import make_url

from app.core.settings import BACKUP_ROOT_DIR, STORAGE_DIR, settings
from app.db.models import ConfigEntry
from app.db.session import SessionLocal
from app.services.config_service import ConfigService


_LOCAL_TZ = ZoneInfo("Asia/Shanghai")
_BACKUP_KIND_DB = "db_backup"
_BACKUP_KIND_FILES = "file_backup"
_DEFAULT_SCHEDULE_TIME = "02:00"
_DEFAULT_RETENTION_DAYS = 30
_SCHEDULER_STOP = threading.Event()
_SCHEDULER_THREAD: threading.Thread | None = None
_BACKUP_LOCKS = {
    _BACKUP_KIND_DB: threading.Lock(),
    _BACKUP_KIND_FILES: threading.Lock(),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _default_db_backup_dir() -> str:
    return str(BACKUP_ROOT_DIR / "db")


def _default_file_backup_dir() -> str:
    return str(BACKUP_ROOT_DIR / "uploads")


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


def _merge_runtime_block(kind: str, updates: dict[str, Any]) -> dict[str, Any]:
    with SessionLocal() as db:
        item = db.get(ConfigEntry, "operations_runtime_status")
        if not item:
            item = ConfigEntry(key="operations_runtime_status", value_json={})
            db.add(item)
            db.flush()
        runtime_status = dict(item.value_json or {})
        block = dict(runtime_status.get(kind) or {})
        block.update(updates)
        runtime_status[kind] = block
        item.value_json = runtime_status
        db.commit()
        db.refresh(item)
        return dict((item.value_json or {}).get(kind) or {})


def _target_dir_for(kind: str, policy: dict[str, Any]) -> Path:
    if kind == _BACKUP_KIND_DB:
        raw = str(policy.get("db_backup_target_path") or "").strip() or _default_db_backup_dir()
    else:
        raw = str(policy.get("file_backup_target_path") or "").strip() or _default_file_backup_dir()
    return Path(raw).expanduser()


def _is_enabled(kind: str, policy: dict[str, Any]) -> bool:
    if kind == _BACKUP_KIND_DB:
        return bool(policy.get("db_backup_enabled"))
    return bool(policy.get("file_backup_enabled"))


def _snapshot_label(kind: str, now: datetime) -> str:
    stamp = now.astimezone(_LOCAL_TZ).strftime("%Y%m%d-%H%M%S")
    if kind == _BACKUP_KIND_DB:
        if settings.database_url.startswith("sqlite"):
            return f"db-backup-{stamp}.sqlite.gz"
        return f"db-backup-{stamp}.sql.gz"
    return f"uploads-backup-{stamp}.zip"


def _purge_old_backups(target_dir: Path, retention_days: int) -> int:
    if retention_days <= 0 or not target_dir.exists():
        return 0
    cutoff = _utcnow() - timedelta(days=retention_days)
    removed = 0
    for path in target_dir.iterdir():
        if not path.is_file():
            continue
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if modified_at < cutoff:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def _sqlite_path() -> Path:
    url = make_url(settings.database_url)
    database = str(url.database or "").strip()
    if not database:
        raise RuntimeError("sqlite database path is empty")
    return Path(database)


def _pick_dump_command() -> str:
    for name in ("mariadb-dump", "mysqldump"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    raise RuntimeError("未找到 mariadb-dump 或 mysqldump，无法执行数据库备份。")


def _run_database_backup(target_path: Path) -> None:
    if settings.database_url.startswith("sqlite"):
        source = _sqlite_path()
        if not source.exists():
            raise RuntimeError(f"sqlite 数据库文件不存在：{source}")
        with source.open("rb") as src, gzip.open(target_path, "wb") as dst:
            shutil.copyfileobj(src, dst)
        return

    url = make_url(settings.database_url)
    command = [
        _pick_dump_command(),
        "--single-transaction",
        "--skip-lock-tables",
        "--default-character-set=utf8mb4",
    ]
    if url.host:
        command.extend(["-h", str(url.host)])
    if url.port:
        command.extend(["-P", str(url.port)])
    if url.username:
        command.extend(["-u", str(url.username)])
    if url.database:
        command.append(str(url.database))
    env = os.environ.copy()
    if url.password:
        env["MYSQL_PWD"] = str(url.password)
    dump = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    with gzip.open(target_path, "wb") as output:
        output.write(dump.stdout)


def _run_file_backup(target_path: Path) -> None:
    with zipfile.ZipFile(target_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in STORAGE_DIR.rglob("*"):
            if not path.is_file():
                continue
            archive.write(path, path.relative_to(STORAGE_DIR))


def _execute_backup(kind: str) -> dict[str, Any]:
    policy = _get_policy()
    if not _is_enabled(kind, policy):
        raise RuntimeError("当前备份开关未启用。")

    target_dir = _target_dir_for(kind, policy)
    target_dir.mkdir(parents=True, exist_ok=True)
    retention_days = _safe_retention_days(policy.get("backup_retention_days"))

    started_at = _utcnow()
    snapshot_label = _snapshot_label(kind, started_at)
    target_path = target_dir / snapshot_label

    _merge_runtime_block(
        kind,
        {
            "last_status": "running",
            "last_error": "",
            "last_started_at": started_at.isoformat(),
            "last_finished_at": None,
            "last_snapshot_label": snapshot_label,
        },
    )

    try:
        if kind == _BACKUP_KIND_DB:
            _run_database_backup(target_path)
        else:
            _run_file_backup(target_path)
        removed_count = _purge_old_backups(target_dir, retention_days)
        finished_at = _utcnow()
        _merge_runtime_block(
            kind,
            {
                "last_status": "succeeded",
                "last_error": "",
                "last_success_at": finished_at.isoformat(),
                "last_finished_at": finished_at.isoformat(),
                "last_snapshot_label": snapshot_label,
            },
        )
        return {
            "backup_kind": kind,
            "status": "succeeded",
            "target_dir": str(target_dir),
            "snapshot_label": snapshot_label,
            "output_path": str(target_path),
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "removed_old_backup_count": removed_count,
        }
    except Exception as exc:
        finished_at = _utcnow()
        _merge_runtime_block(
            kind,
            {
                "last_status": "failed",
                "last_error": str(exc),
                "last_finished_at": finished_at.isoformat(),
                "last_snapshot_label": snapshot_label,
            },
        )
        if target_path.exists():
            target_path.unlink(missing_ok=True)
        raise


def run_database_backup_now() -> dict[str, Any]:
    lock = _BACKUP_LOCKS[_BACKUP_KIND_DB]
    if not lock.acquire(blocking=False):
        raise RuntimeError("数据库备份正在执行，请稍后再试。")
    try:
        return _execute_backup(_BACKUP_KIND_DB)
    finally:
        lock.release()


def run_file_backup_now() -> dict[str, Any]:
    lock = _BACKUP_LOCKS[_BACKUP_KIND_FILES]
    if not lock.acquire(blocking=False):
        raise RuntimeError("上传文件备份正在执行，请稍后再试。")
    try:
        return _execute_backup(_BACKUP_KIND_FILES)
    finally:
        lock.release()


def _scheduled_due(kind: str, *, now_utc: datetime, policy: dict[str, Any], runtime: dict[str, Any]) -> bool:
    if not _is_enabled(kind, policy):
        return False
    local_now = now_utc.astimezone(_LOCAL_TZ)
    schedule_time = _safe_schedule_time(policy.get("backup_schedule_time"))
    hour = int(schedule_time[:2])
    minute = int(schedule_time[3:])
    scheduled_local = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if local_now < scheduled_local:
        return False
    block = dict(runtime.get(kind) or {})
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
    while not _SCHEDULER_STOP.wait(60):
        try:
            policy = _get_policy()
            runtime = _get_runtime()
            now_utc = _utcnow()
            if _scheduled_due(_BACKUP_KIND_DB, now_utc=now_utc, policy=policy, runtime=runtime):
                try:
                    run_database_backup_now()
                except Exception:
                    pass
            if _scheduled_due(_BACKUP_KIND_FILES, now_utc=now_utc, policy=policy, runtime=runtime):
                try:
                    run_file_backup_now()
                except Exception:
                    pass
        except Exception:
            pass


def start_backup_scheduler() -> None:
    global _SCHEDULER_THREAD
    if settings.database_url.startswith("sqlite"):
        return
    if _SCHEDULER_THREAD and _SCHEDULER_THREAD.is_alive():
        return
    _SCHEDULER_STOP.clear()
    _SCHEDULER_THREAD = threading.Thread(target=_scheduler_loop, name="backup-scheduler", daemon=True)
    _SCHEDULER_THREAD.start()


def stop_backup_scheduler() -> None:
    _SCHEDULER_STOP.set()
