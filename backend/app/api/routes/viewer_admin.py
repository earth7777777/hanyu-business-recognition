from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import db_dep, role_dep
from app.core.security import require_admin
from app.db.models import (
    Alert,
    GroupRecordLink,
    MatchGroup,
    NormalizedRecord,
    ViewerAccount,
    ViewerCustomerAlertSetting,
    ViewerCustomerAlertSettingLog,
)
from app.schemas.viewer import (
    AdminCustomerOverviewAlertItem,
    AdminCustomerOverviewDetail,
    AdminCustomerOverviewItem,
    ViewerAccountCreateBody,
    ViewerAccountItem,
    ViewerAccountPatchBody,
    ViewerAccountResetPasswordBody,
    ViewerReminderSettingChangeBody,
    ViewerReminderSettingItem,
    ViewerReminderSettingLogItem,
    ViewerReminderSettingsPayload,
)
from app.services.viewer_auth import (
    alert_last_changed_at,
    ensure_viewer_role,
    hash_password,
    normalize_phone,
    revoke_account_sessions,
    viewer_public,
)
from app.services.viewer_reminder_settings import (
    ensure_viewer_alert_type,
    load_customer_alert_settings_map,
    normalize_customer_key,
    viewer_alert_type_label,
)
from app.services.uninvoiced_dedupe import (
    dedupe_uninvoiced_entries,
    uninvoiced_amount_from_payload,
    viewer_display_order_no,
)


router = APIRouter(prefix="/admin", tags=["viewer-admin"])
_VIEWER_ADMIN_SEVERITY_LABELS = {
    "high": ("fatal", "致命"),
    "medium": ("important", "重要"),
}
_VIEWER_ADMIN_STATUS_LABELS = {
    "open": "未解除",
    "resolved": "已解除",
}


def _ensure_admin(role: str = Depends(role_dep)) -> str:
    require_admin(role)
    return role


def _customer_from_group(group: MatchGroup | None, payload: dict) -> str:
    if group and isinstance(group.summary_json, dict):
        aggregate = group.summary_json.get("aggregate")
        if isinstance(aggregate, dict):
            text = str(aggregate.get("customer") or "").strip()
            if text:
                return text
    return str(payload.get("customer") or "未知客户").strip() or "未知客户"


def _number_from_text(value: object) -> float | None:
    cleaned = str(value or "").replace(",", "").strip()
    cleaned = "".join(ch for ch in cleaned if ch.isdigit() or ch in {".", "-"})
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _uninvoiced_amount(payload: dict, message: str) -> float | None:
    return uninvoiced_amount_from_payload(payload, message)


def _severity_info(raw: str) -> tuple[str, str]:
    code = str(raw or "").strip().lower()
    return _VIEWER_ADMIN_SEVERITY_LABELS.get(code, ("hint", "提示"))


def _due_overdue_days(payload: dict, message: str) -> int | None:
    days_until_due = _number_from_text(payload.get("days_until_due"))
    if days_until_due is None:
        marker = "交期已过〔"
        if marker in message:
            suffix = message.split(marker, 1)[1]
            parsed = _number_from_text(suffix.split("〕", 1)[0])
            return abs(int(parsed)) if parsed is not None else None
        return None
    return abs(int(days_until_due)) if days_until_due < 0 else None


def _uninvoiced_overdue_days(payload: dict, message: str) -> int | None:
    days_after_outbound = _number_from_text(payload.get("days_after_outbound"))
    if days_after_outbound is None:
        marker = "距最近出库已〔"
        if marker in message:
            suffix = message.split(marker, 1)[1]
            days_after_outbound = _number_from_text(suffix.split("〕", 1)[0])
    if days_after_outbound is None:
        return None
    return max(int(days_after_outbound) - 60, 0)


