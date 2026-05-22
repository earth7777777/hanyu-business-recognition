from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.init_db import DEFAULTS
from app.db.models import ConfigEntry


def _merge_default_template(current: dict | None, default: dict | None) -> dict:
    merged = dict(default) if isinstance(default, dict) else {}
    data = dict(current) if isinstance(current, dict) else {}
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_default_template(value, merged.get(key))
        else:
            merged[key] = value
    return merged


def _sanitize_config_value(key: str, value: dict | None) -> dict:
    data = dict(value) if isinstance(value, dict) else {}
    if key in {
        "rule_parameters",
        "data_retention_policy",
        "operations_monitoring_policy",
        "operations_runtime_status",
        "uninvoiced_export_sorting",
    }:
        data = _merge_default_template(data, DEFAULTS.get(key))
    if key == "data_retention_policy":
        data.pop("archive_recommended_after_days", None)
    return data


class ConfigService:
    def __init__(self, db: Session):
        self.db = db

    def get(self, key: str) -> dict:
        item = self.db.get(ConfigEntry, key)
        if not item:
            raise KeyError(f"Config '{key}' not found")
        sanitized = _sanitize_config_value(key, item.value_json or {})
        if sanitized != (item.value_json or {}):
            item.value_json = sanitized
            self.db.commit()
            self.db.refresh(item)
        return sanitized

    def set(self, key: str, value: dict) -> dict:
        sanitized = _sanitize_config_value(key, value)
        item = self.db.get(ConfigEntry, key)
        if not item:
            item = ConfigEntry(key=key, value_json=sanitized)
            self.db.add(item)
        else:
            item.value_json = sanitized
        self.db.commit()
        self.db.refresh(item)
        return item.value_json or {}
