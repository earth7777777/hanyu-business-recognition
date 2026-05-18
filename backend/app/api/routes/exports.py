from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import db_dep, role_dep
from app.services.export_service import export_alerts_csv, export_customer_summary_csv

router = APIRouter(prefix="/exports", tags=["exports"])


@router.post("")
def export_data(body: dict, db: Session = Depends(db_dep), role: str = Depends(role_dep)):
    _ = role
    job_id = (body or {}).get("job_id")
    kind = (body or {}).get("kind")
    if not job_id:
        raise HTTPException(status_code=400, detail="job_id is required")
    if kind not in {"alerts", "customer-summary"}:
        raise HTTPException(status_code=400, detail="kind must be alerts or customer-summary")

    if kind == "alerts":
        data = export_alerts_csv(db, job_id)
        filename = f"alerts-{job_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.csv"
    else:
        data = export_customer_summary_csv(db, job_id)
        filename = f"customer-summary-{job_id}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.csv"

    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
