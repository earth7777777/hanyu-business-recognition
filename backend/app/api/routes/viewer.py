from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.db.models import Alert, ConfigEntry, MatchGroup, NormalizedRecord, ViewerAlertRead
from app.schemas.viewer import (
    ViewerAlertDetail,
    ViewerAlertItem,
    ViewerAlertSourceRow,
    ViewerLoginBody,
    ViewerOverviewItem,
    ViewerProfile,
    ViewerUninvoicedCustomerDetail,
    ViewerUninvoicedCustomerItem,
)
from app.services.normalize_service import parse_date
from app.services.viewer_auth import (
    VIEWER_SESSION_COOKIE,
    alert_last_changed_at,
    authenticate_viewer,
    get_alert_read_map,
    issue_viewer_session,
    revoke_viewer_session,
    viewer_account_dep,
)
from app.services.viewer_reminder_settings import (
    load_disabled_customer_alert_keys,
    normalize_customer_key,
    viewer_alert_type_label,
)
from app.services.uninvoiced_dedupe import (
    actual_uninvoiced_amount_from_payload,
    dedupe_uninvoiced_entries,
    viewer_display_order_no,
)
from app.services.uninvoiced_sorting_config import (
    DEFAULT_UNINVOICED_EXPORT_SORTING,
    UNINVOICED_EXPORT_SORTING_CONFIG_KEY,
)


router = APIRouter(prefix="/viewer", tags=["viewer"])
_SH_TZ = ZoneInfo("Asia/Shanghai")
_SEVERITY_LABELS = {
    "high": ("fatal", "致命"),
    "medium": ("important", "重要"),
}
_STATUS_LABELS = {
    "open": "未解除",
    "resolved": "已解除",
}
_CUSTOMER_BUCKET_IDS = {"all", "week", "twoWeeks", "month", "longer"}
_ORDER_SORT_FULLY_OUTBOUND_DATE_AMOUNT = "fully_outbound_then_order_date_desc_then_urgent_amount_desc"
_ORDER_SORT_FULLY_OUTBOUND_AMOUNT = "fully_outbound_then_urgent_amount_desc"
_CUSTOMER_SORT_AMOUNT_WITH_GROUPS = "amount_desc_with_sort_groups"
_CUSTOMER_SORT_GROUP_PRIORITY = "sort_group_priority_then_amount_desc"
_CUSTOMER_SORT_AMOUNT_ONLY = "amount_desc"


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _severity_info(raw: str) -> tuple[str, str]:
    code = str(raw or "").strip().lower()
    return _SEVERITY_LABELS.get(code, ("hint", "提示"))


def _alert_type_label(raw: str) -> str:
    return viewer_alert_type_label(raw)


def _change_label(alert: Alert, read_item: ViewerAlertRead | None, last_changed_at: datetime) -> tuple[bool, str | None]:
    seen_at = _aware_utc(read_item.last_seen_change_at if read_item else None)
    if seen_at and seen_at >= last_changed_at:
        return False, None

    payload = alert.payload_json if isinstance(alert.payload_json, dict) else {}
    opened_at_raw = str(payload.get("first_opened_at") or payload.get("opened_at") or "").strip()
    opened_at = None
    if opened_at_raw:
        try:
            opened_at = _aware_utc(datetime.fromisoformat(opened_at_raw))
        except ValueError:
            opened_at = None

    if alert.status == "resolved":
        return True, "刚解除"
    if opened_at and abs((last_changed_at - opened_at).total_seconds()) > 1:
        return True, "刚变更"
    return True, "新提醒"


def _customer_from_group(group: MatchGroup | None, payload: dict) -> str:
    if group and isinstance(group.summary_json, dict):
        aggregate = group.summary_json.get("aggregate")
        if isinstance(aggregate, dict):
            text = str(aggregate.get("customer") or "").strip()
            if text:
                return text
    return str(payload.get("customer") or "未知客户").strip() or "未知客户"


def _alert_hidden_for_viewer(alert: Alert, group: MatchGroup | None, disabled_keys: set[tuple[str, str]]) -> bool:
    payload = alert.payload_json if isinstance(alert.payload_json, dict) else {}
    customer = _customer_from_group(group, payload)
    customer_key = normalize_customer_key(customer)
    if not customer_key:
        return False
    return (customer_key, alert.alert_type) in disabled_keys


