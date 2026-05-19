from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.models import Alert, MatchGroup, NormalizedRecord
from app.services.alert_engine import RULE_SHIP_AFTER_NO_FINANCE
from app.services.uninvoiced_dedupe import (
    dedupe_uninvoiced_entries,
    uninvoiced_amount_from_payload,
    viewer_display_order_no,
)
from app.services.normalize_service import parse_date
from app.services.viewer_reminder_settings import load_disabled_customer_alert_keys, normalize_customer_key


_SH_TZ = ZoneInfo("Asia/Shanghai")
_HTML_TITLE = "超60天没开票"
_SHIP_DAYS = 60


@dataclass
class _ExportLine:
    record_id: str
    source_row: int | None
    entry_line_no: str
    customer: str
    customer_order_no: str
    product: str
    quantity: float | None
    amount: float | None
    executed_shipped_qty: float | None
    invoiced_qty: float | None
    uninvoiced_qty: float | None
    order_unshipped_qty: float | None
    latest_outbound_date: str
    days_after_outbound: int | None
    days_over_threshold: int | None
    order_outbound_status: str
    line_outbound_status: str


@dataclass
class _OrderSection:
    customer: str
    order_no: str
    order_outbound_status: str
    should_lines: list[_ExportLine] = field(default_factory=list)
    done_lines: list[_ExportLine] = field(default_factory=list)
    hold_lines: list[_ExportLine] = field(default_factory=list)

    @property
    def lines(self) -> list[_ExportLine]:
        return [*self.should_lines, *self.done_lines, *self.hold_lines]

    @property
    def total_amount(self) -> float:
        return _sum_known_amount(line.amount for line in self.lines)

    @property
    def total_amount_missing(self) -> bool:
        return any(line.amount is None for line in self.lines)

    @property
    def invoiced_amount(self) -> float:
        return _sum_known_amount(_line_invoiced_amount(line) for line in self.lines)

    @property
    def invoiced_amount_missing(self) -> bool:
        return any(_line_invoiced_amount(line) is None for line in self.lines)

    @property
    def urgent_amount(self) -> float:
        return _sum_known_amount(_line_uninvoiced_amount(line) for line in self.should_lines)

    @property
    def urgent_amount_missing(self) -> bool:
        return any(_line_uninvoiced_amount(line) is None for line in self.should_lines)

    @property
    def hold_amount(self) -> float:
        return _sum_known_amount(line.amount for line in self.hold_lines)

    @property
    def hold_amount_missing(self) -> bool:
        return any(line.amount is None for line in self.hold_lines)

    @property
    def max_days_over_threshold(self) -> int | None:
        values = [line.days_over_threshold for line in self.should_lines if line.days_over_threshold is not None]
        return max(values) if values else None

    @property
    def max_days_after_outbound(self) -> int | None:
        values = [line.days_after_outbound for line in self.should_lines if line.days_after_outbound is not None]
        return max(values) if values else None

    @property
    def status_label(self) -> str:
        if _is_fully_outbound(self.order_outbound_status):
            return "整单已发完"
        return "整单未发完"


@dataclass
class _CustomerSection:
    customer: str
    orders: list[_OrderSection] = field(default_factory=list)

    @property
    def urgent_amount(self) -> float:
        return _sum_known_amount(order.urgent_amount for order in self.orders)

    @property
    def urgent_amount_missing(self) -> bool:
        return any(order.urgent_amount_missing for order in self.orders)

    @property
    def hold_amount(self) -> float:
        return _sum_known_amount(order.hold_amount for order in self.orders)

    @property
    def hold_amount_missing(self) -> bool:
        return any(order.hold_amount_missing for order in self.orders)

    @property
    def max_days_over_threshold(self) -> int | None:
        values = [order.max_days_over_threshold for order in self.orders if order.max_days_over_threshold is not None]
        return max(values) if values else None

    @property
    def max_days_after_outbound(self) -> int | None:
        values = [order.max_days_after_outbound for order in self.orders if order.max_days_after_outbound is not None]
        return max(values) if values else None


