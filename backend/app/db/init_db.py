from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.core.settings import BACKUP_ROOT_DIR, LOG_ROOT_DIR
from app.db.base import Base
from app.db.models import ConfigEntry, NormalizedRecord, UploadedFile
from app.db.session import engine, SessionLocal
from app.services.normalize_service import derive_order_unshipped_qty
from app.services.order_governance import (
    LIFECYCLE_ACTIVE,
    LIFECYCLE_ARCHIVED,
    LIFECYCLE_RECYCLE_BIN,
    LIFECYCLE_SPECIAL_CASE,
    STATUS_SPECIAL_CASE,
)
from app.services.uninvoiced_sorting_config import (
    DEFAULT_UNINVOICED_EXPORT_SORTING,
    UNINVOICED_EXPORT_SORTING_CONFIG_KEY,
)


DEFAULTS: dict[str, dict] = {
    "field_mappings": {
        "order": {
            "customer": ["客户", "客户名称", "customer"],
            "contract_no": ["合同号", "contract_no"],
            "customer_order_no": ["客户订单号", "订单号", "order_no", "单据编号", "单据号", "订单编号", "单号"],
            "entry_line_no": ["分录行号", "行号", "明细行号", "分录号", "entry_line_no"],
            "item_code": ["料号", "物料编码", "商品编码", "item_code"],
            "item_name": ["品名", "物料名称", "商品名称", "item_name"],
            "quantity": ["数量", "qty"],
            "amount": ["价税合计", "金额", "amount"],
            "order_total_amount": ["成交金额", "订单金额", "订单总金额", "order_total_amount", "order_amount"],
            "tax_inclusive_unit_price": ["含税单价", "tax_inclusive_unit_price", "unit_price_with_tax"],
            "biz_date": ["日期", "订单日期", "单据日期", "date"],
            "due_date": ["交期", "交货日期", "due_date", "预计交货日期", "预计交期"],
            "order_closed": ["关闭状态", "订单关闭状态", "关闭订单", "是否关闭", "closed"],
            "line_closed": ["行关闭状态", "明细关闭状态", "分录关闭状态", "是否行关闭"],
            "latest_outbound_date": ["最近出库日期", "最新出库日期", "最后出库日期", "最近发货日期"],
            "order_outbound_status": ["出库状态", "整单出库状态", "订单出库状态", "order_outbound_status"],
            "line_outbound_status": ["行出库状态", "分录出库状态", "明细出库状态", "line_outbound_status"],
            "executed_shipped_qty": ["行已执行已出库数量", "已执行已出库数量", "累计出库数量", "已出库数量"],
            "invoiced_qty": ["行已开票数量", "已开票数量", "累计开票数量"],
            "uninvoiced_qty": ["行未开票数量", "未开票数量", "待开票数量"],
            "line_invoice_status": ["行开票状态", "开票状态", "发票状态"],
        },
        "shipment": {
            "customer": ["客户", "customer"],
            "contract_no": ["合同号", "contract_no"],
            "customer_order_no": ["客户订单号", "订单号", "order_no"],
            "item_code": ["料号", "物料编码", "item_code"],
            "item_name": ["品名", "item_name"],
            "quantity": ["发货数量", "数量", "ship_qty", "qty"],
            "amount": ["金额", "amount"],
            "ship_date": ["发货日期", "ship_date", "日期"],
        },
        "payment_notice": {
            "customer": ["客户", "customer"],
            "contract_no": ["合同号", "contract_no"],
            "customer_order_no": ["客户订单号", "订单号", "order_no"],
            "item_code": ["料号", "item_code"],
            "item_name": ["品名", "item_name"],
            "amount": ["金额", "notice_amount", "amount"],
            "notice_date": ["通知日期", "notice_date", "日期"],
        },
        "invoice": {
            "customer": ["客户", "customer"],
            "contract_no": ["合同号", "contract_no"],
            "customer_order_no": ["客户订单号", "订单号", "order_no"],
            "item_code": ["料号", "item_code"],
            "item_name": ["品名", "item_name"],
            "amount": ["开票金额", "金额", "invoice_amount", "amount"],
            "invoice_date": ["开票日期", "invoice_date", "日期"],
            "invoice_formal": ["正式发票", "invoice_formal", "is_formal"],
        },
    },
    "match_template": {
        "name": "default-composite-v1",
        "quantity_primary": True,
        "amount_auxiliary": True,
        "date_tolerance_days": 7,
        "quantity_tolerance_pct": 0.05,
        "amount_tolerance_pct": 0.05,
        "components": [
            "customer",
            "contract_or_customer_order",
            "item_code",
            "item_name",
            "quantity_primary",
            "amount_auxiliary",
            "date",
        ],
    },
    "rule_parameters": {
        "enabled": {
            "due_before_ship": True,
            "ship_after_no_finance": True,
        },
        "due_before_ship_days": 5,
        "ship_after_no_finance_days": 60,
        "ship_after_no_finance_require_order_fully_outbound": False,
    },
    UNINVOICED_EXPORT_SORTING_CONFIG_KEY: DEFAULT_UNINVOICED_EXPORT_SORTING,
    "orchestrator_profile": {
        "provider": "copaw",
        "transport": "http",
        "mode": "mock",
        "submit_url": "",
        "result_url": "",
        "callback_url": "",
        "auth": {},
        "signature": {
            "enabled": False,
            "algorithm": "hmac_sha256",
            "secret": "",
            "header": "X-Signature",
            "timestamp_header": "X-Timestamp",
        },
        "field_mapping": {},
        "timeout_seconds": 30,
    },
    "orchestrator_policy": {
        "retry_max": 0,
        "retry_backoff_seconds": 1.0,
        "poll_interval_seconds": 1.0,
        "result_ttl_hours": 24,
    },
    "data_retention_policy": {
        "keep_current_effective_order_records_forever": True,
        "keep_order_history_versions_forever": True,
        "recycle_bin_retention_days": 30,
        "hard_delete_allowed_from": "recycle_bin_only",
    },
    "operations_monitoring_policy": {
        "review_queue_warn_threshold": 10,
        "parse_failed_warn_threshold": 1,
        "failed_task_warn_threshold": 1,
        "backup_overdue_hours": 24,
        "slow_request_threshold_ms": 1500,
        "slow_request_keep_latest": 10,
        "archive_run_overdue_hours": 24,
        "archive_mode": "auto",
        "backup_schedule_time": "02:00",
        "backup_retention_days": 30,
        "db_backup_enabled": True,
        "db_backup_target_path": str(BACKUP_ROOT_DIR / "db"),
        "file_backup_enabled": True,
        "file_backup_target_path": str(BACKUP_ROOT_DIR / "uploads"),
        "log_cleanup_enabled": True,
        "log_cleanup_schedule_time": "03:00",
        "log_retention_days": 30,
        "restore_drill_database_url": "",
    },
    "operations_runtime_status": {
        "db_backup": {
            "last_success_at": None,
            "last_status": "unknown",
            "last_error": "",
            "last_started_at": None,
            "last_finished_at": None,
            "last_snapshot_label": "",
        },
        "file_backup": {
            "last_success_at": None,
            "last_status": "unknown",
            "last_error": "",
            "last_started_at": None,
            "last_finished_at": None,
            "last_snapshot_label": "",
        },
        "slow_requests": {
            "last_seen_at": None,
            "total_count": 0,
            "slowest_duration_ms": 0,
            "slowest_path": "",
            "slowest_method": "",
            "slowest_status_code": 0,
            "slowest_query": "",
            "recent_items": [],
        },
        "archive_run": {
            "last_run_at": None,
            "last_status": "never",
            "last_error": "",
            "last_archived_file_count": 0,
            "last_archived_record_count": 0,
            "last_trigger": "auto",
        },
        "archive_preview": {
            "last_run_at": None,
            "last_status": "never",
            "last_error": "",
            "last_candidate_file_count": 0,
            "last_candidate_record_count": 0,
            "last_preview_items": [],
        },
        "log_cleanup": {
            "last_success_at": None,
            "last_status": "unknown",
            "last_error": "",
            "last_started_at": None,
            "last_finished_at": None,
            "last_removed_file_count": 0,
            "last_removed_total_size_bytes": 0,
            "last_remaining_file_count": 0,
            "last_remaining_total_size_bytes": 0,
            "last_trigger": "",
            "last_target_path": str(LOG_ROOT_DIR),
        },
        "restore_drill": {
            "last_success_at": None,
            "last_status": "unknown",
            "last_error": "",
            "last_started_at": None,
            "last_finished_at": None,
            "last_db_snapshot_label": "",
            "last_file_snapshot_label": "",
            "last_restored_job_count": 0,
            "last_restored_uploaded_file_row_count": 0,
            "last_restored_record_count": 0,
            "last_restored_task_run_count": 0,
            "last_restored_storage_file_count": 0,
            "last_trigger": "",
        },
    },
    "integration_hub": {
        "default_provider": "copaw",
        "providers": {
            "copaw": {
                "provider_type": "copaw",
                "enabled": True,
                "ingest_mode": "pull",
                "result_mode": "pull",
                "mapping": {},
                "transport": {
                    "mode": "mock",
                    "submit_url": "",
                    "result_url": "",
                    "callback_url": "",
                    "timeout_seconds": 30,
                },
                "auth": {},
                "signature": {
                    "enabled": False,
                    "algorithm": "hmac_sha256",
                    "secret": "",
                    "header": "X-Signature",
                    "timestamp_header": "X-Timestamp",
                },
            }
        },
        "auth_clients": {
            "demo-client": {
                "enabled": False,
                "token": "change-me",
                "providers": ["copaw"],
                "allow_doc_types": ["order", "shipment", "payment_notice", "invoice"],
            }
        },
        "policies": {
            "idempotency_key": "request_id",
            "allow_push_callback": False,
            "max_files_per_job": 20,
        },
    },
    "lobster_connector": {
        "mode": "mock",
        "endpoint": "",
        "api_key": "",
        "timeout_seconds": 30,
    },
}

