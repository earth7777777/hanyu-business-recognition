from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Message(BaseModel):
    message: str


class JobResponse(BaseModel):
    id: str
    status: str
    created_by: str
    created_at: datetime


class FileResponse(BaseModel):
    id: str
    job_id: str
    document_type: str
    filename: str
    parse_status: str
    parse_error: str | None
    parsed_count: int


class GenericList(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)