def _build_item(alert: Alert, group: MatchGroup | None, read_item: ViewerAlertRead | None) -> dict:
    payload = alert.payload_json if isinstance(alert.payload_json, dict) else {}
    severity_code, severity_label = _severity_info(alert.severity)
    last_changed_at = alert_last_changed_at(alert)
    is_unread_change, change_label = _change_label(alert, read_item, last_changed_at)
    actual_uninvoiced_amount = (
        actual_uninvoiced_amount_from_payload(payload, alert.message)
        if alert.alert_type == "ship_after_no_finance"
        else None
    )
    current_days_after_outbound = (
        _current_days_after_outbound(payload, alert.message)
        if alert.alert_type == "ship_after_no_finance"
        else None
    )
    return {
        "id": alert.id,
        "alert_type": alert.alert_type,
        "alert_type_label": _alert_type_label(alert.alert_type),
        "severity": severity_code,
        "severity_label": severity_label,
        "status": alert.status,
        "status_label": _STATUS_LABELS.get(alert.status, alert.status),
        "message": alert.message,
        "customer": _customer_from_group(group, payload),
        "customer_order_no": viewer_display_order_no(payload.get("customer_order_no")),
        "item_name": payload.get("item_name"),
        "item_code": payload.get("item_code"),
        "actual_uninvoiced_amount": actual_uninvoiced_amount,
        "current_days_after_outbound": current_days_after_outbound,
        "created_at": alert.created_at,
        "last_changed_at": last_changed_at,
        "is_unread_change": is_unread_change,
        "change_label": change_label,
    }


def _number_from_text(value: object) -> float | None:
    cleaned = str(value or "").replace(",", "").strip()
    cleaned = "".join(ch for ch in cleaned if ch.isdigit() or ch in {".", "-"})
    if not cleaned:
        return None
    try:
        parsed = float(cleaned)
    except ValueError:
        return None
    return parsed


def _clean_text(value: object, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _to_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _current_days_after_outbound(payload: dict, message: str, record_core: dict | None = None) -> int | None:
    core = record_core if isinstance(record_core, dict) else {}
    latest_outbound_date = _clean_text(core.get("latest_outbound_date") or payload.get("latest_outbound_date"))
    if latest_outbound_date:
        outbound_day = parse_date(latest_outbound_date)
        if outbound_day:
            return (datetime.now(_SH_TZ).date() - outbound_day).days

    days_after = _number_from_text(payload.get("days_after_outbound"))
    if days_after is None:
        marker = "距最近出库已〔"
        if marker in message:
            suffix = message.split(marker, 1)[1]
            days_after = _number_from_text(suffix.split("〕", 1)[0])
    return int(days_after) if days_after is not None else None


def _merge_dict_template(default: dict, current: dict | None) -> dict:
    data = dict(default)
    current_data = dict(current) if isinstance(current, dict) else {}
    for key, value in current_data.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key] = _merge_dict_template(data[key], value)
        else:
            data[key] = value
    return data


def _load_uninvoiced_sorting_config(db: Session) -> dict:
    item = db.get(ConfigEntry, UNINVOICED_EXPORT_SORTING_CONFIG_KEY)
    current = item.value_json if item and isinstance(item.value_json, dict) else {}
    return _merge_dict_template(DEFAULT_UNINVOICED_EXPORT_SORTING, current)


def _configured_sort_groups(config: dict) -> list[dict]:
    raw_groups = config.get("customer_sort_groups")
    if not isinstance(raw_groups, list):
        return []
    groups: list[dict] = []
    for index, raw_group in enumerate(raw_groups, start=1):
        if not isinstance(raw_group, dict):
            continue
        name = _clean_text(raw_group.get("name")) or f"排序组{index}"
        raw_keywords = raw_group.get("keywords")
        if isinstance(raw_keywords, str):
            keywords = [item.strip() for item in raw_keywords.split(",")]
        elif isinstance(raw_keywords, list):
            keywords = [_clean_text(item) for item in raw_keywords]
        else:
            keywords = []
        keywords = [keyword for keyword in keywords if keyword]
        if not keywords:
            continue
        priority = _to_float(raw_group.get("priority"))
        groups.append(
            {
                "name": name,
                "keywords": keywords,
                "priority": int(priority) if priority is not None else index,
            }
        )
    return sorted(groups, key=lambda group: (int(group["priority"]), str(group["name"])))


def _customer_sort_group(customer_name: str, config: dict) -> str:
    text = _clean_text(customer_name)
    for group in _configured_sort_groups(config):
        if any(keyword in text for keyword in group["keywords"]):
            return f"group:{group['priority']}:{group['name']}"
    return f"customer:{normalize_customer_key(text) or text}"


def _customer_priority_key(item: ViewerUninvoicedCustomerItem) -> tuple[float, str]:
    return (-float(item.known_amount_total), item.customer)


