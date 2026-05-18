from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import db_dep, role_dep
from app.db.models import Alert, MatchGroup
from app.schemas.alert import AlertItem, CustomerSummaryItem

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertItem])
def list_alerts(job_id: str | None = None, db: Session = Depends(db_dep), role: str = Depends(role_dep)):
    _ = role
    query = db.query(Alert)
    if job_id:
        query = query.filter(Alert.job_id == job_id)

    rows = query.order_by(Alert.created_at.desc()).all()
    return [
        AlertItem(
            id=a.id,
            job_id=a.job_id,
            group_id=a.group_id,
            alert_type=a.alert_type,
            severity=a.severity,
            status=a.status,
            message=a.message,
            payload=a.payload_json or {},
            created_at=a.created_at,
        )
        for a in rows
    ]


@router.get("/customer-summary", response_model=list[CustomerSummaryItem])
def customer_summary(job_id: str, db: Session = Depends(db_dep), role: str = Depends(role_dep)):
    _ = role
    rows = (
        db.query(Alert, MatchGroup)
        .join(MatchGroup, Alert.group_id == MatchGroup.id)
        .filter(Alert.job_id == job_id, Alert.status == "open")
        .all()
    )

    stat = defaultdict(lambda: {"alert_count": 0, "high_count": 0, "medium_count": 0})
    for alert, group in rows:
        customer = ((group.summary_json or {}).get("aggregate") or {}).get("customer") or "未知客户"
        stat[customer]["alert_count"] += 1
        if alert.severity == "high":
            stat[customer]["high_count"] += 1
        else:
            stat[customer]["medium_count"] += 1

    return [
        CustomerSummaryItem(
            customer=customer,
            alert_count=item["alert_count"],
            high_count=item["high_count"],
            medium_count=item["medium_count"],
        )
        for customer, item in sorted(stat.items(), key=lambda x: x[1]["alert_count"], reverse=True)
    ]
