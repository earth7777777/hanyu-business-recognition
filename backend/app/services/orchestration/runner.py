from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import Alert, GroupRecordLink, MatchGroup, NormalizedRecord, TaskRun, UploadJob
from app.services.alert_engine import run_alerts
from app.services.config_service import ConfigService
from app.services.integration.hub import (
    build_orchestrator_profile_from_hub,
    load_integration_hub,
    resolve_provider_id,
)
from app.services.lifecycle_service import (
    ARCHIVE_MODE_AUTO,
    auto_archive_completed_orders_for_job,
    normalize_archive_mode,
    update_archive_run_runtime_status,
)
from app.services.match_engine import run_match
from app.services.order_governance import current_effective_only_in_job
from app.services.orchestration.adapter_registry import get_adapter
from app.services.orchestration.envelope import build_outbound_envelope


_FINAL_STATES = {"succeeded", "failed", "timeout"}
_MANUAL_OVERRIDE_FIELDS = (
    "manual_override_state",
    "manual_override_by",
    "manual_override_at",
    "manual_override_reason",
)
_ALERT_VOLATILE_FIELDS = {
    "last_task_id",
    "resolved_task_id",
    "opened_at",
    "first_opened_at",
    "last_changed_at",
    "resolved_at",
    "superseded_by_job_id",
    "superseded_by_alert_key",
    "resolution_reason",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _legacy_to_profile(legacy: dict[str, Any]) -> dict[str, Any]:
    mode = str(legacy.get("mode") or "mock").lower()
    endpoint = str(legacy.get("endpoint") or "").strip()
    api_key = str(legacy.get("api_key") or "")

    auth = {"type": "bearer", "token": api_key} if api_key else {}
    return {
        "provider": "copaw",
        "transport": "http",
        "mode": mode,
        "submit_url": endpoint,
        "result_url": "",
        "callback_url": "",
        "auth": auth,
        "signature": {"enabled": False, "algorithm": "hmac_sha256", "secret": ""},
        "field_mapping": {},
        "timeout_seconds": int(legacy.get("timeout_seconds") or 30),
    }


def _material_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in _ALERT_VOLATILE_FIELDS}