def _sort_uninvoiced_customer_items(items: list[ViewerUninvoicedCustomerItem], config: dict) -> None:
    customer_sort = str(config.get("customer_sort") or _CUSTOMER_SORT_AMOUNT_WITH_GROUPS)
    if customer_sort == _CUSTOMER_SORT_AMOUNT_ONLY:
        items.sort(key=_customer_priority_key)
        return

    group_priority: dict[str, tuple[float, str]] = {}
    for item in items:
        group = _customer_sort_group(item.customer, config)
        priority = _customer_priority_key(item)
        if group not in group_priority or priority < group_priority[group]:
            group_priority[group] = priority

    configured_groups = {
        f"group:{group['priority']}:{group['name']}": int(group["priority"])
        for group in _configured_sort_groups(config)
    }

    def group_sort_key(item: ViewerUninvoicedCustomerItem) -> tuple:
        group = _customer_sort_group(item.customer, config)
        if customer_sort == _CUSTOMER_SORT_GROUP_PRIORITY and group in configured_groups:
            return (0, configured_groups[group])
        if customer_sort == _CUSTOMER_SORT_GROUP_PRIORITY:
            return (1, *_customer_priority_key(item))
        return group_priority[group]

    items.sort(key=lambda item: (group_sort_key(item), _customer_priority_key(item)))


def _is_fully_outbound(value: object) -> bool:
    return str(value or "").strip().lower() == "fully_outbound"


def _entry_payload_value(entry: dict, field: str) -> object:
    record_core = entry.get("record_core") if isinstance(entry.get("record_core"), dict) else {}
    value = record_core.get(field)
    if value is not None and value != "":
        return value
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    return payload.get(field)


def _entry_order_date_value(entry: dict) -> date | None:
    return parse_date(_entry_payload_value(entry, "biz_date"))


def _entry_days_after_outbound(entry: dict) -> int | None:
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    record_core = entry.get("record_core") if isinstance(entry.get("record_core"), dict) else {}
    return _current_days_after_outbound(payload, str(entry.get("message") or ""), record_core)


def _entry_uninvoiced_overdue_beyond(entry: dict) -> int | None:
    days_after = _entry_days_after_outbound(entry)
    if days_after is None:
        return None
    return max(int(days_after) - 60, 0)


def _entry_line_sort_key(entry: dict) -> tuple[int, int, str]:
    item = entry.get("item") if isinstance(entry.get("item"), dict) else {}
    entry_value = _to_float(_entry_payload_value(entry, "entry_line_no"))
    source_row = _to_float(entry.get("source_row") or _entry_payload_value(entry, "source_row"))
    product = _clean_text(
        item.get("item_name")
        or _entry_payload_value(entry, "item_name")
        or item.get("item_code")
        or _entry_payload_value(entry, "item_code")
    )
    return (
        int(entry_value) if entry_value is not None else 999999,
        int(source_row or 999999),
        product,
    )


def _sort_uninvoiced_detail_entries(entries: list[dict], config: dict) -> None:
    order_meta: dict[str, dict] = {}
    for entry in entries:
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        order_key = _related_uninvoiced_order_key(entry)
        meta = order_meta.setdefault(
            order_key,
            {
                "fully_outbound_rank": 1,
                "order_date_rank": 0,
                "urgent_amount": 0.0,
                "order_no": order_key,
            },
        )
        if _is_fully_outbound(_entry_payload_value(entry, "order_outbound_status")):
            meta["fully_outbound_rank"] = 0
        order_date = _entry_order_date_value(entry)
        if order_date:
            meta["order_date_rank"] = min(meta["order_date_rank"], -order_date.toordinal())
        amount = _uninvoiced_amount(payload, str(entry.get("message") or ""))
        if amount is not None:
            meta["urgent_amount"] += float(amount)

    order_sort = str(config.get("order_sort") or _ORDER_SORT_FULLY_OUTBOUND_DATE_AMOUNT)

    def order_key(entry: dict) -> tuple:
        meta = order_meta[_related_uninvoiced_order_key(entry)]
        urgent_rank = -float(meta["urgent_amount"])
        if order_sort == _ORDER_SORT_FULLY_OUTBOUND_AMOUNT:
            return (meta["fully_outbound_rank"], urgent_rank, meta["order_no"])
        return (meta["fully_outbound_rank"], meta["order_date_rank"], urgent_rank, meta["order_no"])

    entries.sort(key=lambda entry: (order_key(entry), _entry_line_sort_key(entry)))


