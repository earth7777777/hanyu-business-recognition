from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AlertItem(BaseModel):
    id: str
    job_id: str
    group_id: str
    alert_type: str
    severity: str
    status: str
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class CustomerSummaryItem(BaseModel):
    customer: str
    alert_count: int
    high_count: int
    medium_count: int
