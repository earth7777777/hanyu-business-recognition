from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import db_dep, role_dep
from app.db.models import TaskRun, UploadJob
from app.schemas.task import TaskResponse, TaskResultResponse
from app.services.task_runner import process_task_compat

router = APIRouter(prefix="/tasks", tags=["tasks"])



def _start_task(
    *,
    task_type: str,
    body: dict,
    background_tasks: BackgroundTasks,
    db: Session,
    role: str,
) -> TaskResponse:
    job_id = (body or {}).get("job_id")
    if not job_id:
        raise HTTPException(status_code=400, detail="job_id is required")

    job = db.get(UploadJob, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Upload job not found")

    task = TaskRun(job_id=job_id, task_type=task_type, status="queued", input_json=body, created_by=role)
    db.add(task)
    db.commit()
    db.refresh(task)

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


@router.post("/orchestrate", response_model=TaskResponse)
def start_orchestration(
    body: dict,
    background_tasks: BackgroundTasks,
    db: Session = Depends(db_dep),
    role: str = Depends(role_dep),
):
    # Internal processing trigger. External tools should use /v1/intake.
    return _start_task(
        task_type="orchestrate",
        body=body,
        background_tasks=background_tasks,
        db=db,
        role=role,
    )


@router.post("/lobster-feed", response_model=TaskResponse)
def start_lobster_feed(
    body: dict,
    background_tasks: BackgroundTasks,
    db: Session = Depends(db_dep),
    role: str = Depends(role_dep),
):
    # Backward compatibility alias for one version cycle.
    return _start_task(
        task_type="lobster_feed",
        body=body,
        background_tasks=background_tasks,
        db=db,
        role=role,
    )


@router.get("", response_model=list[TaskResponse])
def list_tasks(job_id: str | None = None, db: Session = Depends(db_dep), role: str = Depends(role_dep)):
    _ = role
    q = db.query(TaskRun)
    if job_id:
        q = q.filter(TaskRun.job_id == job_id)
    rows = q.order_by(TaskRun.created_at.desc()).all()
    return [
        TaskResponse(
            id=t.id,
            job_id=t.job_id,
            task_type=t.task_type,
            status=t.status,
            error=t.error,
            created_by=t.created_by,
            created_at=t.created_at,
            updated_at=t.updated_at,
        )
        for t in rows
    ]


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(task_id: str, db: Session = Depends(db_dep), role: str = Depends(role_dep)):
    _ = role
    t = db.get(TaskRun, task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskResponse(
        id=t.id,
        job_id=t.job_id,
        task_type=t.task_type,
        status=t.status,
        error=t.error,
        created_by=t.created_by,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


@router.get("/{task_id}/result", response_model=TaskResultResponse)
def get_task_result(task_id: str, db: Session = Depends(db_dep), role: str = Depends(role_dep)):
    _ = role
    t = db.get(TaskRun, task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return TaskResultResponse(task_id=t.id, status=t.status, output=t.output_json or {})