def _sort_uninvoiced_entries_for_viewer(db: Session, entries: list[dict], *, state: str) -> list[dict]:
    deduped_entries = list(dedupe_uninvoiced_entries(entries))
    if not deduped_entries:
        return []

    config = _load_uninvoiced_sorting_config(db)
    _attach_order_record_context(db, deduped_entries)

    grouped_entries: dict[str, list[dict]] = {}
    grouped_summary: dict[str, dict] = {}
    for entry in deduped_entries:
        item = entry["item"]
        payload = entry["payload"]
        item["current_days_after_outbound"] = _entry_days_after_outbound(entry)
        customer_name = item["customer"]
        customer_key = normalize_customer_key(customer_name)
        grouped_entries.setdefault(customer_key, []).append(entry)
        summary = grouped_summary.setdefault(
            customer_key,
            {
                "customer": customer_name,
                "alert_count": 0,
                "related_order_keys": set(),
                "known_amount_total": 0.0,
                "has_missing_amount": False,
                "overdue_max_days": None,
                "latest_changed_at": item["last_changed_at"],
                "is_unread_change": False,
                "change_labels": [],
            },
        )
        summary["alert_count"] += 1
        summary["related_order_keys"].add(_related_uninvoiced_order_key(entry))
        amount = _uninvoiced_amount(payload, item["message"])
        overdue_beyond = _entry_uninvoiced_overdue_beyond(entry)
        if amount is not None:
            summary["known_amount_total"] += float(amount)
        else:
            summary["has_missing_amount"] = True
        if overdue_beyond is not None:
            current_max = summary["overdue_max_days"]
            summary["overdue_max_days"] = overdue_beyond if current_max is None else max(current_max, overdue_beyond)
        if item["last_changed_at"] > summary["latest_changed_at"]:
            summary["latest_changed_at"] = item["last_changed_at"]
        if item["is_unread_change"]:
            summary["is_unread_change"] = True
        if item["change_label"]:
            summary["change_labels"].append(item["change_label"])

    customer_items = [
        ViewerUninvoicedCustomerItem(
            customer=data["customer"],
            alert_type="ship_after_no_finance",
            alert_type_label=_alert_type_label("ship_after_no_finance"),
            status=state,
            status_label=_STATUS_LABELS.get(state, state),
            alert_count=data["alert_count"],
            related_order_count=len(data["related_order_keys"]),
            known_amount_total=float(data["known_amount_total"]),
            has_missing_amount=bool(data["has_missing_amount"]),
            overdue_max_days=data["overdue_max_days"],
            latest_changed_at=data["latest_changed_at"],
            is_unread_change=bool(data["is_unread_change"]),
            change_label=_merged_change_label(data["change_labels"]),
        )
        for data in grouped_summary.values()
    ]
    _sort_uninvoiced_customer_items(customer_items, config)

    ordered_entries: list[dict] = []
    for customer_item in customer_items:
        customer_key = normalize_customer_key(customer_item.customer)
        customer_entries = grouped_entries.get(customer_key, [])
        _sort_uninvoiced_detail_entries(customer_entries, config)
        ordered_entries.extend(customer_entries)

    for index, entry in enumerate(ordered_entries):
        entry["item"]["viewer_sort_index"] = index
    return ordered_entries


def _uninvoiced_amount(payload: dict, message: str) -> float | None:
    return actual_uninvoiced_amount_from_payload(payload, message)


def _uninvoiced_overdue_beyond(payload: dict, message: str, record_core: dict | None = None) -> int | None:
    days_after = _current_days_after_outbound(payload, message, record_core)
    if days_after is None:
        return None
    return max(int(days_after) - 60, 0)


def _matches_bucket(value: int | None, bucket: str) -> bool:
    if bucket == "all":
        return True
    if value is None:
        return False
    if bucket == "week":
        return value <= 7
    if bucket == "twoWeeks":
        return 7 < value <= 14
    if bucket == "month":
        return 14 < value <= 30
    if bucket == "longer":
        return value > 30
    return False


def _viewer_item_sort_key(item: dict) -> tuple[float, float, float]:
    severity_rank = {"fatal": 0, "important": 1, "hint": 2}
    return (
        severity_rank.get(item["severity"], 9),
        -item["last_changed_at"].timestamp(),
        -item["created_at"].timestamp(),
    )


def _uninvoiced_item_sort_key(item: dict) -> tuple[float, float, float, str]:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    amount = _uninvoiced_amount(payload, str(item.get("message") or ""))
    overdue_beyond = _uninvoiced_overdue_beyond(payload, str(item.get("message") or ""))
    return (
        -(amount if amount is not None else -1),
        -(overdue_beyond or 0),
        -item["last_changed_at"].timestamp(),
        str(item.get("customer_order_no") or ""),
    )


def _merged_change_label(labels: list[str]) -> str | None:
    if "刚解除" in labels:
        return "刚解除"
    if "刚变更" in labels:
        return "刚变更"
    if "新提醒" in labels:
        return "新提醒"
    return None


def _load_alert_rows(db: Session, *, state: str | None = None) -> list[tuple[Alert, MatchGroup]]:
    query = db.query(Alert, MatchGroup).join(MatchGroup, Alert.group_id == MatchGroup.id)
    if state:
        query = query.filter(Alert.status == state)
    return query.all()