def _build_customer_overview_data(
    db: Session,
) -> tuple[list[AdminCustomerOverviewItem], dict[str, AdminCustomerOverviewDetail]]:
    rows = (
        db.query(Alert, MatchGroup)
        .join(MatchGroup, Alert.group_id == MatchGroup.id)
        .filter(Alert.status == "open")
        .all()
    )
    if not rows:
        return [], {}

    group_ids = [group.id for _, group in rows]
    fallback_record_map: dict[str, str] = {}
    if group_ids:
        link_rows = (
            db.query(GroupRecordLink.group_id, GroupRecordLink.record_id)
            .filter(GroupRecordLink.group_id.in_(group_ids))
            .order_by(GroupRecordLink.group_id.asc(), GroupRecordLink.id.asc())
            .all()
        )
        for group_id, record_id in link_rows:
            if group_id not in fallback_record_map and record_id:
                fallback_record_map[group_id] = record_id

    record_ids = {
        str((alert.payload_json or {}).get("record_id") or "").strip()
        for alert, _ in rows
        if str((alert.payload_json or {}).get("record_id") or "").strip()
    }
    record_ids.update(fallback_record_map.values())
    record_meta_map: dict[str, dict] = {}
    if record_ids:
        record_rows = (
            db.query(
                NormalizedRecord.id,
                NormalizedRecord.file_id,
                NormalizedRecord.job_id,
                NormalizedRecord.source_row,
            )
            .filter(NormalizedRecord.id.in_(record_ids))
            .all()
        )
        record_meta_map = {
            record_id: {
                "file_id": file_id,
                "job_id": job_id,
                "source_row": source_row,
            }
            for record_id, file_id, job_id, source_row in record_rows
        }

    summary_map: dict[str, dict] = {}
    uninvoiced_entries: list[dict] = []

    for alert, group in rows:
        payload = alert.payload_json if isinstance(alert.payload_json, dict) else {}
        customer_name = _customer_from_group(group, payload)
        customer_key = normalize_customer_key(customer_name)
        if not customer_key:
            continue

        payload_record_id = str(payload.get("record_id") or "").strip()
        record_id = payload_record_id if payload_record_id in record_meta_map else fallback_record_map.get(group.id, "")
        record_meta = record_meta_map.get(record_id, {})
        last_changed_at = alert_last_changed_at(alert)
        severity_code, severity_label = _severity_info(alert.severity)
        known_amount = _uninvoiced_amount(payload, alert.message) if alert.alert_type == "ship_after_no_finance" else None
        days_until_due_raw = _number_from_text(payload.get("days_until_due"))
        days_after_outbound_raw = _number_from_text(payload.get("days_after_outbound"))
        overdue_days = (
            _uninvoiced_overdue_days(payload, alert.message)
            if alert.alert_type == "ship_after_no_finance"
            else _due_overdue_days(payload, alert.message)
        )

        summary = summary_map.setdefault(
            customer_key,
            {
                "customer": customer_name,
                "open_alert_count": 0,
                "open_unshipped_count": 0,
                "open_uninvoiced_count": 0,
                "known_uninvoiced_amount_total": 0.0,
                "has_missing_amount": False,
                "uninvoiced_overdue_max_days": None,
                "unshipped_overdue_max_days": None,
                "job_ids": set(),
                "file_ids": set(),
                "record_ids": set(),
                "latest_changed_at": None,
                "unshipped_items": [],
                "uninvoiced_items": [],
            },
        )
        if alert.job_id:
            summary["job_ids"].add(alert.job_id)
        file_id = str(record_meta.get("file_id") or "").strip()
        if file_id:
            summary["file_ids"].add(file_id)
        if record_id:
            summary["record_ids"].add(record_id)
        if summary["latest_changed_at"] is None or last_changed_at > summary["latest_changed_at"]:
            summary["latest_changed_at"] = last_changed_at

        detail_item = AdminCustomerOverviewAlertItem(
            alert_id=alert.id,
            alert_type=alert.alert_type,
            alert_type_label=viewer_alert_type_label(alert.alert_type),
            severity=severity_code,
            severity_label=severity_label,
            status=alert.status,
            status_label=_VIEWER_ADMIN_STATUS_LABELS.get(alert.status, alert.status),
            message=alert.message,
            customer=customer_name,
            customer_order_no=viewer_display_order_no(payload.get("customer_order_no")),
            item_name=str(payload.get("item_name") or "").strip() or None,
            item_code=str(payload.get("item_code") or "").strip() or None,
            job_id=str(alert.job_id or record_meta.get("job_id") or "").strip() or None,
            file_id=file_id or None,
            record_id=record_id or None,
            source_row=int(payload.get("source_row")) if str(payload.get("source_row") or "").strip().isdigit() else record_meta.get("source_row"),
            created_at=alert.created_at if alert.created_at.tzinfo else alert.created_at.replace(tzinfo=timezone.utc),
            last_changed_at=last_changed_at,
            known_amount=known_amount,
            order_unshipped_qty=_number_from_text(payload.get("order_unshipped_qty")),
            uninvoiced_qty=_number_from_text(payload.get("uninvoiced_qty")),
            days_until_due=int(days_until_due_raw) if days_until_due_raw is not None else None,
            days_after_outbound=int(days_after_outbound_raw) if days_after_outbound_raw is not None else None,
            overdue_days=overdue_days,
        )

        if alert.alert_type == "due_before_ship":
            summary["open_alert_count"] += 1
            summary["open_unshipped_count"] += 1
            if overdue_days is not None:
                current_max = summary["unshipped_overdue_max_days"]
                summary["unshipped_overdue_max_days"] = overdue_days if current_max is None else max(current_max, overdue_days)
            summary["unshipped_items"].append(detail_item)
        elif alert.alert_type == "ship_after_no_finance":
            uninvoiced_entries.append(
                {
                    "alert": alert,
                    "payload": payload,
                    "customer": customer_name,
                    "message": alert.message,
                    "created_at": detail_item.created_at,
                    "last_changed_at": detail_item.last_changed_at,
                    "detail_item": detail_item,
                    "summary": summary,
                }
            )

    for entry in dedupe_uninvoiced_entries(uninvoiced_entries):
        payload = entry["payload"]
        detail_item = entry["detail_item"]
        summary = entry["summary"]
        known_amount = _uninvoiced_amount(payload, detail_item.message)
        overdue_days = _uninvoiced_overdue_days(payload, detail_item.message)
        summary["open_alert_count"] += 1
        summary["open_uninvoiced_count"] += 1
        if known_amount is not None:
            summary["known_uninvoiced_amount_total"] += float(known_amount)
        else:
            summary["has_missing_amount"] = True
        if overdue_days is not None:
            current_max = summary["uninvoiced_overdue_max_days"]
            summary["uninvoiced_overdue_max_days"] = overdue_days if current_max is None else max(current_max, overdue_days)
        summary["uninvoiced_items"].append(detail_item)

    items: list[AdminCustomerOverviewItem] = []
    details: dict[str, AdminCustomerOverviewDetail] = {}
    for customer_key, summary in summary_map.items():
        summary_item = AdminCustomerOverviewItem(
            customer=summary["customer"],
            open_alert_count=int(summary["open_alert_count"]),
            open_unshipped_count=int(summary["open_unshipped_count"]),
            open_uninvoiced_count=int(summary["open_uninvoiced_count"]),
            known_uninvoiced_amount_total=float(summary["known_uninvoiced_amount_total"]),
            has_missing_amount=bool(summary["has_missing_amount"]),
            uninvoiced_overdue_max_days=summary["uninvoiced_overdue_max_days"],
            unshipped_overdue_max_days=summary["unshipped_overdue_max_days"],
            job_count=len(summary["job_ids"]),
            file_count=len(summary["file_ids"]),
            record_count=len(summary["record_ids"]),
            latest_changed_at=summary["latest_changed_at"],
        )
        summary["uninvoiced_items"].sort(
            key=lambda item: (
                -(item.known_amount or -1),
                -(item.overdue_days or 0),
                -item.last_changed_at.timestamp(),
                item.customer_order_no or "",
            )
        )
        summary["unshipped_items"].sort(
            key=lambda item: (
                -(item.overdue_days or -1),
                -(item.order_unshipped_qty or -1),
                -item.last_changed_at.timestamp(),
                item.customer_order_no or "",
            )
        )
        details[customer_key] = AdminCustomerOverviewDetail(
            customer=summary_item.customer,
            summary=summary_item,
            unshipped_items=summary["unshipped_items"],
            uninvoiced_items=summary["uninvoiced_items"],
        )
        items.append(summary_item)

    items.sort(
        key=lambda item: (
            -item.known_uninvoiced_amount_total,
            -item.open_uninvoiced_count,
            -(item.uninvoiced_overdue_max_days or 0),
            -(item.unshipped_overdue_max_days or 0),
            -item.open_unshipped_count,
            -item.open_alert_count,
            -(item.latest_changed_at.timestamp() if item.latest_changed_at else 0),
            item.customer,
        )
    )
    return items, details