_ORDER_MAPPING_REQUIRED_KEYS: dict[str, list[str]] = {
    "customer_order_no": ["客户订单号", "订单号", "order_no", "单据编号", "单据号", "订单编号", "单号"],
    "entry_line_no": ["分录行号", "行号", "明细行号", "分录号", "entry_line_no"],
    "item_code": ["料号", "物料编码", "商品编码", "item_code"],
    "item_name": ["品名", "物料名称", "商品名称", "item_name"],
    "amount": ["价税合计", "金额", "amount"],
    "order_total_amount": ["成交金额", "订单金额", "订单总金额", "order_total_amount", "order_amount"],
    "tax_inclusive_unit_price": ["含税单价", "tax_inclusive_unit_price", "unit_price_with_tax"],
    "biz_date": ["日期", "订单日期", "单据日期", "date"],
    "due_date": ["交期", "交货日期", "due_date", "预计交货日期", "预计交期"],
    "order_closed": ["关闭状态", "订单关闭状态", "关闭订单", "是否关闭", "closed"],
    "line_closed": ["行关闭状态", "明细关闭状态", "分录关闭状态", "是否行关闭"],
    "latest_outbound_date": ["最近出库日期", "最新出库日期", "最后出库日期", "最近发货日期"],
    "order_outbound_status": ["出库状态", "整单出库状态", "订单出库状态", "order_outbound_status"],
    "line_outbound_status": ["行出库状态", "分录出库状态", "明细出库状态", "line_outbound_status"],
    "executed_shipped_qty": ["行已执行已出库数量", "已执行已出库数量", "累计出库数量", "已出库数量"],
    "invoiced_qty": ["行已开票数量", "已开票数量", "累计开票数量"],
    "uninvoiced_qty": ["行未开票数量", "未开票数量", "待开票数量"],
    "line_invoice_status": ["行开票状态", "开票状态", "发票状态"],
}
_ORDER_MAPPING_DEPRECATED_ALIASES: dict[str, list[str]] = {
    "amount": ["成交金额", "订单金额", "订单总金额", "order_total_amount", "order_amount"],
}
_ORDER_MAPPING_REQUIRED_ALIAS_ORDER_FIELDS = {"amount", "order_total_amount"}


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_lifecycle_columns()
    with SessionLocal() as db:
        for key, value in DEFAULTS.items():
            item = db.get(ConfigEntry, key)
            if not item:
                db.add(ConfigEntry(key=key, value_json=value))
        db.commit()
        _ensure_order_mapping_keys(db)
        _ensure_rule_parameter_defaults(db)
        _ensure_retention_policy_defaults(db)
        _ensure_operations_defaults(db)
        _backfill_lifecycle_defaults(db)


