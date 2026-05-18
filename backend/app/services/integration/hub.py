from __future__ import annotations

from typing import Any

from app.services.config_service import ConfigService


def load_integration_hub(cfg: ConfigService) -> dict[str, Any]:
    try:
        val = cfg.get("integration_hub")
        if isinstance(val, dict):
            return val
    except KeyError:
        pass
    return {}


def resolve_provider_id(hub: dict[str, Any], requested: str | None = None) -> str:
    providers = hub.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    if requested and requested in providers:
        return requested
    default_provider = str(hub.get("default_provider") or "").strip()
    if default_provider and default_provider in providers:
        return default_provider
    if providers:
        return next(iter(providers.keys()))
    return "copaw"


def build_orchestrator_profile_from_hub(
    hub: dict[str, Any],
    provider_id: str,
) -> dict[str, Any]:
    providers = hub.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    profile = providers.get(provider_id)
    if not isinstance(profile, dict):
        return {}
    if not bool(profile.get("enabled", True)):
        return {}

    transport = profile.get("transport")
    if not isinstance(transport, dict):
        transport = {}

    auth = profile.get("auth")
    if not isinstance(auth, dict):
        auth = {}

    signature = profile.get("signature")
    if not isinstance(signature, dict):
        signature = {}

    mapping = profile.get("mapping")
    if not isinstance(mapping, dict):
        mapping = {}
    field_mapping = mapping.get("field_mapping")
    if not isinstance(field_mapping, dict):
        field_mapping = mapping

    provider_type = str(profile.get("provider_type") or provider_id or "copaw")
    return {
        "provider": provider_type,
        "transport": "http",
        "mode": str(transport.get("mode") or "mock"),
        "submit_url": str(transport.get("submit_url") or ""),
        "result_url": str(transport.get("result_url") or ""),
        "callback_url": str(transport.get("callback_url") or ""),
        "auth": auth,
        "signature": {
            "enabled": bool(signature.get("enabled", False)),
            "algorithm": str(signature.get("algorithm") or "hmac_sha256"),
            "secret": str(signature.get("secret") or ""),
            "header": str(signature.get("header") or "X-Signature"),
            "timestamp_header": str(signature.get("timestamp_header") or "X-Timestamp"),
        },
        "field_mapping": field_mapping,
        "timeout_seconds": int(transport.get("timeout_seconds") or 30),
    }
