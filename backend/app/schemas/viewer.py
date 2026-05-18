from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ViewerLoginBody(BaseModel):
    phone: str
    password: str


class ViewerAccountCreateBody(BaseModel):
    phone: str
    display_name: str
    role: str
    password: str


class ViewerAccountPatchBody(BaseModel):
    phone: str | None = None
    display_name: str | None = None
    role: str | None = None
    is_active: bool | None = None


class ViewerAccountResetPasswordBody(BaseModel):
    password: str


class ViewerReminderSettingChangeBody(BaseModel):
    customer: str
    alert_type: str
    enabled: bool
    reason: str
    operator_name: str


class ViewerAccountItem(BaseModel):
    id: str
    phone: str
    display_name: str
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    last_login_at: datetime | None = None


class ViewerProfile(BaseModel):
    id: str
    phone: str
    display_name: str
    role: str


class ViewerOverviewItem(BaseModel):
    today_new_count: int
    today_resolved_count: int
    open_unshipped_count: int
    open_uninvoiced_count: int


class ViewerAlertItem(BaseModel):
    id: str
    alert_type: str
    alert_type_label: str
    severity: str
    severity_label: str
    status: str
    status_label: str
    message: str
    customer: str
    customer_order_no: str | None = None
    item_name: str | None = None
    item_code: str | None = None
    created_at: datetime
    last_changed_at: datetime
    is_unread_change: bool
    change_label: str | None = None


class ViewerAlertDetail(ViewerAlertItem):
    group_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    message_long: str | None = None
    has_source_row: bool = False


class ViewerAlertSourceRow(BaseModel):
    alert_id: str
    record_id: str
    file_id: str
    filename: str | None = None
    source_row: int | None = None
    document_type: str
    core: dict[str, Any] = Field(default_factory=dict)
    ext: dict[str, Any] = Field(default_factory=dict)


class ViewerUninvoicedCustomerItem(BaseModel):
    customer: str
    alert_type: str
    alert_type_label: str
    status: str
    status_label: str
    alert_count: int
    known_amount_total: float
    has_missing_amount: bool
    overdue_max_days: int | None = None
    latest_changed_at: datetime
    is_unread_change: bool
    change_label: str | None = None


class ViewerUninvoicedCustomerDetail(BaseModel):
    customer: str
    alert_type: str
    alert_type_label: str
    status: str
    status_label: str
    alert_count: int
    known_amount_total: float
    has_missing_amount: bool
    overdue_max_days: int | None = None
    latest_changed_at: datetime | None = None
    items: list[ViewerAlertItem] = Field(default_factory=list)


class ViewerReminderSettingItem(BaseModel):
    customer: str
    alert_type: str
    alert_type_label: str
    is_enabled: bool
    last_reason: str
    last_operator_name: str
    last_changed_at: datetime | None = None
    open_alert_count: int
    resolved_alert_count: int
    known_amount_total: float
    has_missing_amount: bool


class ViewerReminderSettingLogItem(BaseModel):
    customer: str
    alert_type: str
    alert_type_label: str
    is_enabled: bool
    reason: str
    operator_name: str
    created_at: datetime


class ViewerReminderSettingsPayload(BaseModel):
    items: list[ViewerReminderSettingItem] = Field(default_factory=list)
    logs: list[ViewerReminderSettingLogItem] = Field(default_factory=list)


class AdminCustomerOverviewItem(BaseModel):
    customer: str
    open_alert_count: int
    open_unshipped_count: int
    open_uninvoiced_count: int
    known_uninvoiced_amount_total: float
    has_missing_amount: bool
    uninvoiced_overdue_max_days: int | None = None
    unshipped_overdue_max_days: int | None = None
    job_count: int
    file_count: int
    record_count: int
    latest_changed_at: datetime | None = None


class AdminCustomerOverviewAlertItem(BaseModel):
    alert_id: str
    alert_type: str
    alert_type_label: str
    severity: str
    severity_label: str
    status: str
    status_label: str
    message: str
    customer: str
    customer_order_no: str | None = None
    item_name: str | None = None
    item_code: str | None = None
    job_id: str | None = None
    file_id: str | None = None
    record_id: str | None = None
    source_row: int | None = None
    created_at: datetime
    last_changed_at: datetime
    known_amount: float | None = None
    order_unshipped_qty: float | None = None
    uninvoiced_qty: float | None = None
    days_until_due: int | None = None
    days_after_outbound: int | None = None
    overdue_days: int | None = None


class AdminCustomerOverviewDetail(BaseModel):
    customer: str
    summary: AdminCustomerOverviewItem
    unshipped_items: list[AdminCustomerOverviewAlertItem] = Field(default_factory=list)
    uninvoiced_items: list[AdminCustomerOverviewAlertItem] = Field(default_factory=list)
