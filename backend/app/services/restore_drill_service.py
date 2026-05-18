from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.core.settings import settings
from app.db.models import ConfigEntry
from app.db.session import SessionLocal
from app.services.config_service import ConfigService
from app.services.log_retention_service import get_operations_logger


_RUNTIME_KEY = "restore_drill"
_DB_PREFIX = "db-backup-"
_UPLOADS_PREFIX = "uploads-backup-"
_COUNT_TABLES = (
    ("upload_jobs", "restored_job_count"),
    ("uploaded_files", "restored_uploaded_file_row_count"),
    ("normalized_records", "restored_record_count"),
    ("task_runs", "restored_task_run_count"),
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_policy() -> dict[str, Any]:
    with SessionLocal() as db:
        return ConfigService(db).get("operations_monitoring_policy")


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


def _latest_backup_file(target_dir: Path, prefix: str) -> Path | None:
    if not target_dir.exists():
        return None
    items = [path for path in target_dir.iterdir() if path.is_file() and path.name.startswith(prefix)]
    if not items:
        return None
    return max(items, key=lambda path: (path.stat().st_mtime, path.name))


def _restore_target_dirs(policy: dict[str, Any]) -> tuple[Path, Path]:
    db_dir = Path(str(policy.get("db_backup_target_path") or "")).expanduser()
    uploads_dir = Path(str(policy.get("file_backup_target_path") or "")).expanduser()
    return (db_dir, uploads_dir)


def _normalize_database_url(value: Any) -> str:
    return str(value or "").strip()


def _resolve_restore_drill_database_url(policy: dict[str, Any]) -> tuple[str | None, str]:
    if settings.database_url.startswith("sqlite"):
        return (settings.database_url, "primary_sqlite")
    env_url = _normalize_database_url(os.getenv("RESTORE_DRILL_DATABASE_URL"))
    if env_url:
        return (env_url, "env")
    policy_url = _normalize_database_url(policy.get("restore_drill_database_url"))
    if policy_url:
        return (policy_url, "policy")
    return (None, "missing")


def _ensure_restore_drill_database_url(policy: dict[str, Any]) -> tuple[str, str]:
    restore_url, source = _resolve_restore_drill_database_url(policy)
    if not restore_url:
        raise RuntimeError(
            "Restore drill requires a dedicated restore-drill database URL. Configure restore_drill_database_url first."
        )
    if not settings.database_url.startswith("sqlite") and restore_url == settings.database_url:
        raise RuntimeError("Restore drill database URL must not reuse the primary DATABASE_URL.")
    return (restore_url, source)


def summarize_restore_drill_state(*, runtime_status: dict[str, Any], monitoring_policy: dict[str, Any]) -> dict[str, Any]:
    db_dir, uploads_dir = _restore_target_dirs(monitoring_policy)
    latest_db = _latest_backup_file(db_dir, _DB_PREFIX)
    latest_uploads = _latest_backup_file(uploads_dir, _UPLOADS_PREFIX)
    block = dict(runtime_status.get(_RUNTIME_KEY) or {})
    restore_url, connection_source = _resolve_restore_drill_database_url(monitoring_policy)
    return {
        "last_success_at": block.get("last_success_at"),
        "last_status": str(block.get("last_status") or "unknown"),
        "last_error": str(block.get("last_error") or ""),
        "last_started_at": block.get("last_started_at"),
        "last_finished_at": block.get("last_finished_at"),
        "last_db_snapshot_label": str(block.get("last_db_snapshot_label") or ""),
        "last_file_snapshot_label": str(block.get("last_file_snapshot_label") or ""),
        "last_restored_job_count": int(block.get("last_restored_job_count") or 0),
        "last_restored_uploaded_file_row_count": int(block.get("last_restored_uploaded_file_row_count") or 0),
        "last_restored_record_count": int(block.get("last_restored_record_count") or 0),
        "last_restored_task_run_count": int(block.get("last_restored_task_run_count") or 0),
        "last_restored_storage_file_count": int(block.get("last_restored_storage_file_count") or 0),
        "last_trigger": str(block.get("last_trigger") or "").strip().lower() or "none",
        "connection_ready": bool(restore_url),
        "connection_source": connection_source,
        "available_db_snapshot_label": latest_db.name if latest_db else "",
        "available_db_snapshot_time": datetime.fromtimestamp(latest_db.stat().st_mtime, tz=timezone.utc).isoformat()
        if latest_db
        else None,
        "available_file_snapshot_label": latest_uploads.name if latest_uploads else "",
        "available_file_snapshot_time": datetime.fromtimestamp(latest_uploads.stat().st_mtime, tz=timezone.utc).isoformat()
        if latest_uploads
        else None,
    }


def _count_sqlite_rows(sqlite_path: Path) -> dict[str, int]:
    counts = {key: 0 for _, key in _COUNT_TABLES}
    connection = sqlite3.connect(str(sqlite_path))
    try:
        cursor = connection.cursor()
        existing_tables = {
            row[0]
            for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if row and row[0]
        }
        for table_name, count_key in _COUNT_TABLES:
            if table_name not in existing_tables:
                continue
            counts[count_key] = int(cursor.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0] or 0)
    finally:
        connection.close()
    return counts