def _build_setting_items(db: Session) -> list[ViewerReminderSettingItem]:
    rows = db.query(Alert, MatchGroup).join(MatchGroup, Alert.group_id == MatchGroup.id).all()
    setting_map = load_customer_alert_settings_map(db)
    summary_map: dict[tuple[str, str], dict] = {}
    uninvoiced_entries: list[dict] = []

    for alert, group in rows:
        payload = alert.payload_json if isinstance(alert.payload_json, dict) else {}
        customer_name = _customer_from_group(group, payload)
        customer_key = normalize_customer_key(customer_name)
        if not customer_key:
            continue
        key = (customer_key, alert.alert_type)
        summary = summary_map.setdefault(
            key,
            {
                "customer": customer_name,
                "alert_type": alert.alert_type,
                "open_alert_count": 0,
                "resolved_alert_count": 0,
                "known_amount_total": 0.0,
                "has_missing_amount": False,
            },
        )
        if alert.status == "open":
            if alert.alert_type == "ship_after_no_finance":
                uninvoiced_entries.append(
                    {
                        "alert": alert,
                        "payload": payload,
                        "customer": customer_name,
                        "message": alert.message,
                        "created_at": alert.created_at if alert.created_at.tzinfo else alert.created_at.replace(tzinfo=timezone.utc),
                        "last_changed_at": alert_last_changed_at(alert),
                        "summary": summary,
                    }
                )
            else:
                summary["open_alert_count"] += 1
        else:
            summary["resolved_alert_count"] += 1

    for entry in dedupe_uninvoiced_entries(uninvoiced_entries):
        summary = entry["summary"]
        payload = entry["payload"]
        summary["open_alert_count"] += 1
        amount = _uninvoiced_amount(payload, entry["message"])
        if amount is not None:
            summary["known_amount_total"] += float(amount)
        else:
            summary["has_missing_amount"] = True

    for key, setting in setting_map.items():
        summary_map.setdefault(
            key,
            {
                "customer": setting.customer_name,
                "alert_type": setting.alert_type,
                "open_alert_count": 0,
                "resolved_alert_count": 0,
                "known_amount_total": 0.0,
                "has_missing_amount": False,
            },
        )

    items: list[ViewerReminderSettingItem] = []
    for key, summary in summary_map.items():
        setting = setting_map.get(key)
        items.append(
            ViewerReminderSettingItem(
                customer=summary["customer"],
                alert_type=summary["alert_type"],
                alert_type_label=viewer_alert_type_label(summary["alert_type"]),
                is_enabled=bool(setting.is_enabled) if setting else True,
                last_reason=str(setting.last_reason or "").strip() if setting else "",
                last_operator_name=str(setting.last_operator_name or "").strip() if setting else "",
                last_changed_at=setting.last_changed_at if setting else None,
                open_alert_count=int(summary["open_alert_count"]),
                resolved_alert_count=int(summary["resolved_alert_count"]),
                known_amount_total=float(summary["known_amount_total"]),
                has_missing_amount=bool(summary["has_missing_amount"]),
            )
        )
    items.sort(
        key=lambda item: (
            0 if not item.is_enabled else 1,
            0 if item.alert_type == "ship_after_no_finance" else 1,
            -item.open_alert_count,
            -item.known_amount_total,
            item.customer,
            item.alert_type,
        )
    )
    return items