def _sum_known_amount(values) -> float:
    total = 0.0
    for value in values:
        if value is not None:
            total += float(value)
    return round(total, 6)


def _line_invoiced_amount(line: _ExportLine) -> float | None:
    if line.invoiced_qty is None:
        return None
    if line.invoiced_qty <= 0:
        return 0.0
    if line.amount is None or line.quantity is None or line.quantity <= 0:
        return None
    return round(float(line.amount) * float(line.invoiced_qty) / float(line.quantity), 6)


def _line_uninvoiced_amount(line: _ExportLine) -> float | None:
    if line.uninvoiced_qty is None:
        return None
    if line.uninvoiced_qty <= 0:
        return 0.0
    if line.amount is None or line.quantity is None or line.quantity <= 0:
        return None
    return round(float(line.amount) * float(line.uninvoiced_qty) / float(line.quantity), 6)


def _clean_text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _is_fully_outbound(value: Any) -> bool:
    return str(value or "").strip().lower() == "fully_outbound"


def _is_invoice_done(line: _ExportLine) -> bool:
    if line.uninvoiced_qty is not None and line.uninvoiced_qty <= 0:
        return True
    if line.quantity is not None and line.invoiced_qty is not None:
        return line.quantity > 0 and line.invoiced_qty >= line.quantity
    return False


def _line_invoice_tag(line: _ExportLine) -> str:
    if line.invoiced_qty is None or line.uninvoiced_qty is None:
        return "开票状态暂无"
    if line.uninvoiced_qty <= 0:
        return "产品已开完"
    if line.invoiced_qty <= 0:
        return "产品未开票"
    return "产品已部分开票"


def _fmt_num(value: float | None) -> str:
    if value is None:
        return "暂无"
    rounded = round(float(value), 6)
    if abs(rounded) < 1e-12:
        rounded = 0.0
    return f"{rounded:.6f}".rstrip("0").rstrip(".") or "0"


def _fmt_amount(value: float | None) -> str:
    if value is None:
        return "金额暂无"
    return f"¥{float(value):,.2f}"


def _fmt_total_amount(value: float, has_missing: bool) -> str:
    text = _fmt_amount(value)
    return f"{text}（部分金额暂缺）" if has_missing else text


def _fmt_days(value: int | None) -> str:
    return f"{value} 天" if value is not None else "暂无"


def _payload_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _core_from_record(record: NormalizedRecord) -> dict[str, Any]:
    payload = _payload_dict(record.payload_json)
    return _payload_dict(payload.get("core"))


def _customer_from_group(group: MatchGroup | None, payload: dict[str, Any]) -> str:
    if group and isinstance(group.summary_json, dict):
        aggregate = group.summary_json.get("aggregate")
        if isinstance(aggregate, dict):
            text = _clean_text(aggregate.get("customer"))
            if text:
                return text
    return _clean_text(payload.get("customer"), "未知客户")


def _order_no(value: Any) -> str:
    display = viewer_display_order_no(value)
    return _clean_text(display or value, "未知单据")


def _order_key(customer: Any, order_no: Any) -> tuple[str, str]:
    return (normalize_customer_key(_clean_text(customer)), _clean_text(order_no).lower())


def _product_label(item_name: Any, item_code: Any) -> str:
    name = _clean_text(item_name)
    code = _clean_text(item_code)
    if name and code:
        return f"{name}（{code}）"
    return name or code or "未知产品"


def _days_from_payload(payload: dict[str, Any]) -> int | None:
    value = _to_float(payload.get("days_after_outbound"))
    return int(value) if value is not None else None


