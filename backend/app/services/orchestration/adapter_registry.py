from __future__ import annotations

from app.services.orchestration.adapter_base import ExternalOrchestratorAdapter
from app.services.orchestration.adapters.copaw_adapter import CopawAdapter


_REGISTRY: dict[str, type[ExternalOrchestratorAdapter]] = {
    "copaw": CopawAdapter,
    "generic": CopawAdapter,
    "http": CopawAdapter,
}



def get_adapter(provider: str) -> ExternalOrchestratorAdapter:
    key = (provider or "").strip().lower()
    cls = _REGISTRY.get(key)
    if not cls:
        # Generic HTTP adapter fallback keeps provider onboarding open by config.
        cls = CopawAdapter
    return cls()
