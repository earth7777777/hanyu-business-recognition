from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Alert, MatchGroup, TaskRun, UploadJob
from app.services.order_governance import current_effective_only_in_job


def build_result_envelope(
    db: Session,
    *,
    job_id: str,
    task_id: str | None = None,
    base_url: str = "",
) -> dict[str, Any]:
    job = db.get(UploadJob, job_id)
    if not job:
        raise KeyError(f"Upload job not found: {job_id}")

    task: TaskRun | None = None
    if task_id:
        task = db.get(TaskRun, task_id)
    if not task:
        task = (
            db.query(TaskRun)
            .filter(TaskRun.job_id == job_id)
            .order_by(TaskRun.updated_at.desc())
            .first()
        )

    records = current_effective_only_in_job(db, job_id=job_id)
    alerts = db.query(Alert).filter(Alert.job_id == job_id).all()
    open_alerts = [a for a in alerts if a.status == "open"]
    group_count = db.query(MatchGroup).filter(MatchGroup.job_id == job_id).count()

    by_doc = Counter(r.document_type for r in records)
    by_severity = Counter(a.severity for a in open_alerts)

    task_status = task.status if task else "queued"
    orchestration = {}
    if task and isinstance(task.output_json, dict):
        orchestration = task.output_json.get("orchestration") or {}

    base = base_url.rstrip("/")
    export_links = {
        "alerts_csv": f"{base}/v1/results/jobs/{job_id}/export?kind=alerts",
        "customer_summary_csv": f"{base}/v1/results/jobs/{job_id}/export?kind=customer-summary",
    }

    return {
        "job_id": job_id,
        "task_id": task.id if task else None,
        "status": task_status,
        "records_summary": {
            "total": len(records),
            "by_document_type": dict(by_doc),
        },
        "alerts_summary": {
            "total": len(open_alerts),
            "by_severity": dict(by_severity),
            "group_count": group_count,
        },
        "export_links": export_links,
        "trace": {
            "created_by": job.created_by,
            "job_status": job.status,
        },
        "provider_raw": orchestration.get("raw_response") if isinstance(orchestration, dict) else {},
    }