def _stamp_new_alert_payload(payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    stamped = dict(payload)
    iso = now.isoformat()
    stamped["opened_at"] = iso
    stamped["first_opened_at"] = iso
    stamped["last_changed_at"] = iso
    stamped.pop("resolved_at", None)
    return stamped


def _stamp_open_alert_payload(
    old_payload: dict[str, Any],
    new_payload: dict[str, Any],
    *,
    now: datetime,
    prior_status: str,
    new_message: str,
    old_message: str,
    new_severity: str,
    old_severity: str,
) -> dict[str, Any]:
    stamped = dict(new_payload)
    first_opened_at = str(old_payload.get("first_opened_at") or old_payload.get("opened_at") or "").strip()
    stamped["first_opened_at"] = first_opened_at or now.isoformat()

    previous_last_changed = str(old_payload.get("last_changed_at") or old_payload.get("opened_at") or "").strip()
    material_changed = (
        prior_status != "open"
        or new_message != old_message
        or new_severity != old_severity
        or _material_payload(old_payload) != _material_payload(new_payload)
    )
    stamped["last_changed_at"] = now.isoformat() if material_changed or not previous_last_changed else previous_last_changed
    stamped.pop("resolved_at", None)
    return stamped


def _stamp_resolved_alert_payload(payload: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    stamped = dict(payload)
    iso = now.isoformat()
    stamped["resolved_at"] = iso
    stamped["last_changed_at"] = iso
    return stamped


def _load_orchestrator_profile(
    cfg: ConfigService,
    *,
    task_input: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # New open integration config with provider profiles.
    hub = load_integration_hub(cfg)
    if hub:
        requested_provider = ""
        if isinstance(task_input, dict):
            requested_provider = str(task_input.get("provider") or "").strip()
        provider_id = resolve_provider_id(hub, requested_provider)
        profile = build_orchestrator_profile_from_hub(hub, provider_id)
        if isinstance(profile, dict) and profile.get("provider"):
            return profile

    try:
        profile = cfg.get("orchestrator_profile")
        if isinstance(profile, dict) and profile.get("provider"):
            return profile
    except KeyError:
        pass

    # Backward compatibility for previous lobster connector config.
    try:
        legacy = cfg.get("lobster_connector")
        if isinstance(legacy, dict):
            return _legacy_to_profile(legacy)
    except KeyError:
        pass

    return {
        "provider": "copaw",
        "transport": "http",
        "mode": "mock",
        "submit_url": "",
        "result_url": "",
        "callback_url": "",
        "auth": {},
        "signature": {"enabled": False, "algorithm": "hmac_sha256", "secret": ""},
        "field_mapping": {},
        "timeout_seconds": 30,
    }



def _load_orchestrator_policy(cfg: ConfigService) -> dict[str, Any]:
    try:
        policy = cfg.get("orchestrator_policy")
        if isinstance(policy, dict):
            return {
                "retry_max": int(policy.get("retry_max", 0)),
                "retry_backoff_seconds": float(policy.get("retry_backoff_seconds", 1.0)),
                "poll_interval_seconds": float(policy.get("poll_interval_seconds", 1.0)),
                "result_ttl_hours": int(policy.get("result_ttl_hours", 24)),
            }
    except KeyError:
        pass

    return {
        "retry_max": 0,
        "retry_backoff_seconds": 1.0,
        "poll_interval_seconds": 1.0,
        "result_ttl_hours": 24,
    }


def _preserve_manual_override_fields(existing_payload: dict[str, Any], new_payload: dict[str, Any]) -> dict[str, Any]:
    merged = dict(new_payload)
    for field in _MANUAL_OVERRIDE_FIELDS:
        if field in existing_payload:
            merged[field] = existing_payload.get(field)
        else:
            merged.setdefault(field, None)
    return merged



def _is_rule_enabled(raw: Any, *, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(raw)


def _enabled_alert_types(rule_params: dict[str, Any]) -> set[str]:
    enabled = rule_params.get("enabled", {})
    enabled_cfg = enabled if isinstance(enabled, dict) else {}
    active: set[str] = set()
    if _is_rule_enabled(enabled_cfg.get("due_before_ship"), default=True):
        active.add("due_before_ship")
    if _is_rule_enabled(enabled_cfg.get("ship_after_no_finance"), default=True):
        active.add("ship_after_no_finance")
    return active


def _collect_evaluated_line_keys(groups: list[dict[str, Any]]) -> set[str]:
    line_keys: set[str] = set()
    for group in groups:
        aggregate = group.get("aggregate")
        if not isinstance(aggregate, dict):
            continue
        candidates = aggregate.get("line_candidates")
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            line_key = str(candidate.get("line_key") or "").strip()
            if line_key:
                line_keys.add(line_key)
    return line_keys


def _extract_alert_key_for_line(item: Alert, payload: dict[str, Any]) -> str | None:
    line_key = str(payload.get("line_key") or "").strip()
    if line_key:
        return f"{item.alert_type}|{line_key}"

    payload_alert_key = str(payload.get("alert_key") or "").strip()
    prefix = f"{item.alert_type}|"
    if payload_alert_key.startswith(prefix):
        tail = payload_alert_key[len(prefix) :]
        if tail.startswith("line:"):
            return payload_alert_key
    return None


def _resolve_cross_job_alerts(
    db: Session,
    *,
    task_id: str,
    job_id: str,
    groups: list[dict[str, Any]],
    rule_params: dict[str, Any],
) -> None:
    line_keys = _collect_evaluated_line_keys(groups)
    if not line_keys:
        return

    enabled_alert_types = _enabled_alert_types(rule_params)
    if not enabled_alert_types:
        return

    evaluated_alert_keys = {f"{alert_type}|{line_key}" for alert_type in enabled_alert_types for line_key in line_keys}
    if not evaluated_alert_keys:
        return

    cross_job_open_alerts = (
        db.query(Alert)
        .filter(
            Alert.status == "open",
            Alert.job_id != job_id,
            Alert.alert_type.in_(enabled_alert_types),
        )
        .all()
    )
    now = _utcnow()
    for item in cross_job_open_alerts:
        payload = item.payload_json if isinstance(item.payload_json, dict) else {}
        matched_key = _extract_alert_key_for_line(item, payload)
        if not matched_key:
            continue
        if matched_key not in evaluated_alert_keys:
            continue
        next_payload = dict(payload)
        next_payload["resolved_task_id"] = task_id
        next_payload["superseded_by_job_id"] = job_id
        next_payload["superseded_by_alert_key"] = matched_key
        next_payload["resolution_reason"] = "superseded_by_newer_job_line"
        stamped_payload = _preserve_manual_override_fields(payload, next_payload)
        item.payload_json = _stamp_resolved_alert_payload(stamped_payload, now=now)
        item.status = "resolved"


def _persist_groups_and_alerts(
    db: Session,
    *,
    task_id: str,
    job_id: str,
    groups: list[dict[str, Any]],
    rule_params: dict[str, Any],
) -> int:
    group_id_map: dict[str, str] = {}
    for idx, g in enumerate(groups):
        group_key = str(g.get("group_key") or f"group:{idx}")
        persisted_group_key = f"{group_key}::task:{task_id}::idx:{idx}"
        group = MatchGroup(
            job_id=job_id,
            group_key=persisted_group_key,
            summary_json={
                "task_id": task_id,
                "base_group_key": group_key,
                "signature": g.get("signature"),
                "aggregate": g.get("aggregate", {}),
                "scores": g.get("scores", {}),
            },
        )
        db.add(group)
        db.flush()
        group_id_map[group_key] = group.id

        for role, rec_ids in g.get("records", {}).items():
            if not isinstance(rec_ids, list):
                continue
            for rid in rec_ids:
                db.add(GroupRecordLink(group_id=group.id, record_id=rid, role=role))

    db.commit()

    existing_alerts = db.query(Alert).filter(Alert.job_id == job_id).all()
    existing_map: dict[str, Alert] = {}
    for item in existing_alerts:
        payload = item.payload_json if isinstance(item.payload_json, dict) else {}
        payload_group_key = str(payload.get("group_key") or "")
        key = str(payload.get("alert_key") or f"{item.alert_type}|{payload_group_key}")
        if key:
            existing_map[key] = item

    generated_alerts = run_alerts(groups, rule_params)
    active_keys: set[str] = set()
    open_count = 0
    now = _utcnow()
    for a in generated_alerts:
        alert_type = str(a.get("alert_type") or "unknown")
        base_group_key = str(a.get("group_key") or "")
        group_id = group_id_map.get(base_group_key)
        if not group_id:
            continue

        payload = dict(a.get("payload") or {})
        line_key = str(payload.get("line_key") or "")
        alert_key = f"{alert_type}|{line_key}" if line_key else f"{alert_type}|{base_group_key}"
        active_keys.add(alert_key)
        payload["group_key"] = base_group_key
        payload["alert_key"] = alert_key
        payload["last_task_id"] = task_id

        existed = existing_map.get(alert_key)
        if existed:
            existing_payload = dict(existed.payload_json or {})
            payload = _preserve_manual_override_fields(existing_payload, payload)
            payload = _stamp_open_alert_payload(
                existing_payload,
                payload,
                now=now,
                prior_status=str(existed.status or ""),
                new_message=str(a.get("message") or ""),
                old_message=str(existed.message or ""),
                new_severity=str(a.get("severity") or "medium"),
                old_severity=str(existed.severity or ""),
            )
            existed.group_id = group_id
            existed.severity = str(a.get("severity") or "medium")
            existed.status = "open"
            existed.message = str(a.get("message") or "")
            existed.payload_json = payload
        else:
            payload = _preserve_manual_override_fields({}, payload)
            payload = _stamp_new_alert_payload(payload, now=now)
            db.add(
                Alert(
                    job_id=job_id,
                    group_id=group_id,
                    alert_type=alert_type,
                    severity=str(a.get("severity") or "medium"),
                    status="open",
                    message=str(a.get("message") or ""),
                    payload_json=payload,
                )
            )
        open_count += 1

    for key, existed in existing_map.items():
        if existed.status != "open":
            continue
        if key in active_keys:
            continue
        payload = dict(existed.payload_json or {})
        payload["resolved_task_id"] = task_id
        payload = _preserve_manual_override_fields(payload, payload)
        existed.payload_json = _stamp_resolved_alert_payload(payload, now=now)
        existed.status = "resolved"

    _resolve_cross_job_alerts(
        db,
        task_id=task_id,
        job_id=job_id,
        groups=groups,
        rule_params=rule_params,
    )

    db.commit()
    return open_count



def process_orchestration_task(task_id: str, db: Session) -> None:
    task = db.get(TaskRun, task_id)
    if not task:
        return

    task.status = "running"
    task.updated_at = _utcnow()
    db.commit()

    job = db.get(UploadJob, task.job_id)
    if not job:
        task.status = "failed"
        task.error = f"upload job not found: {task.job_id}"
        task.updated_at = _utcnow()
        db.commit()
        return

    records = current_effective_only_in_job(db, job_id=task.job_id)
    if not records:
        task.status = "failed"
        task.error = "No normalized records found. Upload and parse files first."
        task.updated_at = _utcnow()
        db.commit()
        return

    cfg = ConfigService(db)
    match_template = cfg.get("match_template")
    rule_params = cfg.get("rule_parameters")
    monitoring_policy = cfg.get("operations_monitoring_policy")
    task_input = task.input_json if isinstance(task.input_json, dict) else {}
    profile = _load_orchestrator_profile(cfg, task_input=task_input)
    policy = _load_orchestrator_policy(cfg)

    envelope = build_outbound_envelope(task_id=task.id, job_id=task.job_id, records=records, contract_version="v1")
    provider = str(profile.get("provider") or "copaw")
    adapter = get_adapter(provider)

    connector_result = adapter.submit(envelope, profile)
    ext_id = connector_result.get("external_task_id")

    retries = int(policy.get("retry_max", 0))
    poll_interval = float(policy.get("poll_interval_seconds", 1.0))
    backoff = float(policy.get("retry_backoff_seconds", 1.0))

    attempt = 0
    while connector_result.get("status") not in _FINAL_STATES and ext_id and attempt < retries:
        time.sleep(max(0.0, poll_interval + (attempt * backoff)))
        connector_result = adapter.poll(ext_id, profile, submit_result=connector_result)
        attempt += 1

    groups = run_match(records, match_template)
    alert_count = _persist_groups_and_alerts(
        db,
        task_id=task.id,
        job_id=task.job_id,
        groups=groups,
        rule_params=rule_params,
    )
    archive_result: dict[str, Any] = {
        "status": "succeeded",
        "archived_file_count": 0,
        "archived_record_count": 0,
    }
    archive_mode = normalize_archive_mode(monitoring_policy.get("archive_mode"))
    should_update_archive_runtime = False
    if archive_mode == ARCHIVE_MODE_AUTO:
        try:
            archive_counts = auto_archive_completed_orders_for_job(db, job_id=task.job_id, actor="system")
            archive_result.update(archive_counts)
        except Exception as exc:  # pragma: no cover - defensive, archive should not break orchestration
            archive_result = {
                "status": "failed",
                "archived_file_count": 0,
                "archived_record_count": 0,
                "error": str(exc),
            }
        should_update_archive_runtime = True
    else:
        archive_result = {
            "status": "manual_mode",
            "archived_file_count": 0,
            "archived_record_count": 0,
        }

    task.status = "succeeded"
    task.updated_at = _utcnow()
    task.output_json = {
        "orchestration": connector_result,
        "group_count": len(groups),
        "alert_count": alert_count,
        "archive": archive_result,
    }
    if should_update_archive_runtime:
        update_archive_run_runtime_status(db, archive_result=archive_result, run_at=task.updated_at, trigger="auto")
    db.commit()