@router.get("/viewer-accounts", response_model=list[ViewerAccountItem])
def list_viewer_accounts(db: Session = Depends(db_dep), role: str = Depends(_ensure_admin)):
    _ = role
    items = db.query(ViewerAccount).order_by(ViewerAccount.created_at.asc()).all()
    return [ViewerAccountItem(**viewer_public(item)) for item in items]


@router.post("/viewer-accounts", response_model=ViewerAccountItem)
def create_viewer_account(body: ViewerAccountCreateBody, db: Session = Depends(db_dep), role: str = Depends(_ensure_admin)):
    _ = role
    try:
        phone = normalize_phone(body.phone)
        viewer_role = ensure_viewer_role(body.role)
        password_hash = hash_password(body.password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    exists = db.query(ViewerAccount).filter(ViewerAccount.phone == phone).first()
    if exists:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="该手机号已经存在。")

    account = ViewerAccount(
        phone=phone,
        display_name=str(body.display_name or "").strip() or phone,
        role=viewer_role,
        password_hash=password_hash,
        is_active=True,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    return ViewerAccountItem(**viewer_public(account))


@router.patch("/viewer-accounts/{account_id}", response_model=ViewerAccountItem)
def patch_viewer_account(
    account_id: str,
    body: ViewerAccountPatchBody,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    _ = role
    account = db.get(ViewerAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在。")

    if body.phone is not None:
        try:
            phone = normalize_phone(body.phone)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        exists = db.query(ViewerAccount).filter(ViewerAccount.phone == phone, ViewerAccount.id != account.id).first()
        if exists:
            raise HTTPException(status_code=409, detail="该手机号已经存在。")
        account.phone = phone
    if body.display_name is not None:
        account.display_name = str(body.display_name or "").strip() or account.phone
    if body.role is not None:
        try:
            account.role = ensure_viewer_role(body.role)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    if body.is_active is not None:
        account.is_active = bool(body.is_active)
        if not account.is_active:
            revoke_account_sessions(db, account.id, reason="account_disabled")

    db.commit()
    db.refresh(account)
    return ViewerAccountItem(**viewer_public(account))


@router.post("/viewer-accounts/{account_id}/reset-password")
def reset_viewer_password(
    account_id: str,
    body: ViewerAccountResetPasswordBody,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    _ = role
    account = db.get(ViewerAccount, account_id)
    if not account:
        raise HTTPException(status_code=404, detail="账号不存在。")
    try:
        account.password_hash = hash_password(body.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.commit()
    revoke_account_sessions(db, account.id, reason="password_reset")
    return {"ok": True}


@router.get("/viewer-reminder-settings", response_model=ViewerReminderSettingsPayload)
def list_viewer_reminder_settings(db: Session = Depends(db_dep), role: str = Depends(_ensure_admin)):
    _ = role
    logs = (
        db.query(ViewerCustomerAlertSettingLog)
        .order_by(ViewerCustomerAlertSettingLog.created_at.desc())
        .limit(60)
        .all()
    )
    return ViewerReminderSettingsPayload(
        items=_build_setting_items(db),
        logs=[
            ViewerReminderSettingLogItem(
                customer=log.customer_name,
                alert_type=log.alert_type,
                alert_type_label=viewer_alert_type_label(log.alert_type),
                is_enabled=bool(log.is_enabled),
                reason=log.reason,
                operator_name=log.operator_name,
                created_at=log.created_at,
            )
            for log in logs
        ],
    )


@router.put("/viewer-reminder-settings")
def update_viewer_reminder_setting(
    body: ViewerReminderSettingChangeBody,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    _ = role
    try:
        alert_type = ensure_viewer_alert_type(body.alert_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    customer_name = str(body.customer or "").strip()
    customer_key = normalize_customer_key(customer_name)
    reason = str(body.reason or "").strip()
    operator_name = str(body.operator_name or "").strip()
    if not customer_key:
        raise HTTPException(status_code=400, detail="客户名称不能为空。")
    if not reason:
        raise HTTPException(status_code=400, detail="请先填写原因。")
    if not operator_name:
        raise HTTPException(status_code=400, detail="请先填写操作人。")

    setting = (
        db.query(ViewerCustomerAlertSetting)
        .filter(
            ViewerCustomerAlertSetting.customer_key == customer_key,
            ViewerCustomerAlertSetting.alert_type == alert_type,
        )
        .first()
    )
    changed_at = datetime.now(timezone.utc)
    if not setting:
        setting = ViewerCustomerAlertSetting(
            customer_name=customer_name,
            customer_key=customer_key,
            alert_type=alert_type,
            is_enabled=bool(body.enabled),
            last_reason=reason,
            last_operator_name=operator_name,
            last_changed_at=changed_at,
        )
        db.add(setting)
        db.flush()
    else:
        setting.customer_name = customer_name
        setting.is_enabled = bool(body.enabled)
        setting.last_reason = reason
        setting.last_operator_name = operator_name
        setting.last_changed_at = changed_at

    db.add(
        ViewerCustomerAlertSettingLog(
            setting_id=setting.id,
            customer_name=customer_name,
            customer_key=customer_key,
            alert_type=alert_type,
            is_enabled=bool(body.enabled),
            reason=reason,
            operator_name=operator_name,
            created_at=changed_at,
        )
    )
    db.commit()
    return {"ok": True, "changed_at": changed_at}


@router.get("/customer-overview/customers", response_model=list[AdminCustomerOverviewItem])
def list_customer_overview_items(
    keyword: str | None = None,
    limit: int = 20,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    _ = role
    normalized_keyword = str(keyword or "").strip().lower()
    items, _details = _build_customer_overview_data(db)
    if normalized_keyword:
        items = [item for item in items if normalized_keyword in item.customer.lower()]
    safe_limit = max(1, min(int(limit or 20), 100))
    return items[:safe_limit]


@router.get("/customer-overview/detail", response_model=AdminCustomerOverviewDetail)
def get_customer_overview_detail(
    customer: str,
    db: Session = Depends(db_dep),
    role: str = Depends(_ensure_admin),
):
    _ = role
    customer_key = normalize_customer_key(customer)
    if not customer_key:
        raise HTTPException(status_code=400, detail="客户名称不能为空。")
    _items, details = _build_customer_overview_data(db)
    detail = details.get(customer_key)
    if not detail:
        raise HTTPException(status_code=404, detail="当前未找到这个客户的总体情况。")
    return detail