def _build_uninvoiced_entry(alert: Alert, group: MatchGroup | None, read_item: ViewerAlertRead | None) -> dict:
    payload = alert.payload_json if isinstance(alert.payload_json, dict) else {}
    item = _build_item(alert, group, read_item)
    return {
        "alert": alert,
        "group": group,
        "payload": payload,
        "item": item,
        "customer": item["customer"],
        "message": item["message"],
        "created_at": item["created_at"],
        "last_changed_at": item["last_changed_at"],
    }


def _attach_order_record_context(db: Session, entries: list[dict]) -> None:
    record_ids = {
        _clean_text((entry.get("payload") if isinstance(entry.get("payload"), dict) else {}).get("record_id"))
        for entry in entries
    }
    record_ids.discard("")
    if not record_ids:
        return

    records = db.query(NormalizedRecord).filter(NormalizedRecord.id.in_(record_ids)).all()
    records_by_id = {record.id: record for record in records}
    for entry in entries:
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        record = records_by_id.get(_clean_text(payload.get("record_id")))
        if not record:
            continue
        payload_json = record.payload_json if isinstance(record.payload_json, dict) else {}
        core = payload_json.get("core") if isinstance(payload_json.get("core"), dict) else {}
        entry["record_core"] = core
        entry["source_row"] = record.source_row


def _related_uninvoiced_order_key(entry: dict) -> str:
    payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
    item = entry.get("item") if isinstance(entry.get("item"), dict) else {}
    raw_order_no = str(payload.get("customer_order_no") or item.get("customer_order_no") or "").strip()
    order_no = viewer_display_order_no(raw_order_no) or raw_order_no or "未知单据"
    return order_no.lower()


@router.post("/auth/login")
def viewer_login(body: ViewerLoginBody, request: Request, response: Response, db: Session = Depends(db_dep)):
    account = authenticate_viewer(db, phone=body.phone, password=body.password)
    token = issue_viewer_session(db, account, device_info=body.device, request=request)
    response.set_cookie(
        key=VIEWER_SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=365 * 24 * 60 * 60,
        path="/",
    )
    return {
        "ok": True,
        "account": ViewerProfile(
            id=account.id,
            phone=account.phone,
            display_name=account.display_name,
            role=account.role,
        )
    }


