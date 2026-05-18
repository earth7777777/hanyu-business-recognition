from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from app.services.orchestration.adapter_base import ExternalOrchestratorAdapter
from app.services.orchestration.envelope import normalize_provider_result


class CopawAdapter(ExternalOrchestratorAdapter):
    provider = "copaw"

    def submit(self, envelope: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        mode = str(profile.get("mode") or "mock").lower()
        if mode == "mock":
            return self._mock_submit(envelope, profile)
        return self._http_submit(envelope, profile)

    def poll(
        self,
        external_task_id: str,
        profile: dict[str, Any],
        submit_result: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        mode = str(profile.get("mode") or "mock").lower()
        if mode == "mock":
            return submit_result or self._mock_poll(external_task_id, profile)

        result_url = str(profile.get("result_url") or "").strip()
        if not result_url:
            return submit_result or self._mock_poll(external_task_id, profile)

        timeout_seconds = int(profile.get("timeout_seconds") or 30)
        headers = self._build_headers(profile, body={})
        with httpx.Client(timeout=timeout_seconds) as client:
            resp = client.get(result_url, params={"task_id": external_task_id}, headers=headers)
            resp.raise_for_status()
            data = resp.json() if resp.content else {}

        status = self._extract_status(data)
        provider_name = self._resolved_provider(profile)
        return normalize_provider_result(
            provider=provider_name,
            raw_response=data if isinstance(data, dict) else {"raw": data},
            status=status,
            external_task_id=external_task_id,
            normalized_output={},
            error_code=data.get("error_code") if isinstance(data, dict) else None,
            error_message=data.get("error_message") if isinstance(data, dict) else None,
        )

    def _mock_submit(self, envelope: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        time.sleep(0.12)
        ext_id = f"copaw-mock-{uuid.uuid4().hex[:12]}"
        provider_name = self._resolved_provider(profile)
        return normalize_provider_result(
            provider=provider_name,
            raw_response={
                "accepted": True,
                "record_count": len(envelope.get("records", [])),
                "mode": "mock",
            },
            status="succeeded",
            external_task_id=ext_id,
            normalized_output={"accepted": True},
        )

    def _mock_poll(self, external_task_id: str, profile: dict[str, Any]) -> dict[str, Any]:
        provider_name = self._resolved_provider(profile)
        return normalize_provider_result(
            provider=provider_name,
            raw_response={"external_task_id": external_task_id, "mode": "mock"},
            status="succeeded",
            external_task_id=external_task_id,
            normalized_output={},
        )

    def _http_submit(self, envelope: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        submit_url = str(profile.get("submit_url") or "").strip()
        if not submit_url:
            raise ValueError("orchestrator_profile.submit_url is required for copaw http mode")

        timeout_seconds = int(profile.get("timeout_seconds") or 30)
        body = self._map_payload(envelope, profile)
        headers = self._build_headers(profile, body=body)

        with httpx.Client(timeout=timeout_seconds) as client:
            resp = client.post(submit_url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json() if resp.content else {}

        raw = data if isinstance(data, dict) else {"raw": data}
        external_task_id = self._extract_external_task_id(raw)
        status = self._extract_status(raw)
        provider_name = self._resolved_provider(profile)

        return normalize_provider_result(
            provider=provider_name,
            raw_response=raw,
            status=status,
            external_task_id=external_task_id,
            normalized_output={},
            error_code=raw.get("error_code"),
            error_message=raw.get("error_message"),
        )

    def _map_payload(self, envelope: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
        mapping = profile.get("field_mapping") or {}
        if not isinstance(mapping, dict):
            mapping = {}

        renamed = mapping.get("renames") or {}
        if not isinstance(renamed, dict) or not renamed:
            return envelope

        out: dict[str, Any] = {}
        for k, v in envelope.items():
            out_key = str(renamed.get(k) or k)
            out[out_key] = v
        return out

    def _build_headers(self, profile: dict[str, Any], body: dict[str, Any]) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}

        auth = profile.get("auth") or {}
        if isinstance(auth, dict):
            auth_type = str(auth.get("type") or "").lower()
            if auth_type == "bearer" and auth.get("token"):
                headers["Authorization"] = f"Bearer {auth['token']}"
            elif auth_type == "header" and auth.get("key") and auth.get("value"):
                headers[str(auth["key"])] = str(auth["value"])

        sign = profile.get("signature") or {}
        if isinstance(sign, dict) and bool(sign.get("enabled")):
            algorithm = str(sign.get("algorithm") or "hmac_sha256").lower()
            secret = str(sign.get("secret") or "")
            if algorithm == "hmac_sha256" and secret:
                payload = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                ts = str(int(datetime.now(timezone.utc).timestamp()))
                message = f"{ts}.{payload}".encode("utf-8")
                digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
                headers[str(sign.get("timestamp_header") or "X-Timestamp")] = ts
                headers[str(sign.get("header") or "X-Signature")] = digest

        return headers

    def _extract_external_task_id(self, raw: dict[str, Any]) -> str | None:
        for key in ("external_task_id", "task_id", "id"):
            value = raw.get(key)
            if value is not None and str(value).strip():
                return str(value)
        return None

    def _extract_status(self, raw: dict[str, Any]) -> str:
        value = str(raw.get("status") or raw.get("state") or "succeeded").lower()
        if value in {"queued", "running", "pending", "succeeded", "failed", "timeout"}:
            return value
        return "succeeded"

    def _resolved_provider(self, profile: dict[str, Any]) -> str:
        value = str((profile or {}).get("provider") or "").strip().lower()
        return value or self.provider
