from __future__ import annotations

from typing import Any

from app.services.orchestration.adapter_registry import get_adapter
from app.services.orchestration.envelope import normalize_provider_result


class LobsterConnector:
    """
    Legacy compatibility wrapper.
    New code should use services.orchestration directly.
    """

    def run(self, payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        adapter = get_adapter("copaw")
        profile = {
            "provider": "copaw",
            "mode": str(config.get("mode") or "mock"),
            "submit_url": str(config.get("endpoint") or ""),
            "result_url": "",
            "auth": {"type": "bearer", "token": str(config.get("api_key") or "")},
            "timeout_seconds": int(config.get("timeout_seconds") or 30),
            "field_mapping": {},
        }
        try:
            return adapter.submit(payload, profile)
        except Exception as exc:
            return normalize_provider_result(
                provider="copaw",
                raw_response={"legacy_connector_error": str(exc)},
                status="failed",
                external_task_id=None,
                error_code="legacy_connector_failed",
                error_message=str(exc),
            )
