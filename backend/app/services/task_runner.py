from __future__ import annotations

from datetime import datetime, timezone

from app.db.models import TaskRun
from app.db.session import SessionLocal
from app.services.orchestration.runner import process_orchestration_task



def _utcnow() -> datetime:
    return datetime.now(timezone.utc)



def process_lobster_task(task_id: str) -> None:
    """
    Backward-compat alias: previous entrypoint kept for one version cycle.
    """
    process_task_compat(task_id)



def process_task_compat(task_id: str) -> None:
    db = SessionLocal()
    try:
        process_orchestration_task(task_id, db)
    except Exception as exc:  # pragma: no cover - safety path
        task = db.get(TaskRun, task_id)
        if task:
            task.status = "failed"
            task.error = str(exc)
            task.updated_at = _utcnow()
            db.commit()
    finally:
        db.close()