def _pick_restore_client() -> str:
    for name in ("mariadb", "mysql"):
        resolved = shutil.which(name)
        if resolved:
            return resolved
    raise RuntimeError("未找到 mariadb 或 mysql 客户端，无法执行数据库恢复演练。")


def _build_mysql_command(url, database: str | None = None) -> tuple[list[str], dict[str, str]]:
    command = [_pick_restore_client()]
    if url.host:
        command.extend(["-h", str(url.host)])
    if url.port:
        command.extend(["-P", str(url.port)])
    if url.username:
        command.extend(["-u", str(url.username)])
    if database:
        command.append(database)
    env = dict(os.environ)
    if url.password:
        env["MYSQL_PWD"] = str(url.password)
    return command, env


def _count_sqlalchemy_rows(database_url: str | Any) -> dict[str, int]:
    counts = {key: 0 for _, key in _COUNT_TABLES}
    engine = create_engine(database_url, future=True)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        with engine.connect() as conn:
            for table_name, count_key in _COUNT_TABLES:
                if table_name not in tables:
                    continue
                counts[count_key] = int(conn.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar() or 0)
    finally:
        engine.dispose()
    return counts


def _open_sql_dump_bytes(backup_path: Path) -> bytes:
    try:
        with gzip.open(backup_path, "rb") as src:
            return src.read()
    except OSError:
        return backup_path.read_bytes()


def _restore_database_backup(backup_path: Path, workspace_dir: Path, *, policy: dict[str, Any]) -> dict[str, int]:
    if backup_path.name.endswith(".sqlite.gz"):
        restored_path = workspace_dir / "restored.sqlite"
        with gzip.open(backup_path, "rb") as src, restored_path.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        return _count_sqlite_rows(restored_path)

    restore_database_url, _ = _ensure_restore_drill_database_url(policy)
    url = make_url(restore_database_url)
    temp_db_name = f"hanyu_restore_drill_{uuid4().hex[:12]}"
    command, env = _build_mysql_command(url)
    create_sql = f"CREATE DATABASE `{temp_db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;"
    drop_sql = f"DROP DATABASE IF EXISTS `{temp_db_name}`;"
    try:
        create_process = subprocess.run(command + ["-e", create_sql], check=False, stderr=subprocess.PIPE, env=env)
        if create_process.returncode != 0:
            raise RuntimeError(
                "Restore drill create database failed: "
                + (create_process.stderr.decode("utf-8", errors="ignore").strip() or "unknown error")
            )
        import_command, import_env = _build_mysql_command(url, temp_db_name)
        process = subprocess.Popen(import_command, stdin=subprocess.PIPE, stderr=subprocess.PIPE, env=import_env)
        assert process.stdin is not None
        process.stdin.write(_open_sql_dump_bytes(backup_path))
        process.stdin.close()
        stderr = process.stderr.read() if process.stderr else b""
        return_code = process.wait()
        if return_code != 0:
            raise RuntimeError(stderr.decode("utf-8", errors="ignore") or "恢复 SQL dump 失败。")

        restored_url = url.set(database=temp_db_name)
        return _count_sqlalchemy_rows(restored_url)
    finally:
        try:
            subprocess.run(command + ["-e", drop_sql], check=True, stderr=subprocess.PIPE, env=env)
        except Exception:
            pass


