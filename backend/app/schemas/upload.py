from __future__ import annotations

from pydantic import BaseModel, Field


class CreateUploadJobRequest(BaseModel):
    note: str | None = None


class UploadFileResponse(BaseModel):
    file_id: str
    parsed_count: int
    parse_status: str
    parse_error: str | None = None


class StartLobsterTaskRequest(BaseModel):
    job_id: str = Field(min_length=1)


class ExportRequest(BaseModel):
    job_id: str
    kind: str = Field(pattern="^(alerts|customer-summary)$")