def _line_from_record(
    record: NormalizedRecord,
    *,
    fallback_payload: dict[str, Any] | None = None,
    fallback_customer: str = "",
    generated_day: datetime | None = None,
) -> _ExportLine:
    payload = fallback_payload or {}
    core = _core_from_record(record)
    generated_day = generated_day or datetime.now(_SH_TZ)

    def pick(field: str) -> Any:
        value = core.get(field)
        if value is not None and value != "":
            return value
        return payload.get(field)

    customer = _clean_text(pick("customer") or fallback_customer, "未知客户")
    order_no = _order_no(pick("customer_order_no"))
    latest_outbound_date = _clean_text(pick("latest_outbound_date"))
    days_after_outbound = _days_from_payload(payload)
    if days_after_outbound is None and latest_outbound_date:
        outbound_day = parse_date(latest_outbound_date)
        if outbound_day:
            days_after_outbound = (generated_day.date() - outbound_day).days
    days_over_threshold = max(days_after_outbound - _SHIP_DAYS, 0) if days_after_outbound is not None else None

    return _ExportLine(
        record_id=_clean_text(record.id),
        source_row=record.source_row,
        entry_line_no=_clean_text(pick("entry_line_no")),
        customer=customer,
        customer_order_no=order_no,
        product=_product_label(pick("item_name"), pick("item_code")),
        quantity=_to_float(pick("quantity")),
        amount=_to_float(pick("amount")),
        executed_shipped_qty=_to_float(pick("executed_shipped_qty")),
        invoiced_qty=_to_float(pick("invoiced_qty")),
        uninvoiced_qty=_to_float(pick("uninvoiced_qty")),
        order_unshipped_qty=_to_float(pick("order_unshipped_qty")),
        latest_outbound_date=latest_outbound_date or "暂无",
        days_after_outbound=days_after_outbound,
        days_over_threshold=days_over_threshold,
        order_outbound_status=_clean_text(pick("order_outbound_status")),
        line_outbound_status=_clean_text(pick("line_outbound_status")),
    )


def _line_from_alert_payload(entry: dict[str, Any], *, generated_day: datetime | None = None) -> _ExportLine:
    payload = _payload_dict(entry.get("payload"))
    generated_day = generated_day or datetime.now(_SH_TZ)
    customer = _clean_text(entry.get("customer"), "未知客户")
    order_no = _order_no(payload.get("customer_order_no"))
    latest_outbound_date = _clean_text(payload.get("latest_outbound_date"))
    days_after_outbound = _days_from_payload(payload)
    if days_after_outbound is None and latest_outbound_date:
        outbound_day = parse_date(latest_outbound_date)
        if outbound_day:
            days_after_outbound = (generated_day.date() - outbound_day).days
    days_over_threshold = max(days_after_outbound - _SHIP_DAYS, 0) if days_after_outbound is not None else None
    amount = uninvoiced_amount_from_payload(payload, _clean_text(entry.get("message")))

    return _ExportLine(
        record_id=_clean_text(payload.get("record_id") or f"alert:{getattr(entry.get('alert'), 'id', '')}"),
        source_row=int(payload.get("source_row") or 0) or None,
        entry_line_no=_clean_text(payload.get("entry_line_no")),
        customer=customer,
        customer_order_no=order_no,
        product=_product_label(payload.get("item_name"), payload.get("item_code")),
        quantity=_to_float(payload.get("quantity")),
        amount=amount,
        executed_shipped_qty=_to_float(payload.get("executed_shipped_qty")),
        invoiced_qty=_to_float(payload.get("invoiced_qty")),
        uninvoiced_qty=_to_float(payload.get("uninvoiced_qty")),
        order_unshipped_qty=_to_float(payload.get("order_unshipped_qty")),
        latest_outbound_date=latest_outbound_date or "暂无",
        days_after_outbound=days_after_outbound,
        days_over_threshold=days_over_threshold,
        order_outbound_status=_clean_text(payload.get("order_outbound_status")),
        line_outbound_status=_clean_text(payload.get("line_outbound_status")),
    )


def _line_sort_key(line: _ExportLine) -> tuple[int, int, str]:
    entry_value = _to_float(line.entry_line_no)
    return (
        int(entry_value) if entry_value is not None else 999999,
        int(line.source_row or 999999),
        line.product,
    )