def _restore_upload_backup(backup_path: Path, workspace_dir: Path) -> int:
    extract_dir = workspace_dir / "uploads"
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(backup_path, "r") as archive:
        archive.extractall(extract_dir)
    return sum(1 for path in extract_dir.rglob("*") if path.is_file())


def run_restore_drill_now(*, trigger: str = "manual") -> dict[str, Any]:
    logger = get_operations_logger()
    policy = _get_policy()
    db_dir, uploads_dir = _restore_target_dirs(policy)
    latest_db = _latest_backup_file(db_dir, _DB_PREFIX)
    latest_uploads = _latest_backup_file(uploads_dir, _UPLOADS_PREFIX)
    if latest_db is None:
        raise RuntimeError("未找到数据库备份文件，无法开始恢复演练。")
    if latest_uploads is None:
        raise RuntimeError("未找到上传文件备份包，无法开始恢复演练。")

    workspace_dir = Path(tempfile.mkdtemp(prefix="hanyu-restore-drill-"))
    started_at = _utcnow()
    _merge_runtime_block(
        {
            "last_status": "running",
            "last_error": "",
            "last_started_at": started_at.isoformat(),
            "last_finished_at": None,
            "last_db_snapshot_label": latest_db.name,
            "last_file_snapshot_label": latest_uploads.name,
            "last_trigger": str(trigger or "").strip().lower() or "manual",
        }
    )

    try:
        db_counts = _restore_database_backup(latest_db, workspace_dir, policy=policy)
        restored_storage_file_count = _restore_upload_backup(latest_uploads, workspace_dir)
        finished_at = _utcnow()
        _merge_runtime_block(
            {
                "last_success_at": finished_at.isoformat(),
                "last_status": "succeeded",
                "last_error": "",
                "last_finished_at": finished_at.isoformat(),
                "last_restored_job_count": int(db_counts.get("restored_job_count") or 0),
                "last_restored_uploaded_file_row_count": int(db_counts.get("restored_uploaded_file_row_count") or 0),
                "last_restored_record_count": int(db_counts.get("restored_record_count") or 0),
                "last_restored_task_run_count": int(db_counts.get("restored_task_run_count") or 0),
                "last_restored_storage_file_count": int(restored_storage_file_count or 0),
                "last_trigger": str(trigger or "").strip().lower() or "manual",
            }
        )
        logger.info(
            "恢复演练成功：trigger=%s db_backup=%s file_backup=%s jobs=%s uploaded_file_rows=%s records=%s storage_files=%s",
            str(trigger or "").strip().lower() or "manual",
            latest_db.name,
            latest_uploads.name,
            int(db_counts.get("restored_job_count") or 0),
            int(db_counts.get("restored_uploaded_file_row_count") or 0),
            int(db_counts.get("restored_record_count") or 0),
            int(restored_storage_file_count or 0),
        )
        return {
            "status": "succeeded",
            "trigger": str(trigger or "").strip().lower() or "manual",
            "db_snapshot_label": latest_db.name,
            "file_snapshot_label": latest_uploads.name,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "restored_job_count": int(db_counts.get("restored_job_count") or 0),
            "restored_uploaded_file_row_count": int(db_counts.get("restored_uploaded_file_row_count") or 0),
            "restored_record_count": int(db_counts.get("restored_record_count") or 0),
            "restored_task_run_count": int(db_counts.get("restored_task_run_count") or 0),
            "restored_storage_file_count": int(restored_storage_file_count or 0),
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
        logger.exception("恢复演练失败：trigger=%s", str(trigger or "").strip().lower() or "manual")
        raise
    finally:
        shutil.rmtree(workspace_dir, ignore_errors=True)
