from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.db.models import NormalizedRecord, UploadedFile
from app.services.normalize_service import get_core

COMPARE_FIELDS = (
    "latest_outbound_date",
    "executed_shipped_qty",
    "invoiced_qty",
    "uninvoiced_qty",
)
REVIEW_REQUIRED_FIELDS = (
    "customer_order_no",
    "entry_line_no",
    "biz_date",
    "item_name",
    "item_code",
    "latest_outbound_date",
    "executed_shipped_qty",
    "invoiced_qty",
    "uninvoiced_qty",
)
REVIEW_IDENTITY_FIELDS = (
    "customer_order_no",
    "entry_line_no",
    "biz_date",
    "item_name",
    "item_code",
)

CHANGE_NEW = "new"
CHANGE_DUPLICATE = "duplicate"
CHANGE_UPDATE = "update"

IDENTITY_STRICT = "strict_line_no"
IDENTITY_FALLBACK = "legacy_fallback"
IDENTITY_BRIDGE = "legacy_bridge"

LIFECYCLE_ACTIVE = "active"
LIFECYCLE_RECYCLE_BIN = "recycle_bin"
LIFECYCLE_ARCHIVED = "archived"
LIFECYCLE_SPECIAL_CASE = "special_case"

STATUS_CURRENT = "current"
STATUS_INACTIVE = "inactive_old_version"
STATUS_DUPLICATE_SHADOW = "duplicate_shadow"
STATUS_REVIEW_PENDING = "review_pending"
STATUS_RESTORED_HISTORY = "restored_history"
STATUS_REVIEW_RELEASED = "review_released"
STATUS_SPECIAL_CASE = "special_case_retained"

DELETE_ORIGIN_MANUAL_FILE = "manual_file"
DELETE_ORIGIN_MANUAL_RECORD = "manual_record"

_NUMERIC_COMPARE_FIELDS = {
    "executed_shipped_qty",
    "invoiced_qty",
    "uninvoiced_qty",
}
_FLOAT_EPSILON = 1e-9


@dataclass
class GovernanceDecision:
    change_type: str
    identity_mode: str
    governance_reason: str
    candidate: NormalizedRecord | None
    compared_fields_snapshot: dict[str, dict[str, Any]]