def _load_uninvoiced_entries(db: Session) -> list[dict[str, Any]]:
    rows = (
        db.query(Alert, MatchGroup)
        .join(MatchGroup, Alert.group_id == MatchGroup.id)
        .filter(Alert.alert_type == RULE_SHIP_AFTER_NO_FINANCE, Alert.status == "open")
        .all()
    )
    disabled_keys = load_disabled_customer_alert_keys(db)
    entries: list[dict[str, Any]] = []
    for alert, group in rows:
        payload = _payload_dict(alert.payload_json)
        customer = _customer_from_group(group, payload)
        customer_key = normalize_customer_key(customer)
        if customer_key and (customer_key, alert.alert_type) in disabled_keys:
            continue
        entries.append(
            {
                "alert": alert,
                "group": group,
                "payload": payload,
                "customer": customer,
                "message": alert.message,
                "created_at": alert.created_at,
                "last_changed_at": alert.created_at,
            }
        )
    return dedupe_uninvoiced_entries(entries)


def _load_current_order_records(db: Session) -> list[NormalizedRecord]:
    return (
        db.query(NormalizedRecord)
        .filter(
            NormalizedRecord.document_type == "order",
            NormalizedRecord.lifecycle_state == "active",
            NormalizedRecord.is_current_effective.is_(True),
        )
        .order_by(NormalizedRecord.source_row.asc(), NormalizedRecord.created_at.asc())
        .all()
    )


def _build_customer_sections(db: Session, generated_at: datetime) -> list[_CustomerSection]:
    entries = _load_uninvoiced_entries(db)
    if not entries:
        return []

    records = _load_current_order_records(db)
    records_by_id = {record.id: record for record in records}
    records_by_order: dict[tuple[str, str], list[NormalizedRecord]] = defaultdict(list)
    for record in records:
        core = _core_from_record(record)
        customer = _clean_text(core.get("customer"))
        order_no = _order_no(core.get("customer_order_no"))
        if customer and order_no != "未知单据":
            records_by_order[_order_key(customer, order_no)].append(record)

    alert_lines_by_order: dict[tuple[str, str], list[_ExportLine]] = defaultdict(list)
    should_record_ids_by_order: dict[tuple[str, str], set[str]] = defaultdict(set)
    for entry in entries:
        payload = _payload_dict(entry.get("payload"))
        record = records_by_id.get(_clean_text(payload.get("record_id")))
        if record:
            line = _line_from_record(
                record,
                fallback_payload=payload,
                fallback_customer=_clean_text(entry.get("customer")),
                generated_day=generated_at,
            )
        else:
            line = _line_from_alert_payload(entry, generated_day=generated_at)
        key = _order_key(line.customer, line.customer_order_no)
        alert_lines_by_order[key].append(line)
        should_record_ids_by_order[key].add(line.record_id)

    customer_map: dict[str, _CustomerSection] = {}
    for key, alert_lines in alert_lines_by_order.items():
        context_records = records_by_order.get(key, [])
        context_lines = [
            _line_from_record(record, generated_day=generated_at)
            for record in context_records
        ]
        existing_ids = {line.record_id for line in context_lines}
        context_lines.extend(line for line in alert_lines if line.record_id not in existing_ids)
        should_ids = should_record_ids_by_order[key]

        order = _OrderSection(
            customer=alert_lines[0].customer,
            order_no=alert_lines[0].customer_order_no,
            order_outbound_status=alert_lines[0].order_outbound_status,
        )
        for line in sorted(context_lines, key=_line_sort_key):
            if line.record_id in should_ids:
                order.should_lines.append(line)
                if not order.order_outbound_status:
                    order.order_outbound_status = line.order_outbound_status
            elif not _is_fully_outbound(line.line_outbound_status):
                order.hold_lines.append(line)
            elif _is_invoice_done(line):
                order.done_lines.append(line)

        if not order.should_lines:
            continue
        customer_key = normalize_customer_key(order.customer) or order.customer
        customer_section = customer_map.setdefault(customer_key, _CustomerSection(customer=order.customer))
        customer_section.orders.append(order)

    customers = list(customer_map.values())
    for customer in customers:
        customer.orders.sort(
            key=lambda order: (
                0 if _is_fully_outbound(order.order_outbound_status) else 1,
                -order.urgent_amount,
                order.order_no,
            )
        )
    customers.sort(key=lambda customer: (-customer.urgent_amount, customer.customer))
    return customers