def _norm_alias(value: str) -> str:
    return str(value).strip().lower().replace(" ", "").replace("_", "")


def _ensure_order_mapping_keys(db: Session) -> None:
    item = db.get(ConfigEntry, "field_mappings")
    if not item:
        return
    current = item.value_json if isinstance(item.value_json, dict) else {}
    mappings = dict(current)
    order_map_raw = mappings.get("order")
    order_map: dict[str, list[str]] = dict(order_map_raw) if isinstance(order_map_raw, dict) else {}

    changed = False
    for field, required_aliases in _ORDER_MAPPING_REQUIRED_KEYS.items():
        existing = order_map.get(field)
        if not isinstance(existing, list):
            order_map[field] = list(required_aliases)
            changed = True
            continue
        deprecated = {_norm_alias(x) for x in _ORDER_MAPPING_DEPRECATED_ALIASES.get(field, [])}
        merged = [alias for alias in existing if _norm_alias(alias) not in deprecated]
        if len(merged) != len(existing):
            changed = True
        normalized_existing = {_norm_alias(x) for x in merged if str(x).strip()}
        if field in _ORDER_MAPPING_REQUIRED_ALIAS_ORDER_FIELDS:
            prioritized = list(required_aliases)
            normalized_prioritized = {_norm_alias(x) for x in prioritized if str(x).strip()}
            prioritized.extend(alias for alias in merged if _norm_alias(alias) not in normalized_prioritized)
            if prioritized != existing:
                changed = True
            merged = prioritized
        else:
            for alias in required_aliases:
                if _norm_alias(alias) not in normalized_existing:
                    merged.append(alias)
                    normalized_existing.add(_norm_alias(alias))
                    changed = True
        order_map[field] = merged

    if not changed:
        return

    mappings["order"] = order_map
    item.value_json = mappings
    db.commit()


