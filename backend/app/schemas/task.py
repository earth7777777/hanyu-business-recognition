from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TaskResponse(BaseModel):
    id: str
    job_id: str
    task_type: str
    status: str
    error: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime


class TaskResultResponse(BaseModel):
    task_id: str
    status: str
    output: dict[str, Any] = Field(default_factory=dict)
