from __future__ import annotations

import csv
import io
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Alert, MatchGroup



def export_alerts_csv(db: Session, job_id: str) -> bytes:
    alerts = (
        db.query(Alert)
        .filter(Alert.job_id == job_id, Alert.status == "open")
        .order_by(Alert.created_at.desc())
        .all()
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["alert_id", "job_id", "group_id", "alert_type", "severity", "status", "message", "created_at"])
    for a in alerts:
        writer.writerow([a.id, a.job_id, a.group_id, a.alert_type, a.severity, a.status, a.message, a.created_at.isoformat()])
    return output.getvalue().encode("utf-8-sig")



def export_customer_summary_csv(db: Session, job_id: str) -> bytes:
    rows = (
        db.query(Alert, MatchGroup)
        .join(MatchGroup, Alert.group_id == MatchGroup.id)
        .filter(Alert.job_id == job_id, Alert.status == "open")
        .all()
    )

    stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"alert_count": 0, "high_count": 0, "medium_count": 0})
    for alert, group in rows:
        customer = ((group.summary_json or {}).get("aggregate") or {}).get("customer") or "未知客户"
        stats[customer]["alert_count"] += 1
        if alert.severity == "high":
            stats[customer]["high_count"] += 1
        else:
            stats[customer]["medium_count"] += 1

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["customer", "alert_count", "high_count", "medium_count"])
    for customer, item in sorted(stats.items(), key=lambda x: x[1]["alert_count"], reverse=True):
        writer.writerow([customer, item["alert_count"], item["high_count"], item["medium_count"]])
    return output.getvalue().encode("utf-8-sig")