def _ensure_rule_parameter_defaults(db: Session) -> None:
    item = db.get(ConfigEntry, "rule_parameters")
    if not item:
        return

    current = dict(item.value_json) if isinstance(item.value_json, dict) else {}
    if not isinstance(current, dict):
        return

    changed = False

    default_enabled = DEFAULTS["rule_parameters"].get("enabled", {})
    enabled = dict(current.get("enabled")) if isinstance(current.get("enabled"), dict) else {}
    for key, value in default_enabled.items():
        if key not in enabled:
            enabled[key] = value
            changed = True
    current["enabled"] = enabled

    for key, value in DEFAULTS["rule_parameters"].items():
        if key == "enabled":
            continue
        if key not in current:
            current[key] = value
            changed = True

    # Upgrade legacy/test threshold values to the new 60-day business default,
    # while preserving any other explicit user override.
    current_ship_days = current.get("ship_after_no_finance_days")
    if current_ship_days in {0, "0", 7, "7"}:
        current["ship_after_no_finance_days"] = 60
        changed = True

    if changed:
        item.value_json = current
        db.commit()


def _ensure_retention_policy_defaults(db: Session) -> None:
    item = db.get(ConfigEntry, "data_retention_policy")
    if not item:
        return
    current = dict(item.value_json) if isinstance(item.value_json, dict) else {}
    changed = False
    if "archive_recommended_after_days" in current:
        current.pop("archive_recommended_after_days", None)
        changed = True
    for key, value in DEFAULTS["data_retention_policy"].items():
        if key not in current:
            current[key] = value
            changed = True
    if changed:
        item.value_json = current
        db.commit()


def _ensure_operations_defaults(db: Session) -> None:
    for key in ("operations_monitoring_policy", "operations_runtime_status"):
        item = db.get(ConfigEntry, key)
        if not item:
            continue
        current = dict(item.value_json) if isinstance(item.value_json, dict) else {}
        default_value = DEFAULTS[key]
        changed = False
        for child_key, child_default in default_value.items():
            if child_key not in current:
                current[child_key] = child_default
                changed = True
                continue
            if isinstance(child_default, dict) and isinstance(current.get(child_key), dict):
                nested = dict(current[child_key])
                for nested_key, nested_default in child_default.items():
                    if nested_key not in nested:
                        nested[nested_key] = nested_default
                        changed = True
                current[child_key] = nested
        if key == "operations_monitoring_policy":
            if not str(current.get("db_backup_target_path") or "").strip():
                current["db_backup_target_path"] = DEFAULTS[key]["db_backup_target_path"]
                changed = True
            if not str(current.get("file_backup_target_path") or "").strip():
                current["file_backup_target_path"] = DEFAULTS[key]["file_backup_target_path"]
                changed = True
            if current.get("db_backup_enabled") is False and current["db_backup_target_path"] == DEFAULTS[key]["db_backup_target_path"]:
                current["db_backup_enabled"] = DEFAULTS[key]["db_backup_enabled"]
                changed = True
            if current.get("file_backup_enabled") is False and current["file_backup_target_path"] == DEFAULTS[key]["file_backup_target_path"]:
                current["file_backup_enabled"] = DEFAULTS[key]["file_backup_enabled"]
                changed = True
        if changed:
            item.value_json = current
            db.commit()