def _norm_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _has_meaningful_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _clone_payload(payload_json: dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(payload_json, dict):
        return deepcopy(payload_json)
    return {}


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        text = str(value).replace(",", "").strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _values_equal(field: str, left: Any, right: Any) -> bool:
    if field in _NUMERIC_COMPARE_FIELDS:
        left_num = _to_float(left)
        right_num = _to_float(right)
        if left_num is None:
            left_num = 0.0
        if right_num is None:
            right_num = 0.0
        return abs(left_num - right_num) <= _FLOAT_EPSILON
    return left == right


def _strict_main_key(core: dict[str, Any]) -> str | None:
    order_no = _norm_text(core.get("customer_order_no"))
    entry_line_no = _norm_text(core.get("entry_line_no"))
    if not order_no or not entry_line_no:
        return None
    return f"{order_no}|entry:{entry_line_no}"


def _strict_aux_key(core: dict[str, Any]) -> str | None:
    biz_date = _norm_text(core.get("biz_date"))
    item_code = _norm_text(core.get("item_code"))
    if not biz_date or not item_code:
        return None
    return f"{biz_date}|item:{item_code}"


def strict_identity_key(core: dict[str, Any]) -> str | None:
    main_key = _strict_main_key(core)
    aux_key = _strict_aux_key(core)
    if not main_key or not aux_key:
        return None
    return f"{main_key}|{aux_key}"


def build_strict_identity_key(core: dict[str, Any]) -> str | None:
    return strict_identity_key(core)


def _legacy_identity_key(core: dict[str, Any]) -> str | None:
    customer = _norm_text(core.get("customer"))
    order_key = _norm_text(core.get("customer_order_no") or core.get("contract_no"))
    item_key = _norm_text(core.get("item_code")) or _norm_text(core.get("item_name"))
    if not order_key or not item_key:
        return None
    return f"{customer}|{order_key}|{item_key}"


def _strict_aux_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_key = _strict_aux_key(left)
    return left_key is not None and left_key == _strict_aux_key(right)


def has_entry_line_no(core: dict[str, Any]) -> bool:
    return bool(_norm_text(core.get("entry_line_no")))


def get_governance(payload_json: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload_json, dict):
        return {}
    value = payload_json.get("governance")
    return value if isinstance(value, dict) else {}


def _get_ext(payload_json: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload_json, dict):
        return {}
    value = payload_json.get("ext")
    return value if isinstance(value, dict) else {}


def _review_required_column_presence(payload_json: dict[str, Any]) -> dict[str, bool]:
    ext = _get_ext(payload_json)
    raw = ext.get("review_required_columns_present")
    if not isinstance(raw, dict):
        return {}
    return {str(field): bool(present) for field, present in raw.items()}


def _build_drift_review_reason(payload_json: dict[str, Any]) -> str | None:
    core = get_core(payload_json)
    column_presence = _review_required_column_presence(payload_json)
    missing_columns = [field for field in REVIEW_REQUIRED_FIELDS if column_presence.get(field) is False]
    if missing_columns:
        return f"pending_review_missing_required_columns:{','.join(missing_columns)}"

    missing_identity_values = [field for field in REVIEW_IDENTITY_FIELDS if not _has_meaningful_value(core.get(field))]
    if missing_identity_values:
        return f"pending_review_missing_identity_values:{','.join(missing_identity_values)}"
    return None


def _with_review_reason(decision: GovernanceDecision, review_reason: str | None) -> GovernanceDecision:
    if not review_reason:
        return decision
    return GovernanceDecision(
        change_type=decision.change_type,
        identity_mode=decision.identity_mode,
        governance_reason=review_reason,
        candidate=decision.candidate,
        compared_fields_snapshot=decision.compared_fields_snapshot,
    )


def file_lifecycle_state(file: UploadedFile) -> str:
    state = _norm_text(getattr(file, "lifecycle_state", None))
    if state in {LIFECYCLE_ACTIVE, LIFECYCLE_RECYCLE_BIN, LIFECYCLE_ARCHIVED, LIFECYCLE_SPECIAL_CASE}:
        return state
    if getattr(file, "archived_at", None) is not None:
        return LIFECYCLE_ARCHIVED
    if getattr(file, "deleted_at", None) is not None:
        return LIFECYCLE_RECYCLE_BIN
    return LIFECYCLE_ACTIVE


def record_lifecycle_state(record: NormalizedRecord) -> str:
    state = _norm_text(getattr(record, "lifecycle_state", None))
    if state in {LIFECYCLE_ACTIVE, LIFECYCLE_RECYCLE_BIN, LIFECYCLE_ARCHIVED, LIFECYCLE_SPECIAL_CASE}:
        return state
    if getattr(record, "archived_at", None) is not None:
        return LIFECYCLE_ARCHIVED
    if getattr(record, "deleted_at", None) is not None:
        return LIFECYCLE_RECYCLE_BIN
    return LIFECYCLE_ACTIVE


def record_version_status(record: NormalizedRecord) -> str:
    raw = _norm_text(getattr(record, "version_status", None))
    if raw:
        return raw
    governance = get_governance(record.payload_json)
    raw = _norm_text(governance.get("version_status"))
    if raw:
        return raw
    return STATUS_CURRENT


def is_current_effective_payload(payload_json: dict[str, Any] | None, *, document_type: str | None = None) -> bool:
    if document_type and document_type != "order":
        return True
    governance = get_governance(payload_json)
    return governance.get("is_current_effective", True) is not False


def is_current_effective_record(record: NormalizedRecord) -> bool:
    value = getattr(record, "is_current_effective", None)
    if value is not None:
        return bool(value)
    return is_current_effective_payload(record.payload_json, document_type=record.document_type)


def is_record_current_effective(record: NormalizedRecord) -> bool:
    return is_current_effective_record(record)


def is_file_active(file: UploadedFile) -> bool:
    return file_lifecycle_state(file) == LIFECYCLE_ACTIVE


def is_record_active(record: NormalizedRecord) -> bool:
    return record_lifecycle_state(record) == LIFECYCLE_ACTIVE


def sync_record_governance(
    record: NormalizedRecord,
    *,
    change_type: str | None = None,
    identity_mode: str | None = None,
    governance_reason: str | None = None,
    compared_fields_snapshot: dict[str, Any] | None = None,
) -> None:
    payload = _clone_payload(record.payload_json)
    governance = dict(get_governance(payload))
    if change_type is not None:
        governance["change_type"] = change_type
    if identity_mode is not None:
        governance["identity_mode"] = identity_mode
    if governance_reason is not None:
        governance["governance_reason"] = governance_reason
    if compared_fields_snapshot is not None:
        governance["compared_fields_snapshot"] = compared_fields_snapshot

    governance["version_status"] = record_version_status(record)
    governance["is_current_effective"] = is_current_effective_record(record)
    governance["duplicate_of_record_id"] = getattr(record, "duplicate_of_record_id", None)
    governance["superseded_by_record_id"] = getattr(record, "superseded_by_record_id", None)
    governance["supersedes_record_id"] = getattr(record, "supersedes_record_id", None)
    governance["lifecycle_state"] = record_lifecycle_state(record)
    governance["delete_origin"] = getattr(record, "delete_origin", None)
    payload["governance"] = governance
    record.payload_json = payload


def filter_current_effective_records(records: Iterable[NormalizedRecord]) -> list[NormalizedRecord]:
    out: list[NormalizedRecord] = []
    for record in records:
        if not is_record_active(record):
            continue
        file = getattr(record, "file", None)
        if isinstance(file, UploadedFile) and not is_file_active(file):
            continue
        if record.document_type == "order" and not is_current_effective_record(record):
            continue
        out.append(record)
    return out


def current_effective_only_in_job(db: Session, *, job_id: str) -> list[NormalizedRecord]:
    rows = (
        db.query(NormalizedRecord)
        .join(UploadedFile, UploadedFile.id == NormalizedRecord.file_id)
        .filter(
            NormalizedRecord.job_id == job_id,
            UploadedFile.lifecycle_state == LIFECYCLE_ACTIVE,
            NormalizedRecord.lifecycle_state == LIFECYCLE_ACTIVE,
        )
        .order_by(NormalizedRecord.created_at.asc())
        .all()
    )
    return filter_current_effective_records(rows)


def build_match_identity_key(
    core: dict[str, Any],
    *,
    fallback_record_id: str,
    source_row: int = 0,
) -> tuple[str, str, str]:
    strict_key = strict_identity_key(core)
    if strict_key:
        return f"strict:{strict_key}", IDENTITY_STRICT, "high"

    if has_entry_line_no(core):
        return f"strict-incomplete:{fallback_record_id}", IDENTITY_STRICT, "low"

    legacy_key = _legacy_identity_key(core)
    if legacy_key:
        return f"legacy:{legacy_key}", IDENTITY_FALLBACK, "high"

    return f"legacy-row:{fallback_record_id}:{int(source_row or 0)}", IDENTITY_FALLBACK, "low"


def build_match_business_key(core: dict[str, Any]) -> tuple[str, str]:
    strict_key = strict_identity_key(core)
    if strict_key:
        return f"strict:{strict_key}", IDENTITY_STRICT

    legacy_key = _legacy_identity_key(core)
    if legacy_key:
        return f"legacy:{legacy_key}", IDENTITY_FALLBACK

    fallback = _strict_main_key(core)
    if fallback:
        return f"strict-incomplete:{fallback}", IDENTITY_STRICT
    return "legacy:missing-identity", IDENTITY_FALLBACK


def _candidate_rank(record: NormalizedRecord) -> tuple[str, int, str]:
    created = getattr(record, "created_at", None)
    created_ord = int(created.timestamp()) if created is not None else -1
    return (record.job_id, created_ord, record.id)


def _pick_candidate(rows: list[NormalizedRecord]) -> NormalizedRecord | None:
    if not rows:
        return None
    return max(rows, key=_candidate_rank)


def _build_compared_fields_snapshot(
    new_core: dict[str, Any],
    candidate_core: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    old_core = candidate_core or {}
    for field in COMPARE_FIELDS:
        incoming_value = new_core.get(field)
        current_value = old_core.get(field)
        snapshot[field] = {
            "incoming": incoming_value,
            "current": current_value,
            "same": _values_equal(field, incoming_value, current_value),
        }
    return snapshot


def _classify_change(snapshot: dict[str, dict[str, Any]]) -> str:
    if not snapshot:
        return CHANGE_NEW
    if all(bool(item.get("same")) for item in snapshot.values()):
        return CHANGE_DUPLICATE
    return CHANGE_UPDATE


def _current_order_candidates(
    db: Session,
    *,
    exclude_record_id: str | None = None,
) -> list[NormalizedRecord]:
    rows = (
        db.query(NormalizedRecord)
        .join(UploadedFile, UploadedFile.id == NormalizedRecord.file_id)
        .filter(
            NormalizedRecord.document_type == "order",
            NormalizedRecord.lifecycle_state == LIFECYCLE_ACTIVE,
            UploadedFile.lifecycle_state == LIFECYCLE_ACTIVE,
        )
        .all()
    )
    return [
        row
        for row in rows
        if is_current_effective_record(row) and (exclude_record_id is None or row.id != exclude_record_id)
    ]


def classify_order_record(
    db: Session,
    *,
    payload_json: dict[str, Any],
    exclude_record_id: str | None = None,
) -> GovernanceDecision:
    new_core = get_core(payload_json)
    drift_review_reason = _build_drift_review_reason(payload_json)

    current_rows = _current_order_candidates(db, exclude_record_id=exclude_record_id)

    strict_main_key = _strict_main_key(new_core)
    strict_aux_key = _strict_aux_key(new_core)
    legacy_key = _legacy_identity_key(new_core)

    if has_entry_line_no(new_core):
        if strict_main_key and strict_aux_key:
            strict_matches: list[NormalizedRecord] = []
            strict_main_hits: list[NormalizedRecord] = []
            for row in current_rows:
                row_core = get_core(row.payload_json)
                if _strict_main_key(row_core) == strict_main_key:
                    strict_main_hits.append(row)
                    if _strict_aux_key(row_core) == strict_aux_key:
                        strict_matches.append(row)

            candidate = _pick_candidate(strict_matches)
            if candidate:
                snapshot = _build_compared_fields_snapshot(new_core, get_core(candidate.payload_json))
                return _with_review_reason(
                    GovernanceDecision(
                        change_type=_classify_change(snapshot),
                        identity_mode=IDENTITY_STRICT,
                        governance_reason="strict_identity_match",
                        candidate=candidate,
                        compared_fields_snapshot=snapshot,
                    ),
                    drift_review_reason,
                )

            if legacy_key:
                bridge_matches = [
                    row
                    for row in current_rows
                    if not has_entry_line_no(get_core(row.payload_json))
                    and _strict_aux_match(get_core(row.payload_json), new_core)
                    and _legacy_identity_key(get_core(row.payload_json)) == legacy_key
                ]
                bridge_candidate = _pick_candidate(bridge_matches)
                if bridge_candidate:
                    snapshot = _build_compared_fields_snapshot(new_core, get_core(bridge_candidate.payload_json))
                    return _with_review_reason(
                        GovernanceDecision(
                            change_type=_classify_change(snapshot),
                            identity_mode=IDENTITY_BRIDGE,
                            governance_reason="legacy_bridge_match",
                            candidate=bridge_candidate,
                            compared_fields_snapshot=snapshot,
                        ),
                        drift_review_reason,
                    )

            if strict_main_hits:
                return _with_review_reason(
                    GovernanceDecision(
                        change_type=CHANGE_NEW,
                        identity_mode=IDENTITY_STRICT,
                        governance_reason="main_hit_aux_mismatch",
                        candidate=None,
                        compared_fields_snapshot={},
                    ),
                    drift_review_reason,
                )

            return _with_review_reason(
                GovernanceDecision(
                    change_type=CHANGE_NEW,
                    identity_mode=IDENTITY_STRICT,
                    governance_reason="strict_identity_no_match",
                    candidate=None,
                    compared_fields_snapshot={},
                ),
                drift_review_reason,
            )

        return _with_review_reason(
            GovernanceDecision(
                change_type=CHANGE_NEW,
                identity_mode=IDENTITY_STRICT,
                governance_reason="missing_auxiliary_for_strict_identity",
                candidate=None,
                compared_fields_snapshot={},
            ),
            drift_review_reason,
        )

    if legacy_key:
        legacy_matches = [
            row
            for row in current_rows
            if _strict_aux_match(get_core(row.payload_json), new_core)
            and _legacy_identity_key(get_core(row.payload_json)) == legacy_key
        ]
        candidate = _pick_candidate(legacy_matches)
        if candidate:
            snapshot = _build_compared_fields_snapshot(new_core, get_core(candidate.payload_json))
            return _with_review_reason(
                GovernanceDecision(
                    change_type=_classify_change(snapshot),
                    identity_mode=IDENTITY_FALLBACK,
                    governance_reason="legacy_fallback_match",
                    candidate=candidate,
                    compared_fields_snapshot=snapshot,
                ),
                drift_review_reason,
            )

        return _with_review_reason(
            GovernanceDecision(
                change_type=CHANGE_NEW,
                identity_mode=IDENTITY_FALLBACK,
                governance_reason="legacy_fallback_no_match",
                candidate=None,
                compared_fields_snapshot={},
            ),
            drift_review_reason,
        )

    return _with_review_reason(
        GovernanceDecision(
            change_type=CHANGE_NEW,
            identity_mode=IDENTITY_FALLBACK,
            governance_reason="insufficient_identity_for_legacy_fallback",
            candidate=None,
            compared_fields_snapshot={},
        ),
        drift_review_reason,
    )


def apply_order_governance(
    db: Session,
    record: NormalizedRecord,
    payload_json: dict[str, Any] | None = None,
) -> GovernanceDecision | None:
    if record.document_type != "order":
        return None

    effective_payload = payload_json if isinstance(payload_json, dict) else record.payload_json or {}
    decision = classify_order_record(db, payload_json=effective_payload, exclude_record_id=record.id)
    record.lifecycle_state = LIFECYCLE_ACTIVE
    record.deleted_at = None
    record.deleted_by = None
    record.delete_reason = None
    record.delete_origin = None
    record.restored_at = None
    record.restored_by = None
    record.restore_reason = None
    record.archived_at = None
    record.archived_by = None
    record.archive_reason = None
    record.pre_delete_version_status = None
    record.pre_delete_is_current_effective = None

    if should_hold_for_review(decision):
        record.version_status = STATUS_REVIEW_PENDING
        record.is_current_effective = False
        record.duplicate_of_record_id = None
        record.superseded_by_record_id = None
        record.supersedes_record_id = None
    elif decision.change_type == CHANGE_DUPLICATE:
        record.version_status = STATUS_DUPLICATE_SHADOW
        record.is_current_effective = False
        record.duplicate_of_record_id = decision.candidate.id if decision.candidate else None
        record.superseded_by_record_id = None
        record.supersedes_record_id = None
    elif decision.change_type == CHANGE_UPDATE:
        record.version_status = STATUS_CURRENT
        record.is_current_effective = True
        record.duplicate_of_record_id = None
        record.superseded_by_record_id = None
        record.supersedes_record_id = decision.candidate.id if decision.candidate else None
    else:
        record.version_status = STATUS_CURRENT
        record.is_current_effective = True
        record.duplicate_of_record_id = None
        record.superseded_by_record_id = None
        record.supersedes_record_id = None

    record.payload_json = _clone_payload(effective_payload)
    sync_record_governance(
        record,
        change_type=decision.change_type,
        identity_mode=decision.identity_mode,
        governance_reason=_governance_reason_for_record(decision),
        compared_fields_snapshot=decision.compared_fields_snapshot,
    )

    if should_hold_for_review(decision):
        return decision

    if decision.change_type != CHANGE_UPDATE or decision.candidate is None:
        return decision

    decision.candidate.lifecycle_state = LIFECYCLE_ACTIVE
    decision.candidate.version_status = STATUS_INACTIVE
    decision.candidate.is_current_effective = False
    decision.candidate.duplicate_of_record_id = None
    decision.candidate.supersedes_record_id = None
    decision.candidate.superseded_by_record_id = record.id
    sync_record_governance(
        decision.candidate,
        governance_reason="superseded_by_newer_record",
    )
    return decision


def should_auto_delete_duplicate_record(decision: GovernanceDecision | None) -> bool:
    if decision is None:
        return False
    return (
        decision.change_type == CHANGE_DUPLICATE
        and decision.identity_mode == IDENTITY_STRICT
        and decision.governance_reason == "strict_identity_match"
    )


def should_hold_for_review(decision: GovernanceDecision | None) -> bool:
    if decision is None:
        return False
    if str(decision.governance_reason or "").startswith("pending_review_"):
        return True
    if decision.candidate is None:
        return False
    return decision.identity_mode in {IDENTITY_FALLBACK, IDENTITY_BRIDGE}


def _governance_reason_for_record(decision: GovernanceDecision) -> str:
    if not should_hold_for_review(decision):
        return decision.governance_reason
    if str(decision.governance_reason or "").startswith("pending_review_"):
        return decision.governance_reason
    if decision.identity_mode == IDENTITY_BRIDGE:
        return "pending_review_legacy_bridge_match"
    return "pending_review_legacy_fallback_match"
