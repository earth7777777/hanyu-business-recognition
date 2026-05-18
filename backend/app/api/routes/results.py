from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.db.models import Alert
from app.services.config_service import ConfigService
from app.services.export_service import export_alerts_csv, export_customer_summary_csv
from app.services.integration.auth import validate_external_client
from app.services.integration.hub import load_integration_hub, resolve_provider_id
from app.services.integration.providers.registry import get_provider_adapter
from app.services.integration.result_service import build_result_envelope
from app.services.order_governance import current_effective_only_in_job

router = APIRouter(prefix="/results", tags=["results"])


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


@router.get("/jobs/{job_id}")
def get_job_result(
    job_id: str,
    request: Request,
    provider: str | None = Query(default=None),
    task_id: str | None = Query(default=None),
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

    try:
        result = build_result_envelope(
            db,
            job_id=job_id,
            task_id=task_id,
            base_url=str(request.base_url).rstrip("/"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    adapter = get_provider_adapter(ctx.provider)
    provider_view = adapter.build_provider_result(result)
    return {
        "provider": ctx.provider,
        "result": result,
        "provider_view": provider_view,
    }


@router.get("/jobs/{job_id}/records")
def get_job_records(
    job_id: str,
    provider: str | None = Query(default=None),
    document_type: str | None = Query(default=None),
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

    rows = current_effective_only_in_job(db, job_id=job_id)
    if document_type:
        rows = [row for row in rows if row.document_type == document_type]
    return {
        "job_id": job_id,
        "count": len(rows),
        "items": [
            {
                "record_id": r.id,
                "document_type": r.document_type,
                "source_row": r.source_row,
                "payload": r.payload_json or {},
            }
            for r in rows
        ],
    }


@router.get("/jobs/{job_id}/alerts")
def get_job_alerts(
    job_id: str,
    provider: str | None = Query(default=None),
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

    rows = (
        db.query(Alert)
        .filter(Alert.job_id == job_id)
        .order_by(Alert.created_at.desc())
        .all()
    )
    return {
        "job_id": job_id,
        "count": len(rows),
        "items": [
            {
                "id": a.id,
                "alert_type": a.alert_type,
                "severity": a.severity,
                "status": a.status,
                "message": a.message,
                "payload": a.payload_json or {},
                "created_at": a.created_at,
            }
            for a in rows
        ],
    }


@router.get("/jobs/{job_id}/export")
def export_job_result(
    job_id: str,
    kind: str = Query(pattern="^(alerts|customer-summary)$"),
    provider: str | None = Query(default=None),
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

    if kind == "alerts":
        data = export_alerts_csv(db, job_id)
        filename = f"alerts-{job_id}.csv"
    else:
        data = export_customer_summary_csv(db, job_id)
        filename = f"customer-summary-{job_id}.csv"

    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