def _ensure_lifecycle_columns() -> None:
    inspector = inspect(engine)
    try:
        file_cols = {c["name"] for c in inspector.get_columns("uploaded_files")}
        record_cols = {c["name"] for c in inspector.get_columns("normalized_records")}
    except Exception:
        return
    try:
        file_idx_names = {i["name"] for i in inspector.get_indexes("uploaded_files")}
        record_idx_names = {i["name"] for i in inspector.get_indexes("normalized_records")}
    except Exception:
        file_idx_names = set()
        record_idx_names = set()

    ddl: list[str] = []
    if "storage_key" not in file_cols:
        ddl.append("ALTER TABLE uploaded_files ADD COLUMN storage_key VARCHAR(500) NOT NULL DEFAULT ''")
    if "file_size" not in file_cols:
        ddl.append("ALTER TABLE uploaded_files ADD COLUMN file_size INTEGER NOT NULL DEFAULT 0")
    if "file_hash_sha256" not in file_cols:
        ddl.append("ALTER TABLE uploaded_files ADD COLUMN file_hash_sha256 VARCHAR(64) NULL")
    if "lifecycle_state" not in file_cols:
        ddl.append("ALTER TABLE uploaded_files ADD COLUMN lifecycle_state VARCHAR(30) NOT NULL DEFAULT 'active'")
    if "auto_deleted_duplicate_count" not in file_cols:
        ddl.append("ALTER TABLE uploaded_files ADD COLUMN auto_deleted_duplicate_count INTEGER NOT NULL DEFAULT 0")
    if "deleted_at" not in file_cols:
        ddl.append("ALTER TABLE uploaded_files ADD COLUMN deleted_at DATETIME NULL")
    if "deleted_by" not in file_cols:
        ddl.append("ALTER TABLE uploaded_files ADD COLUMN deleted_by VARCHAR(20) NULL")
    if "delete_reason" not in file_cols:
        ddl.append("ALTER TABLE uploaded_files ADD COLUMN delete_reason TEXT NULL")
    if "restored_at" not in file_cols:
        ddl.append("ALTER TABLE uploaded_files ADD COLUMN restored_at DATETIME NULL")
    if "restored_by" not in file_cols:
        ddl.append("ALTER TABLE uploaded_files ADD COLUMN restored_by VARCHAR(20) NULL")
    if "restore_reason" not in file_cols:
        ddl.append("ALTER TABLE uploaded_files ADD COLUMN restore_reason TEXT NULL")
    if "archived_at" not in file_cols:
        ddl.append("ALTER TABLE uploaded_files ADD COLUMN archived_at DATETIME NULL")
    if "archived_by" not in file_cols:
        ddl.append("ALTER TABLE uploaded_files ADD COLUMN archived_by VARCHAR(20) NULL")
    if "archive_reason" not in file_cols:
        ddl.append("ALTER TABLE uploaded_files ADD COLUMN archive_reason TEXT NULL")

    if "lifecycle_state" not in record_cols:
        ddl.append("ALTER TABLE normalized_records ADD COLUMN lifecycle_state VARCHAR(30) NOT NULL DEFAULT 'active'")
    if "order_unshipped_qty" not in record_cols:
        ddl.append("ALTER TABLE normalized_records ADD COLUMN order_unshipped_qty DOUBLE NULL")
    if "version_status" not in record_cols:
        ddl.append("ALTER TABLE normalized_records ADD COLUMN version_status VARCHAR(40) NULL")
    if "is_current_effective" not in record_cols:
        ddl.append("ALTER TABLE normalized_records ADD COLUMN is_current_effective BOOLEAN NOT NULL DEFAULT 1")
    if "duplicate_of_record_id" not in record_cols:
        ddl.append("ALTER TABLE normalized_records ADD COLUMN duplicate_of_record_id VARCHAR(36) NULL")
    if "superseded_by_record_id" not in record_cols:
        ddl.append("ALTER TABLE normalized_records ADD COLUMN superseded_by_record_id VARCHAR(36) NULL")
    if "supersedes_record_id" not in record_cols:
        ddl.append("ALTER TABLE normalized_records ADD COLUMN supersedes_record_id VARCHAR(36) NULL")
    if "deleted_at" not in record_cols:
        ddl.append("ALTER TABLE normalized_records ADD COLUMN deleted_at DATETIME NULL")
    if "deleted_by" not in record_cols:
        ddl.append("ALTER TABLE normalized_records ADD COLUMN deleted_by VARCHAR(20) NULL")
    if "delete_reason" not in record_cols:
        ddl.append("ALTER TABLE normalized_records ADD COLUMN delete_reason TEXT NULL")
    if "delete_origin" not in record_cols:
        ddl.append("ALTER TABLE normalized_records ADD COLUMN delete_origin VARCHAR(30) NULL")
    if "restored_at" not in record_cols:
        ddl.append("ALTER TABLE normalized_records ADD COLUMN restored_at DATETIME NULL")
    if "restored_by" not in record_cols:
        ddl.append("ALTER TABLE normalized_records ADD COLUMN restored_by VARCHAR(20) NULL")
    if "restore_reason" not in record_cols:
        ddl.append("ALTER TABLE normalized_records ADD COLUMN restore_reason TEXT NULL")
    if "archived_at" not in record_cols:
        ddl.append("ALTER TABLE normalized_records ADD COLUMN archived_at DATETIME NULL")
    if "archived_by" not in record_cols:
        ddl.append("ALTER TABLE normalized_records ADD COLUMN archived_by VARCHAR(20) NULL")
    if "archive_reason" not in record_cols:
        ddl.append("ALTER TABLE normalized_records ADD COLUMN archive_reason TEXT NULL")
    if "pre_delete_version_status" not in record_cols:
        ddl.append("ALTER TABLE normalized_records ADD COLUMN pre_delete_version_status VARCHAR(40) NULL")
    if "pre_delete_is_current_effective" not in record_cols:
        ddl.append("ALTER TABLE normalized_records ADD COLUMN pre_delete_is_current_effective BOOLEAN NULL")

    create_hash_index = "ix_uploaded_files_file_hash_sha256" not in file_idx_names
    create_file_lifecycle_index = "ix_uploaded_files_lifecycle_state" not in file_idx_names
    create_record_lifecycle_index = "ix_normalized_records_lifecycle_state" not in record_idx_names
    create_record_version_index = "ix_normalized_records_version_status" not in record_idx_names
    create_record_current_index = "ix_normalized_records_is_current_effective" not in record_idx_names
    create_record_delete_origin_index = "ix_normalized_records_delete_origin" not in record_idx_names

    if (
        not ddl
        and not create_hash_index
        and not create_file_lifecycle_index
        and not create_record_lifecycle_index
        and not create_record_version_index
        and not create_record_current_index
        and not create_record_delete_origin_index
    ):
        return

    with engine.begin() as conn:
        for sql in ddl:
            conn.execute(text(sql))
        if create_hash_index:
            conn.execute(text("CREATE INDEX ix_uploaded_files_file_hash_sha256 ON uploaded_files (file_hash_sha256)"))
        if create_file_lifecycle_index:
            conn.execute(text("CREATE INDEX ix_uploaded_files_lifecycle_state ON uploaded_files (lifecycle_state)"))
        if create_record_lifecycle_index:
            conn.execute(text("CREATE INDEX ix_normalized_records_lifecycle_state ON normalized_records (lifecycle_state)"))
        if create_record_version_index:
            conn.execute(text("CREATE INDEX ix_normalized_records_version_status ON normalized_records (version_status)"))
        if create_record_current_index:
            conn.execute(text("CREATE INDEX ix_normalized_records_is_current_effective ON normalized_records (is_current_effective)"))
        if create_record_delete_origin_index:
            conn.execute(text("CREATE INDEX ix_normalized_records_delete_origin ON normalized_records (delete_origin)"))


