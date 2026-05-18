from __future__ import annotations

from typing import Any

from app.services.integration.providers.base import ExternalProviderAdapter


class CopawProviderAdapter(ExternalProviderAdapter):
    provider_type = "copaw"

    def normalize_intake_metadata(self, body: dict[str, Any]) -> dict[str, Any]:
        source = super().normalize_intake_metadata(body)
        source.setdefault("channel", body.get("channel") or "copaw")
        return source

    def build_provider_result(
        self,
        result_envelope: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "provider": self.provider_type,
            "status": result_envelope.get("status"),
            "job_id": result_envelope.get("job_id"),
            "task_id": result_envelope.get("task_id"),
            "alerts_summary": result_envelope.get("alerts_summary", {}),
            "records_summary": result_envelope.get("records_summary", {}),
            "exports": result_envelope.get("export_links", {}),
            "trace": result_envelope.get("trace", {}),
            "provider_raw": result_envelope.get("provider_raw", {}),
        }