@router.post("/auth/logout")
def viewer_logout(
    response: Response,
    db: Session = Depends(db_dep),
    account=Depends(viewer_account_dep),
    viewer_session: str | None = Cookie(default=None, alias=VIEWER_SESSION_COOKIE),
):
    _ = account
    revoke_viewer_session(db, viewer_session, reason="user_logout")
    response.delete_cookie(VIEWER_SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me", response_model=ViewerProfile)
def viewer_me(account=Depends(viewer_account_dep)):
    return ViewerProfile(
        id=account.id,
        phone=account.phone,
        display_name=account.display_name,
        role=account.role,
    )


@router.get("/overview", response_model=ViewerOverviewItem)
def viewer_overview(account=Depends(viewer_account_dep), db: Session = Depends(db_dep)):
    _ = account
    rows = _load_alert_rows(db)
    disabled_keys = load_disabled_customer_alert_keys(db)
    today_local = datetime.now(_SH_TZ).date()
    today_new_count = 0
    today_resolved_count = 0
    open_unshipped_count = 0
    open_uninvoiced_count = 0

    uninvoiced_entries: list[dict] = []
    for alert, group in rows:
        if _alert_hidden_for_viewer(alert, group, disabled_keys):
            continue
        if alert.alert_type == "ship_after_no_finance":
            uninvoiced_entries.append(_build_uninvoiced_entry(alert, group, None))
            continue

        last_changed = alert_last_changed_at(alert).astimezone(_SH_TZ)
        if last_changed.date() == today_local:
            if alert.status == "resolved":
                today_resolved_count += 1
            else:
                today_new_count += 1
        if alert.status == "open" and alert.alert_type == "due_before_ship":
            open_unshipped_count += 1

    for entry in dedupe_uninvoiced_entries(uninvoiced_entries):
        alert = entry["alert"]
        last_changed = entry["last_changed_at"].astimezone(_SH_TZ)
        if last_changed.date() == today_local:
            if alert.status == "resolved":
                today_resolved_count += 1
            else:
                today_new_count += 1
        if alert.status == "open":
            open_uninvoiced_count += 1

    return ViewerOverviewItem(
        today_new_count=today_new_count,
        today_resolved_count=today_resolved_count,
        open_unshipped_count=open_unshipped_count,
        open_uninvoiced_count=open_uninvoiced_count,
    )


@router.get("/alerts", response_model=list[ViewerAlertItem])
def viewer_alerts(
    tab: str = Query(default="all", pattern="^(all|unshipped|uninvoiced)$"),
    state: str = Query(default="open", pattern="^(open|resolved)$"),
    customer: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    account=Depends(viewer_account_dep),
    db: Session = Depends(db_dep),
):
    rows = _load_alert_rows(db, state=state)
    read_map = get_alert_read_map(db, account_id=account.id)
    disabled_keys = load_disabled_customer_alert_keys(db)
    normalized_customer = str(customer or "").strip().lower()
    items: list[dict] = []
    uninvoiced_entries: list[dict] = []
    for alert, group in rows:
        if _alert_hidden_for_viewer(alert, group, disabled_keys):
            continue
        item = _build_item(alert, group, read_map.get(alert.id))
        if tab == "unshipped":
            if alert.alert_type != "due_before_ship":
                continue
        elif tab == "uninvoiced":
            if alert.alert_type != "ship_after_no_finance":
                continue
        if alert.alert_type == "ship_after_no_finance":
            uninvoiced_entries.append(
                {
                    "alert": alert,
                    "group": group,
                    "payload": alert.payload_json if isinstance(alert.payload_json, dict) else {},
                    "item": item,
                    "customer": item["customer"],
                    "message": item["message"],
                    "created_at": item["created_at"],
                    "last_changed_at": item["last_changed_at"],
                }
            )
            continue
        if normalized_customer and normalized_customer not in item["customer"].lower():
            continue
        local_day = item["last_changed_at"].astimezone(_SH_TZ).date()
        if date_from and local_day < date_from:
            continue
        if date_to and local_day > date_to:
            continue
        items.append(item)

    for entry in _sort_uninvoiced_entries_for_viewer(db, uninvoiced_entries, state=state):
        item = entry["item"]
        if normalized_customer and normalized_customer not in item["customer"].lower():
            continue
        local_day = item["last_changed_at"].astimezone(_SH_TZ).date()
        if date_from and local_day < date_from:
            continue
        if date_to and local_day > date_to:
            continue
        items.append(item)

    if tab == "uninvoiced":
        items.sort(
            key=lambda item: (
                item.get("viewer_sort_index") if item.get("viewer_sort_index") is not None else 999999,
                _viewer_item_sort_key(item),
            )
        )
    else:
        items.sort(key=_viewer_item_sort_key)
    return [ViewerAlertItem(**item) for item in items]


@router.get("/uninvoiced/customers", response_model=list[ViewerUninvoicedCustomerItem])
def viewer_uninvoiced_customers(
    state: str = Query(default="open", pattern="^(open|resolved)$"),
    customer: str | None = Query(default=None),
    bucket: str = Query(default="all", pattern="^(all|week|twoWeeks|month|longer)$"),
    account=Depends(viewer_account_dep),
    db: Session = Depends(db_dep),
):
    _ = account
    rows = _load_alert_rows(db, state=state)
    read_map = get_alert_read_map(db, account_id=account.id)
    disabled_keys = load_disabled_customer_alert_keys(db)
    normalized_customer = str(customer or "").strip().lower()
    grouped: dict[str, dict] = {}
    entries: list[dict] = []
    if bucket not in _CUSTOMER_BUCKET_IDS:
        raise HTTPException(status_code=400, detail="时间筛选不支持。")

    for alert, group in rows:
        if alert.alert_type != "ship_after_no_finance":
            continue
        if _alert_hidden_for_viewer(alert, group, disabled_keys):
            continue
        item = _build_item(alert, group, read_map.get(alert.id))
        customer_name = item["customer"]
        if normalized_customer and normalized_customer not in customer_name.lower():
            continue
        payload = alert.payload_json if isinstance(alert.payload_json, dict) else {}
        entries.append(
            {
                "alert": alert,
                "group": group,
                "payload": payload,
                "item": item,
                "customer": customer_name,
                "message": item["message"],
                "created_at": item["created_at"],
                "last_changed_at": item["last_changed_at"],
            }
        )

    deduped_entries = list(dedupe_uninvoiced_entries(entries))
    _attach_order_record_context(db, deduped_entries)
    for entry in deduped_entries:
        item = entry["item"]
        payload = entry["payload"]
        item["current_days_after_outbound"] = _entry_days_after_outbound(entry)
        overdue_beyond = _entry_uninvoiced_overdue_beyond(entry)
        if not _matches_bucket(overdue_beyond, bucket):
            continue
        customer_name = item["customer"]
        customer_key = normalize_customer_key(customer_name)
        amount = _uninvoiced_amount(payload, item["message"])
        bucket_item = grouped.setdefault(
            customer_key,
            {
                "customer": customer_name,
                "alert_count": 0,
                "known_amount_total": 0.0,
                "has_missing_amount": False,
                "overdue_max_days": None,
                "latest_changed_at": item["last_changed_at"],
                "change_labels": [],
                "is_unread_change": False,
                "related_order_keys": set(),
            },
        )
        bucket_item["alert_count"] += 1
        bucket_item["related_order_keys"].add(_related_uninvoiced_order_key(entry))
        if amount is not None:
            bucket_item["known_amount_total"] += float(amount)
        else:
            bucket_item["has_missing_amount"] = True
        if overdue_beyond is not None:
            current_max = bucket_item["overdue_max_days"]
            bucket_item["overdue_max_days"] = overdue_beyond if current_max is None else max(current_max, overdue_beyond)
        if item["last_changed_at"] > bucket_item["latest_changed_at"]:
            bucket_item["latest_changed_at"] = item["last_changed_at"]
        if item["change_label"]:
            bucket_item["change_labels"].append(item["change_label"])
        if item["is_unread_change"]:
            bucket_item["is_unread_change"] = True

    items = [
        ViewerUninvoicedCustomerItem(
            customer=data["customer"],
            alert_type="ship_after_no_finance",
            alert_type_label=_alert_type_label("ship_after_no_finance"),
            status=state,
            status_label=_STATUS_LABELS.get(state, state),
            alert_count=data["alert_count"],
            related_order_count=len(data["related_order_keys"]),
            known_amount_total=float(data["known_amount_total"]),
            has_missing_amount=bool(data["has_missing_amount"]),
            overdue_max_days=data["overdue_max_days"],
            latest_changed_at=data["latest_changed_at"],
            is_unread_change=bool(data["is_unread_change"]),
            change_label=_merged_change_label(data["change_labels"]),
        )
        for data in grouped.values()
    ]
    _sort_uninvoiced_customer_items(items, _load_uninvoiced_sorting_config(db))
    return items


@router.get("/uninvoiced/customer-detail", response_model=ViewerUninvoicedCustomerDetail)
def viewer_uninvoiced_customer_detail(
    customer: str = Query(..., min_length=1),
    state: str = Query(default="open", pattern="^(open|resolved)$"),
    bucket: str = Query(default="all", pattern="^(all|week|twoWeeks|month|longer)$"),
    account=Depends(viewer_account_dep),
    db: Session = Depends(db_dep),
):
    customer_key = normalize_customer_key(customer)
    if not customer_key:
        raise HTTPException(status_code=400, detail="客户名称不能为空。")

    rows = _load_alert_rows(db, state=state)
    read_map = get_alert_read_map(db, account_id=account.id)
    disabled_keys = load_disabled_customer_alert_keys(db)
    entries: list[dict] = []
    known_amount_total = 0.0
    has_missing_amount = False
    overdue_max_days: int | None = None
    latest_changed_at: datetime | None = None
    customer_name = customer.strip()
    related_order_keys: set[str] = set()
    if bucket not in _CUSTOMER_BUCKET_IDS:
        raise HTTPException(status_code=400, detail="时间筛选不支持。")

    for alert, group in rows:
        if alert.alert_type != "ship_after_no_finance":
            continue
        if _alert_hidden_for_viewer(alert, group, disabled_keys):
            continue
        item = _build_item(alert, group, read_map.get(alert.id))
        if normalize_customer_key(item["customer"]) != customer_key:
            continue
        payload = alert.payload_json if isinstance(alert.payload_json, dict) else {}
        entries.append(
            {
                "alert": alert,
                "group": group,
                "payload": payload,
                "item": item,
                "customer": item["customer"],
                "message": item["message"],
                "created_at": item["created_at"],
                "last_changed_at": item["last_changed_at"],
            }
        )

    detail_entries: list[dict] = []
    deduped_entries = list(dedupe_uninvoiced_entries(entries))
    _attach_order_record_context(db, deduped_entries)
    for entry in deduped_entries:
        item = entry["item"]
        payload = entry["payload"]
        item["current_days_after_outbound"] = _entry_days_after_outbound(entry)
        overdue_beyond = _entry_uninvoiced_overdue_beyond(entry)
        if not _matches_bucket(overdue_beyond, bucket):
            continue
        amount = _uninvoiced_amount(payload, item["message"])
        if amount is not None:
            known_amount_total += float(amount)
        else:
            has_missing_amount = True
        if overdue_beyond is not None:
            overdue_max_days = overdue_beyond if overdue_max_days is None else max(overdue_max_days, overdue_beyond)
        latest_changed_at = item["last_changed_at"] if latest_changed_at is None else max(latest_changed_at, item["last_changed_at"])
        customer_name = item["customer"]
        related_order_keys.add(_related_uninvoiced_order_key(entry))
        detail_entries.append(entry)

    if not detail_entries:
        raise HTTPException(status_code=404, detail="当前筛选下没有这个客户的提醒。")

    _attach_order_record_context(db, detail_entries)
    _sort_uninvoiced_detail_entries(detail_entries, _load_uninvoiced_sorting_config(db))
    return ViewerUninvoicedCustomerDetail(
        customer=customer_name,
        alert_type="ship_after_no_finance",
        alert_type_label=_alert_type_label("ship_after_no_finance"),
        status=state,
        status_label=_STATUS_LABELS.get(state, state),
        alert_count=len(detail_entries),
        related_order_count=len(related_order_keys),
        known_amount_total=float(known_amount_total),
        has_missing_amount=has_missing_amount,
        overdue_max_days=overdue_max_days,
        latest_changed_at=latest_changed_at,
        items=[ViewerAlertItem(**entry["item"]) for entry in detail_entries],
    )


@router.get("/alerts/{alert_id}", response_model=ViewerAlertDetail)
def viewer_alert_detail(alert_id: str, account=Depends(viewer_account_dep), db: Session = Depends(db_dep)):
    alert = db.get(Alert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="提醒不存在。")
    group = db.get(MatchGroup, alert.group_id)
    if _alert_hidden_for_viewer(alert, group, load_disabled_customer_alert_keys(db)):
        raise HTTPException(status_code=404, detail="提醒不存在。")
    read_map = get_alert_read_map(db, account_id=account.id)
    item = _build_item(alert, group, read_map.get(alert.id))
    payload = alert.payload_json if isinstance(alert.payload_json, dict) else {}
    if alert.alert_type == "ship_after_no_finance":
        record_id = _clean_text(payload.get("record_id"))
        record = db.get(NormalizedRecord, record_id) if record_id else None
        payload_json = record.payload_json if record and isinstance(record.payload_json, dict) else {}
        record_core = payload_json.get("core") if isinstance(payload_json.get("core"), dict) else {}
        item["current_days_after_outbound"] = _current_days_after_outbound(payload, item["message"], record_core)
    return ViewerAlertDetail(
        **item,
        group_id=alert.group_id,
        payload=payload,
        message_long=str(payload.get("message_long") or "").strip() or None,
        has_source_row=bool(str(payload.get("record_id") or "").strip()),
    )


@router.get("/alerts/{alert_id}/source-row", response_model=ViewerAlertSourceRow)
def viewer_alert_source_row(alert_id: str, account=Depends(viewer_account_dep), db: Session = Depends(db_dep)):
    _ = account
    alert = db.get(Alert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="提醒不存在。")
    group = db.get(MatchGroup, alert.group_id)
    if _alert_hidden_for_viewer(alert, group, load_disabled_customer_alert_keys(db)):
        raise HTTPException(status_code=404, detail="提醒不存在。")
    payload = alert.payload_json if isinstance(alert.payload_json, dict) else {}
    record_id = str(payload.get("record_id") or "").strip()
    if not record_id:
        raise HTTPException(status_code=404, detail="这条提醒还没有可展示的原始来源。")
    record = db.get(NormalizedRecord, record_id)
    if not record:
        raise HTTPException(status_code=404, detail="原始来源记录不存在。")
    record_payload = record.payload_json if isinstance(record.payload_json, dict) else {}
    core = record_payload.get("core") if isinstance(record_payload.get("core"), dict) else {}
    ext = record_payload.get("ext") if isinstance(record_payload.get("ext"), dict) else {}
    return ViewerAlertSourceRow(
        alert_id=alert.id,
        record_id=record.id,
        file_id=record.file_id,
        filename=getattr(record.file, "filename", None),
        source_row=record.source_row,
        document_type=record.document_type,
        core=core,
        ext=ext,
    )


@router.post("/alerts/{alert_id}/read")
def viewer_alert_read(alert_id: str, account=Depends(viewer_account_dep), db: Session = Depends(db_dep)):
    alert = db.get(Alert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="提醒不存在。")
    group = db.get(MatchGroup, alert.group_id)
    if _alert_hidden_for_viewer(alert, group, load_disabled_customer_alert_keys(db)):
        raise HTTPException(status_code=404, detail="提醒不存在。")
    last_changed_at = alert_last_changed_at(alert)
    item = (
        db.query(ViewerAlertRead)
        .filter(ViewerAlertRead.account_id == account.id, ViewerAlertRead.alert_id == alert.id)
        .first()
    )
    if not item:
        item = ViewerAlertRead(account_id=account.id, alert_id=alert.id, last_seen_change_at=last_changed_at)
        db.add(item)
    else:
        item.last_seen_change_at = last_changed_at
    db.commit()
    return {"ok": True, "last_seen_change_at": last_changed_at}
