from __future__ import annotations

from app.services.integration.providers.base import ExternalProviderAdapter
from app.services.integration.providers.copaw_provider import CopawProviderAdapter


_REGISTRY: dict[str, type[ExternalProviderAdapter]] = {
    "copaw": CopawProviderAdapter,
}


def get_provider_adapter(provider_type: str) -> ExternalProviderAdapter:
    key = (provider_type or "").strip().lower()
    cls = _REGISTRY.get(key)
    if not cls:
        adapter = ExternalProviderAdapter()
        adapter.provider_type = key or "generic"
        return adapter
    return cls()