def _render_line(line: _ExportLine) -> str:
    invoice_tag = _line_invoice_tag(line)
    uninvoiced_amount = _line_uninvoiced_amount(line)
    metrics = [
        ("还差开票金额", _fmt_amount(uninvoiced_amount)),
        ("还差开票数量", _fmt_num(line.uninvoiced_qty)),
        ("已开票数量", _fmt_num(line.invoiced_qty)),
        ("订单数量", _fmt_num(line.quantity)),
        ("已出库数量", _fmt_num(line.executed_shipped_qty)),
        ("最近出库日期", line.latest_outbound_date),
        ("出库天数", _fmt_days(line.days_after_outbound)),
    ]
    metric_html = "".join(
        f"<span><b>{escape(label)}</b>{escape(str(value))}</span>" for label, value in metrics
    )
    return (
        f'<div class="product-line product-line--should">'
        f'<div class="product-main">'
        f'<span class="product-name">{escape(line.product)}</span>'
        f'<span class="line-tag">{escape(invoice_tag)}</span>'
        f"</div>"
        f'<div class="line-metrics">{metric_html}</div>'
        f"</div>"
    )


def _render_order(order: _OrderSection, *, order_index: int, order_total: int) -> str:
    should_html = "".join(_render_line(line) for line in order.should_lines)
    order_qty = _sum_known_amount(line.quantity for line in order.lines)
    invoiced_qty = _sum_known_amount(line.invoiced_qty for line in order.lines)
    pending_qty = _sum_known_amount(line.uninvoiced_qty for line in order.should_lines)
    order_index_text = f"订单 {order_index}/{order_total}"
    summary = [
        ("订单总金额", _fmt_total_amount(order.total_amount, order.total_amount_missing)),
        ("已开票金额", _fmt_total_amount(order.invoiced_amount, order.invoiced_amount_missing)),
        ("还差开票金额", _fmt_total_amount(order.urgent_amount, order.urgent_amount_missing)),
        ("订单总量", _fmt_num(order_qty)),
        ("已开票数量", _fmt_num(invoiced_qty)),
        ("还差开票数量", _fmt_num(pending_qty)),
    ]
    summary_html = "".join(f"<span><b>{escape(label)}</b>{escape(value)}</span>" for label, value in summary)
    return f"""
      <article class="order-card">
        <header class="order-header">
          <div class="order-title">
            <strong>{escape(order.customer)}</strong>
            <span>{escape(order.order_no)}</span>
          </div>
          <span class="status-tag">{escape(order.status_label)}</span>
          <div class="amount-focus">
            <span>应催金额</span>
            <strong>{escape(_fmt_total_amount(order.urgent_amount, order.urgent_amount_missing))}</strong>
          </div>
        </header>
        <div class="order-summary">{summary_html}</div>
        {should_html}
        <div class="order-index">{escape(order_index_text)}</div>
      </article>
    """


