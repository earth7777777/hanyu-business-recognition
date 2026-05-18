from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import db_dep
from app.db.models import Alert, MatchGroup, NormalizedRecord, ViewerAlertRead
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
    dedupe_uninvoiced_entries,
    uninvoiced_amount_from_payload,
    viewer_display_order_no,
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


def _uninvoiced_amount(payload: dict, message: str) -> float | None:
    return uninvoiced_amount_from_payload(payload, message)


def _uninvoiced_overdue_beyond(payload: dict, message: str) -> int | None:
    days_after = _number_from_text(payload.get("days_after_outbound"))
    if days_after is None:
        marker = "距最近出库已〔"
        if marker in message:
            suffix = message.split(marker, 1)[1]
            days_after = _number_from_text(suffix.split("〕", 1)[0])
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


@router.post("/auth/login")
def viewer_login(body: ViewerLoginBody, request: Request, response: Response, db: Session = Depends(db_dep)):
    account = authenticate_viewer(db, phone=body.phone, password=body.password)
    token = issue_viewer_session(db, account)
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

    for entry in dedupe_uninvoiced_entries(uninvoiced_entries):
        item = entry["item"]
        if normalized_customer and normalized_customer not in item["customer"].lower():
            continue
        local_day = item["last_changed_at"].astimezone(_SH_TZ).date()
        if date_from and local_day < date_from:
            continue
        if date_to and local_day > date_to:
            continue
        items.append(item)

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

    for alert, group in rows:
        if alert.alert_type != "ship_after_no_finance":
            continue
        if _alert_hidden_for_viewer(alert, group, disabled_keys):
            continue
        item = _build_item(alert, group, read_map.get(alert.id))
        customer_name = item["customer"]
        customer_key = normalize_customer_key(customer_name)
        if normalized_customer and normalized_customer not in customer_name.lower():
            continue
        payload = alert.payload_json if isinstance(alert.payload_json, dict) else {}
        overdue_beyond = _uninvoiced_overdue_beyond(payload, item["message"])
        if bucket not in _CUSTOMER_BUCKET_IDS:
            raise HTTPException(status_code=400, detail="时间筛选不支持。")
        if not _matches_bucket(overdue_beyond, bucket):
            continue
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

    for entry in dedupe_uninvoiced_entries(entries):
        item = entry["item"]
        payload = entry["payload"]
        customer_name = item["customer"]
        customer_key = normalize_customer_key(customer_name)
        amount = _uninvoiced_amount(payload, item["message"])
        overdue_beyond = _uninvoiced_overdue_beyond(payload, item["message"])
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
            },
        )
        bucket_item["alert_count"] += 1
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
            known_amount_total=float(data["known_amount_total"]),
            has_missing_amount=bool(data["has_missing_amount"]),
            overdue_max_days=data["overdue_max_days"],
            latest_changed_at=data["latest_changed_at"],
            is_unread_change=bool(data["is_unread_change"]),
            change_label=_merged_change_label(data["change_labels"]),
        )
        for data in grouped.values()
    ]
    items.sort(
        key=lambda item: (
            -item.known_amount_total,
            -(1 if item.has_missing_amount else 0),
            -(item.overdue_max_days or 0),
            -item.latest_changed_at.timestamp(),
            item.customer,
        )
    )
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

    for alert, group in rows:
        if alert.alert_type != "ship_after_no_finance":
            continue
        if _alert_hidden_for_viewer(alert, group, disabled_keys):
            continue
        item = _build_item(alert, group, read_map.get(alert.id))
        if normalize_customer_key(item["customer"]) != customer_key:
            continue
        payload = alert.payload_json if isinstance(alert.payload_json, dict) else {}
        overdue_beyond = _uninvoiced_overdue_beyond(payload, item["message"])
        if bucket not in _CUSTOMER_BUCKET_IDS:
            raise HTTPException(status_code=400, detail="时间筛选不支持。")
        if not _matches_bucket(overdue_beyond, bucket):
            continue
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

    items: list[dict] = []
    for entry in dedupe_uninvoiced_entries(entries):
        item = entry["item"]
        payload = entry["payload"]
        amount = _uninvoiced_amount(payload, item["message"])
        overdue_beyond = _uninvoiced_overdue_beyond(payload, item["message"])
        if amount is not None:
            known_amount_total += float(amount)
        else:
            has_missing_amount = True
        if overdue_beyond is not None:
            overdue_max_days = overdue_beyond if overdue_max_days is None else max(overdue_max_days, overdue_beyond)
        latest_changed_at = item["last_changed_at"] if latest_changed_at is None else max(latest_changed_at, item["last_changed_at"])
        customer_name = item["customer"]
        items.append(item)

    if not items:
        raise HTTPException(status_code=404, detail="当前筛选下没有这个客户的提醒。")

    items.sort(key=_uninvoiced_item_sort_key)
    return ViewerUninvoicedCustomerDetail(
        customer=customer_name,
        alert_type="ship_after_no_finance",
        alert_type_label=_alert_type_label("ship_after_no_finance"),
        status=state,
        status_label=_STATUS_LABELS.get(state, state),
        alert_count=len(items),
        known_amount_total=float(known_amount_total),
        has_missing_amount=has_missing_amount,
        overdue_max_days=overdue_max_days,
        latest_changed_at=latest_changed_at,
        items=[ViewerAlertItem(**item) for item in items],
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
