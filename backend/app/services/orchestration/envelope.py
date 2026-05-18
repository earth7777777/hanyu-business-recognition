from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()



def build_outbound_envelope(
    *,
    task_id: str,
    job_id: str,
    records: list[Any],
    contract_version: str = "v1",
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for rec in records:
        payload = rec.payload_json if isinstance(rec.payload_json, dict) else {}
        items.append(
            {
                "record_id": rec.id,
                "document_type": rec.document_type,
                "core": payload.get("core", {}),
                "ext": payload.get("ext", {}),
                "attachments": payload.get("attachments", {}),
                "trace": payload.get("trace", {}),
            }
        )

    return {
        "task_id": task_id,
        "job_id": job_id,
        "contract_version": contract_version,
        "submitted_at": _utcnow_iso(),
        "records": items,
    }



def normalize_provider_result(
    *,
    provider: str,
    raw_response: dict[str, Any],
    status: str,
    external_task_id: str | None,
    normalized_output: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "external_task_id": external_task_id,
        "status": status,
        "accepted_at": _utcnow_iso(),
        "finished_at": _utcnow_iso() if status in {"succeeded", "failed", "timeout"} else None,
        "normalized_output": normalized_output or {},
        "raw_response": raw_response,
        "error_code": error_code,
        "error_message": error_message,
    }