def _backfill_lifecycle_defaults(db: Session) -> None:
    record_changed = False
    record_states_by_file: dict[str, set[str]] = {}
    for record in db.query(NormalizedRecord).all():
        payload = record.payload_json if isinstance(record.payload_json, dict) else {}
        governance = payload.get("governance") if isinstance(payload.get("governance"), dict) else {}
        core = payload.get("core") if isinstance(payload.get("core"), dict) else {}
        raw_lifecycle = str(record.lifecycle_state or "").strip().lower()
        governance_version = str(governance.get("version_status") or "").strip().lower()
        desired_lifecycle = LIFECYCLE_ACTIVE
        if record.version_status == STATUS_SPECIAL_CASE or governance_version == STATUS_SPECIAL_CASE:
            desired_lifecycle = LIFECYCLE_SPECIAL_CASE
        elif raw_lifecycle in {LIFECYCLE_ACTIVE, LIFECYCLE_RECYCLE_BIN, LIFECYCLE_ARCHIVED, LIFECYCLE_SPECIAL_CASE}:
            desired_lifecycle = raw_lifecycle
        elif record.archived_at is not None:
            desired_lifecycle = LIFECYCLE_ARCHIVED
        elif record.deleted_at is not None:
            desired_lifecycle = LIFECYCLE_RECYCLE_BIN
        if record.lifecycle_state != desired_lifecycle:
            record.lifecycle_state = desired_lifecycle
            record_changed = True
        record_states_by_file.setdefault(record.file_id, set()).add(desired_lifecycle)

        desired_version_status = record.version_status
        if not desired_version_status:
            desired_version_status = str(governance.get("version_status") or "").strip() or "current"
        if record.version_status != desired_version_status:
            record.version_status = desired_version_status
            record_changed = True

        current_flag = governance.get("is_current_effective")
        desired_current = True if current_flag is None else bool(current_flag)
        if record.is_current_effective != desired_current:
            record.is_current_effective = desired_current
            record_changed = True

        desired_unshipped_qty = None
        if record.document_type == "order":
            desired_unshipped_qty = derive_order_unshipped_qty(
                core.get("quantity"),
                core.get("executed_shipped_qty"),
            )
        if record.order_unshipped_qty != desired_unshipped_qty:
            record.order_unshipped_qty = desired_unshipped_qty
            record_changed = True

        for attr in ("duplicate_of_record_id", "superseded_by_record_id", "supersedes_record_id"):
            desired_value = getattr(record, attr)
            if not desired_value:
                raw = governance.get(attr)
                desired_value = str(raw).strip() if raw is not None and str(raw).strip() else None
            if getattr(record, attr) != desired_value:
                setattr(record, attr, desired_value)
                record_changed = True

    file_changed = False
    for item in db.query(UploadedFile).all():
        desired_state = None
        related_states = record_states_by_file.get(item.id, set())
        if LIFECYCLE_ACTIVE in related_states:
            desired_state = LIFECYCLE_ACTIVE
        elif LIFECYCLE_SPECIAL_CASE in related_states:
            desired_state = LIFECYCLE_SPECIAL_CASE
        elif LIFECYCLE_ARCHIVED in related_states:
            desired_state = LIFECYCLE_ARCHIVED
        elif LIFECYCLE_RECYCLE_BIN in related_states:
            desired_state = LIFECYCLE_RECYCLE_BIN
        else:
            raw_state = str(item.lifecycle_state or "").strip().lower()
            if raw_state in {LIFECYCLE_ACTIVE, LIFECYCLE_RECYCLE_BIN, LIFECYCLE_ARCHIVED, LIFECYCLE_SPECIAL_CASE}:
                desired_state = raw_state
            elif item.archived_at is not None:
                desired_state = LIFECYCLE_ARCHIVED
            elif item.deleted_at is not None:
                desired_state = LIFECYCLE_RECYCLE_BIN
            else:
                desired_state = LIFECYCLE_ACTIVE

        if item.lifecycle_state != desired_state:
            item.lifecycle_state = desired_state
            file_changed = True
        desired_auto_deleted_duplicate_count = int(item.auto_deleted_duplicate_count or 0)
        if item.auto_deleted_duplicate_count != desired_auto_deleted_duplicate_count:
            item.auto_deleted_duplicate_count = desired_auto_deleted_duplicate_count
            file_changed = True

    if file_changed or record_changed:
        db.commit()
