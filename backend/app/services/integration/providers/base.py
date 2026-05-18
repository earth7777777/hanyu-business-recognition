from __future__ import annotations

from abc import ABC
from typing import Any


class ExternalProviderAdapter(ABC):
    provider_type: str = "generic"

    def normalize_intake_metadata(self, body: dict[str, Any]) -> dict[str, Any]:
        source = body.get("source")
        if isinstance(source, dict):
            return source
        return {}

    def build_provider_result(
        self,
        result_envelope: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "provider": self.provider_type,
            "result": result_envelope,
        }

