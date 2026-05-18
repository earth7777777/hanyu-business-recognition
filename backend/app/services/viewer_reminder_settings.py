from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import ViewerCustomerAlertSetting


VIEWER_ALERT_TYPE_LABELS = {
    "due_before_ship": "该发没发",
    "ship_after_no_finance": "超60天没开票",
}


def normalize_customer_key(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return " ".join(raw.split()).lower()


def ensure_viewer_alert_type(value: str | None) -> str:
    alert_type = str(value or "").strip().lower()
    if alert_type not in VIEWER_ALERT_TYPE_LABELS:
        raise ValueError("提醒类型只支持 due_before_ship 或 ship_after_no_finance。")
    return alert_type


def viewer_alert_type_label(alert_type: str | None) -> str:
    return VIEWER_ALERT_TYPE_LABELS.get(str(alert_type or "").strip().lower(), "提醒")


def load_customer_alert_settings_map(db: Session) -> dict[tuple[str, str], ViewerCustomerAlertSetting]:
    rows = db.query(ViewerCustomerAlertSetting).all()
    return {(row.customer_key, row.alert_type): row for row in rows}


def load_disabled_customer_alert_keys(db: Session) -> set[tuple[str, str]]:
    rows = (
        db.query(ViewerCustomerAlertSetting.customer_key, ViewerCustomerAlertSetting.alert_type)
        .filter(ViewerCustomerAlertSetting.is_enabled.is_(False))
        .all()
    )
    return {(customer_key, alert_type) for customer_key, alert_type in rows if customer_key and alert_type}
