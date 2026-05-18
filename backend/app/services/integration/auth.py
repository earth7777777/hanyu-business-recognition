from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExternalClientContext:
    client_id: str
    provider: str
    allow_doc_types: set[str] = field(default_factory=set)
    raw: dict[str, Any] = field(default_factory=dict)


def validate_external_client(
    *,
    hub: dict[str, Any],
    client_id: str,
    token: str,
    provider: str,
) -> ExternalClientContext | None:
    clients = hub.get("auth_clients")
    if not isinstance(clients, dict):
        return None
    client = clients.get(client_id)
    if not isinstance(client, dict):
        return None
    if not bool(client.get("enabled")):
        return None

    expected = str(client.get("token") or "")
    if not expected or not hmac.compare_digest(expected, token):
        return None

    allowed_providers = client.get("providers")
    if isinstance(allowed_providers, list) and allowed_providers:
        if provider not in {str(x) for x in allowed_providers}:
            return None

    allow_doc_types = client.get("allow_doc_types")
    if isinstance(allow_doc_types, list):
        doc_set = {str(x).strip().lower() for x in allow_doc_types if str(x).strip()}
    else:
        doc_set = set()

    return ExternalClientContext(
        client_id=client_id,
        provider=provider,
        allow_doc_types=doc_set,
        raw=client,
    )