def _render_html(customers: list[_CustomerSection], generated_at: datetime) -> str:
    total_urgent = _sum_known_amount(customer.urgent_amount for customer in customers)
    total_urgent_missing = any(customer.urgent_amount_missing for customer in customers)
    order_count = sum(len(customer.orders) for customer in customers)
    generated_text = generated_at.astimezone(_SH_TZ).strftime("%Y-%m-%d %H:%M")
    customer_html: list[str] = []
    customer_total = len(customers)
    for customer_index, customer in enumerate(customers, start=1):
        customer_index_text = f"客户 {customer_index}/{customer_total}"
        summary = [
            ("应催金额", _fmt_total_amount(customer.urgent_amount, customer.urgent_amount_missing)),
            ("出库天数", _fmt_days(customer.max_days_after_outbound)),
            ("相关订单数", f"{len(customer.orders)} 笔"),
        ]
        summary_html = "".join(f"<span><b>{escape(label)}</b>{escape(value)}</span>" for label, value in summary)
        order_total = len(customer.orders)
        orders_html = "".join(
            _render_order(order, order_index=index, order_total=order_total)
            for index, order in enumerate(customer.orders, start=1)
        )
        customer_html.append(
            f"""
            <section class="customer-section">
              <div class="customer-summary">
                <header class="customer-heading">
                  <h2>{escape(customer.customer)}</h2>
                  <span class="customer-index">{escape(customer_index_text)}</span>
                </header>
                <div>{summary_html}</div>
              </div>
              <div class="order-grid">{orders_html}</div>
            </section>
            """
        )

    body = "\n".join(customer_html)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_HTML_TITLE}</title>
  <style>
    :root {{
      color: #161616;
      background: #ffffff;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #f7f8f6; color: #161616; }}
    main {{ max-width: 1120px; margin: 0 auto; padding: 24px 16px 40px; }}
    .topbar {{ margin-bottom: 18px; border-bottom: 2px solid #222; padding-bottom: 14px; }}
    h1 {{ margin: 0 0 8px; font-size: 30px; font-weight: 750; letter-spacing: 0; }}
    .export-time {{ color: #555; font-size: 13px; }}
    .overview {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin: 18px 0 24px; }}
    .overview-item, .customer-summary, .order-card {{
      background: #fff;
      border: 1px solid #cfcfc8;
      border-radius: 8px;
      box-shadow: none;
    }}
    .overview-item {{ padding: 12px; }}
    .overview-item span, .amount-focus span {{ display: block; color: #666; font-size: 12px; }}
    .overview-item strong {{ display: block; margin-top: 4px; font-size: 20px; }}
    .customer-section {{ margin-top: 22px; }}
    .customer-summary {{ padding: 14px; margin-bottom: 10px; border-left: 4px solid #222; }}
    .customer-heading {{ display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 10px; }}
    .customer-heading h2 {{ flex: 1; min-width: 0; margin: 0; font-size: 21px; overflow-wrap: anywhere; }}
    .customer-index {{
      border: 1px solid #777;
      border-radius: 999px;
      padding: 3px 8px;
      color: #555;
      font-size: 12px;
      white-space: nowrap;
      background: #fff;
    }}
    .customer-summary div, .order-summary, .line-metrics {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }}
    .order-summary {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    .customer-summary span, .order-summary span, .line-metrics span {{
      border-top: 1px solid #e3e3de;
      padding-top: 6px;
      min-width: 0;
      font-size: 13px;
    }}
    .customer-summary b, .order-summary b, .line-metrics b {{
      display: block;
      color: #666;
      font-size: 11px;
      font-weight: 600;
      margin-bottom: 3px;
    }}
    .order-grid {{ display: grid; grid-template-columns: 1fr; gap: 12px; }}
    .order-card {{ padding: 14px; break-inside: avoid; page-break-inside: avoid; }}
    .order-header {{ display: grid; grid-template-columns: 1fr auto; gap: 10px; align-items: start; border-bottom: 1px solid #d8d8d2; padding-bottom: 10px; }}
    .order-title strong, .order-title span {{ display: block; }}
    .order-title strong {{ font-size: 17px; }}
    .order-title span {{ margin-top: 3px; font-size: 13px; color: #555; }}
    .status-tag, .line-tag {{
      border: 1px solid #777;
      border-radius: 999px;
      padding: 3px 8px;
      font-size: 12px;
      white-space: nowrap;
      background: #fff;
    }}
    .amount-focus {{ grid-column: 1 / -1; padding-top: 6px; }}
    .amount-focus strong {{ display: block; margin-top: 2px; font-size: 24px; }}
    .order-summary {{ margin: 10px 0 12px; }}
    .order-index {{ margin-top: 10px; text-align: right; color: #666; font-size: 12px; }}
    h4 {{ margin: 14px 0 8px; font-size: 14px; border-bottom: 1px solid #ecece7; padding-bottom: 5px; }}
    .product-line {{ border: 1px solid #dadad4; border-radius: 7px; padding: 10px; margin-top: 8px; background: #fff; }}
    .product-main {{ display: flex; gap: 8px; align-items: center; justify-content: space-between; }}
    .product-name {{ font-weight: 700; min-width: 0; overflow-wrap: anywhere; }}
    .line-metrics {{ margin-top: 8px; grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    @media (max-width: 640px) {{
      main {{ padding: 18px 12px 32px; }}
      .overview, .customer-summary div {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .order-summary {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
      .order-grid {{ grid-template-columns: 1fr; }}
      h1 {{ font-size: 26px; }}
    }}
    @media print {{
      @page {{ size: A4; margin: 10mm; }}
      body {{ background: #fff; }}
      main {{ max-width: none; padding: 0; }}
      .overview {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
      .order-grid {{ grid-template-columns: 1fr; gap: 6mm; }}
      .order-card, .customer-summary, .overview-item {{ box-shadow: none; }}
      .customer-section {{ break-inside: auto; }}
    }}
  </style>
</head>
<body>
  <main>
    <header class="topbar">
      <h1>{_HTML_TITLE}</h1>
      <div class="export-time">导出时间：{escape(generated_text)}</div>
    </header>
    <section class="overview" aria-label="汇总">
      <div class="overview-item"><span>客户数</span><strong>{len(customers)}</strong></div>
      <div class="overview-item"><span>相关订单数</span><strong>{order_count}</strong></div>
      <div class="overview-item"><span>应催金额合计</span><strong>{escape(_fmt_total_amount(total_urgent, total_urgent_missing))}</strong></div>
    </section>
    {body}
  </main>
</body>
</html>
"""


def export_uninvoiced_html(db: Session, generated_at: datetime | None = None) -> bytes | None:
    generated_at = generated_at or datetime.now(timezone.utc).astimezone(_SH_TZ)
    customers = _build_customer_sections(db, generated_at)
    if not customers:
        return None
    return _render_html(customers, generated_at).encode("utf-8")


def export_alerts_csv(db: Session, job_id: str) -> bytes:
    alerts = (
        db.query(Alert)
        .filter(Alert.job_id == job_id, Alert.status == "open")
        .order_by(Alert.created_at.desc())
        .all()
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["alert_id", "job_id", "group_id", "alert_type", "severity", "status", "message", "created_at"])
    for a in alerts:
        writer.writerow([a.id, a.job_id, a.group_id, a.alert_type, a.severity, a.status, a.message, a.created_at.isoformat()])
    return output.getvalue().encode("utf-8-sig")



def export_customer_summary_csv(db: Session, job_id: str) -> bytes:
    rows = (
        db.query(Alert, MatchGroup)
        .join(MatchGroup, Alert.group_id == MatchGroup.id)
        .filter(Alert.job_id == job_id, Alert.status == "open")
        .all()
    )

    stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"alert_count": 0, "high_count": 0, "medium_count": 0})
    for alert, group in rows:
        customer = ((group.summary_json or {}).get("aggregate") or {}).get("customer") or "未知客户"
        stats[customer]["alert_count"] += 1
        if alert.severity == "high":
            stats[customer]["high_count"] += 1
        else:
            stats[customer]["medium_count"] += 1

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["customer", "alert_count", "high_count", "medium_count"])
    for customer, item in sorted(stats.items(), key=lambda x: x[1]["alert_count"], reverse=True):
        writer.writerow([customer, item["alert_count"], item["high_count"], item["medium_count"]])
    return output.getvalue().encode("utf-8-sig")
