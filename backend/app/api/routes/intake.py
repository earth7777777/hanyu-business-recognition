from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.db.models import TaskRun, UploadJob
from app.schemas.common import FileResponse, JobResponse
from app.schemas.task import TaskResponse
from app.services.config_service import ConfigService
from app.services.integration.auth import validate_external_client
from app.services.integration.hub import load_integration_hub, resolve_provider_id
from app.services.integration.intake_service import create_external_job, start_job_task, upload_file_for_job
from app.services.task_runner import process_task_compat

router = APIRouter(prefix="/intake", tags=["intake"])


def _auth_external(
    *,
    db: Session,
    client_id: str,
    client_token: str,
    requested_provider: str | None,
):
    cfg = ConfigService(db)
    hub = load_integration_hub(cfg)
    provider_id = resolve_provider_id(hub, requested_provider)
    ctx = validate_external_client(
        hub=hub,
        client_id=client_id,
        token=client_token,
        provider=provider_id,
    )
    if not ctx:
        raise HTTPException(status_code=401, detail="Invalid external client credentials or provider access.")
    return ctx


def _enqueue_auto_recompute(
    *,
    db: Session,
    background_tasks: BackgroundTasks,
    job_id: str,
    created_by: str,
    trigger_doc_type: str,
    trigger_file_id: str,
) -> TaskRun:
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
    return task


@router.post("/jobs")
def create_intake_job(
    body: dict,
    db: Session = Depends(db_dep),
    x_client_id: str | None = Header(default=None),
    x_client_token: str | None = Header(default=None),
):
    if not x_client_id or not x_client_token:
        raise HTTPException(status_code=401, detail="Missing X-Client-Id or X-Client-Token header.")

    provider_id = (body or {}).get("provider")
    request_id = str((body or {}).get("request_id") or "").strip()
    source_ref = (body or {}).get("source_ref")
    metadata = (body or {}).get("metadata")
    if not request_id:
        raise HTTPException(status_code=400, detail="request_id is required")
    if metadata is not None and not isinstance(metadata, dict):
        raise HTTPException(status_code=400, detail="metadata must be an object when provided")

    ctx = _auth_external(
        db=db,
        client_id=x_client_id,
        client_token=x_client_token,
        requested_provider=provider_id,
    )
    job, idempotent_hit = create_external_job(
        db,
        client_id=ctx.client_id,
        provider=ctx.provider,
        request_id=request_id,
        source_ref=str(source_ref) if source_ref is not None else None,
        metadata=metadata if isinstance(metadata, dict) else {},
    )
    return {
        "idempotent_hit": idempotent_hit,
        "provider": ctx.provider,
        "job": JobResponse(id=job.id, status=job.status, created_by=job.created_by, created_at=job.created_at),
    }


@router.post("/jobs/{job_id}/files", response_model=FileResponse)
def upload_intake_file(
    job_id: str,
    background_tasks: BackgroundTasks,
    document_type: str = Form(...),
    metadata_json: str | None = Form(default=None),
    upload: UploadFile = File(...),
    provider: str | None = Form(default=None),
    db: Session = Depends(db_dep),
    x_client_id: str | None = Header(default=None),
    x_client_token: str | None = Header(default=None),
):
    if not x_client_id or not x_client_token:
        raise HTTPException(status_code=401, detail="Missing X-Client-Id or X-Client-Token header.")

    ctx = _auth_external(
        db=db,
        client_id=x_client_id,
        client_token=x_client_token,
        requested_provider=provider,
    )
    if ctx.allow_doc_types and document_type.strip().lower() not in ctx.allow_doc_types:
        raise HTTPException(status_code=403, detail=f"document_type '{document_type}' is not allowed for this client.")

    f = upload_file_for_job(
        db,
        job_id=job_id,
        document_type=document_type,
        upload=upload,
        metadata_json=metadata_json,
    )
    if f.parse_status == "parsed" and f.parsed_count > 0 and f.document_type in {"order", "payment_notice", "invoice"}:
        _enqueue_auto_recompute(
            db=db,
            background_tasks=background_tasks,
            job_id=f.job_id,
            created_by=f"ext:{ctx.client_id}",
            trigger_doc_type=f.document_type,
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


@router.post("/jobs/{job_id}/run", response_model=TaskResponse)
def run_intake_job(
    job_id: str,
    body: dict,
    background_tasks: BackgroundTasks,
    db: Session = Depends(db_dep),
    x_client_id: str | None = Header(default=None),
    x_client_token: str | None = Header(default=None),
):
    if not x_client_id or not x_client_token:
        raise HTTPException(status_code=401, detail="Missing X-Client-Id or X-Client-Token header.")

    provider_id = (body or {}).get("provider")
    request_id = str((body or {}).get("request_id") or "").strip()
    source_ref = (body or {}).get("source_ref")
    if not request_id:
        raise HTTPException(status_code=400, detail="request_id is required")

    ctx = _auth_external(
        db=db,
        client_id=x_client_id,
        client_token=x_client_token,
        requested_provider=provider_id,
    )
    task = start_job_task(
        db,
        job_id=job_id,
        client_id=ctx.client_id,
        created_by=f"ext:{ctx.client_id}",
        provider_id=ctx.provider,
        request_id=request_id,
        source_ref=str(source_ref) if source_ref is not None else None,
    )
    background_tasks.add_task(process_task_compat, task.id)
    return TaskResponse(
        id=task.id,
        job_id=task.job_id,
        task_type=task.task_type,
        status=task.status,
        error=task.error,
        created_by=task.created_by,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


@router.get("/jobs/{job_id}/status")
def get_intake_status(
    job_id: str,
    provider: str | None = None,
    db: Session = Depends(db_dep),
    x_client_id: str | None = Header(default=None),
    x_client_token: str | None = Header(default=None),
):
    if not x_client_id or not x_client_token:
        raise HTTPException(status_code=401, detail="Missing X-Client-Id or X-Client-Token header.")
    _ = _auth_external(
        db=db,
        client_id=x_client_id,
        client_token=x_client_token,
        requested_provider=provider,
    )

    job = db.get(UploadJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Upload job not found")

    tasks = (
        db.query(TaskRun)
        .filter(TaskRun.job_id == job_id)
        .order_by(TaskRun.created_at.desc())
        .all()
    )
    return {
        "job_id": job.id,
        "job_status": job.status,
        "created_by": job.created_by,
        "tasks": [
            {
                "task_id": t.id,
                "task_type": t.task_type,
                "status": t.status,
                "error": t.error,
                "updated_at": t.updated_at,
            }
            for t in tasks
        ],
    }
