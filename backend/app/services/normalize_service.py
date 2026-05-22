from __future__ import annotations

from datetime import date
from typing import Any

from dateutil import parser as dt_parser


NUMERIC_FIELDS = {"quantity", "amount", "order_total_amount", "tax_inclusive_unit_price"}
DATE_FIELDS = {"biz_date", "due_date", "ship_date", "notice_date", "invoice_date"}

_CLOSED_TRUE = {"1", "true", "yes", "y", "是", "已关闭", "关闭", "closed", "close"}
_CLOSED_FALSE = {"0", "false", "no", "n", "否", "未关闭", "开启", "open", "opened"}
_ORDER_IDENTITY_FIELDS = (
    "customer_order_no",
    "contract_no",
    "entry_line_no",
    "item_code",
    "item_name",
)
_ORDER_SIGNAL_FIELDS = (
    "due_date",
    "quantity",
    "amount",
    "order_total_amount",
    "latest_outbound_date",
    "order_outbound_status",
    "line_outbound_status",
    "executed_shipped_qty",
    "invoiced_qty",
    "uninvoiced_qty",
    "line_invoice_status",
)
_ORDER_REVIEW_REQUIRED_FIELDS = (
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
_NUMERIC_EPSILON = 1e-9
_ZERO_IF_EMPTY_ORDER_QTY_FIELDS = {
    "executed_shipped_qty",
    "invoiced_qty",
    "uninvoiced_qty",
}



def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        text = str(value).replace(",", "").strip()
        if text == "":
            return None
        return float(text)
    except (ValueError, TypeError):
        return None



def _to_date_str(value: Any) -> str | None:
    if value is None:
        return None
    try:
        parsed = dt_parser.parse(str(value)).date()
        return parsed.isoformat()
    except Exception:
        return None



def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "是", "正式"}:
        return True
    if text in {"0", "false", "no", "n", "否", "非正式"}:
        return False
    return None


def _to_closed_bool(value: Any) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in _CLOSED_TRUE:
        return True
    if text in _CLOSED_FALSE:
        return False
    return None


def _to_line_invoice_status(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {
        "invoiced",
        "full",
        "fully_invoiced",
        "全部开票",
        "已开票",
        "已全部开票",
        "开票完成",
        "已完成",
    }:
        return "invoiced"
    if text in {
        "partial",
        "partially_invoiced",
        "部分",
        "部分开票",
        "部分已开票",
    }:
        return "partial"
    if text in {
        "uninvoiced",
        "none",
        "not_invoiced",
        "未开票",
        "待开票",
        "未开发票",
    }:
        return "uninvoiced"
    return None


def _to_outbound_status(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {
        "fully_outbound",
        "full",
        "全部出库",
        "已全部出库",
        "完全出库",
        "全部发货",
    }:
        return "fully_outbound"
    if text in {
        "partially_outbound",
        "partial",
        "部分出库",
        "部分发货",
    }:
        return "partially_outbound"
    if text in {
        "not_outbound",
        "none",
        "未出库",
        "未发货",
        "没有出库",
    }:
        return "not_outbound"
    return None


def _to_entry_line_no(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        text = f"{value}".strip()
        return text or None
    text = str(value).strip()
    if not text:
        return None
    try:
        numeric = float(text)
    except (TypeError, ValueError):
        return text
    if numeric.is_integer():
        return str(int(numeric))
    return text


def _column_present(source_columns: dict[str, str | None] | None, field: str) -> bool:
    if not source_columns:
        return False
    return source_columns.get(field) is not None


def _has_meaningful_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _is_close(left: float, right: float) -> bool:
    return abs(left - right) <= _NUMERIC_EPSILON


def _uninvoiced_qty_for_skip_scan(value: Any, column_present: bool) -> float | None:
    if not column_present:
        return None
    if value is None:
        return 0.0
    if isinstance(value, str) and not value.strip():
        return 0.0
    return _to_float(value)


def _order_qty_zero_if_empty(value: Any, column_present: bool) -> float | None:
    if not column_present:
        return None
    if value is None:
        return 0.0
    if isinstance(value, str) and not value.strip():
        return 0.0
    return _to_float(value)


def derive_order_unshipped_qty(quantity: Any, executed_shipped_qty: Any) -> float | None:
    normalized_quantity = _to_float(quantity)
    normalized_shipped = _to_float(executed_shipped_qty)
    if normalized_quantity is None or normalized_shipped is None:
        return None
    return max(normalized_quantity - normalized_shipped, 0.0)


def _order_identity_hits(core: dict[str, Any]) -> int:
    return sum(1 for field in _ORDER_IDENTITY_FIELDS if _has_meaningful_value(core.get(field)))


def _order_signal_hits(core: dict[str, Any]) -> int:
    return sum(1 for field in _ORDER_SIGNAL_FIELDS if _has_meaningful_value(core.get(field)))


def _is_effective_order_core(core: dict[str, Any]) -> bool:
    if not core:
        return False
    return _order_identity_hits(core) >= 1 and _order_signal_hits(core) >= 1


def _is_dirty_completed_skip_scan_order(core: dict[str, Any], skip_scan_uninvoiced_qty: float | None = None) -> bool:
    if not _is_effective_order_core(core):
        return True

    has_order_anchor = _has_meaningful_value(core.get("customer_order_no")) or _has_meaningful_value(core.get("contract_no"))
    has_item_anchor = _has_meaningful_value(core.get("item_code")) or _has_meaningful_value(core.get("item_name"))
    if not has_order_anchor or not has_item_anchor:
        return True

    quantity = _to_float(core.get("quantity"))
    executed_shipped_qty = _to_float(core.get("executed_shipped_qty"))
    uninvoiced_qty = skip_scan_uninvoiced_qty

    if quantity is None or executed_shipped_qty is None or uninvoiced_qty is None:
        return True
    if quantity <= 0 or executed_shipped_qty < 0 or uninvoiced_qty < 0:
        return True
    if executed_shipped_qty > quantity and not _is_close(executed_shipped_qty, quantity):
        return True
    return False


def _derive_order_scan_state(core: dict[str, Any], skip_scan_uninvoiced_qty: float | None = None) -> str | None:
    if _is_dirty_completed_skip_scan_order(core, skip_scan_uninvoiced_qty=skip_scan_uninvoiced_qty):
        return None

    quantity = _to_float(core.get("quantity"))
    executed_shipped_qty = _to_float(core.get("executed_shipped_qty"))
    uninvoiced_qty = skip_scan_uninvoiced_qty
    if quantity is None or executed_shipped_qty is None or uninvoiced_qty is None:
        return None

    if quantity > 0 and _is_close(executed_shipped_qty, quantity) and _is_close(uninvoiced_qty, 0.0):
        return "completed_skip_scan"
    return None



def normalize_record(
    document_type: str,
    parsed_row: dict[str, Any],
    file_id: str,
    source_row: int,
    filename: str,
    source_columns: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    core: dict[str, Any] = {
        "customer": parsed_row.get("customer"),
        "contract_no": parsed_row.get("contract_no"),
        "customer_order_no": parsed_row.get("customer_order_no"),
        "entry_line_no": _to_entry_line_no(parsed_row.get("entry_line_no")),
        "item_code": parsed_row.get("item_code"),
        "item_name": parsed_row.get("item_name"),
        "quantity": _to_float(parsed_row.get("quantity")),
        "amount": _to_float(parsed_row.get("amount")),
        "order_total_amount": _to_float(parsed_row.get("order_total_amount")),
        "tax_inclusive_unit_price": _to_float(parsed_row.get("tax_inclusive_unit_price")),
        "biz_date": _to_date_str(parsed_row.get("biz_date")),
        "due_date": _to_date_str(parsed_row.get("due_date")),
        "ship_date": _to_date_str(parsed_row.get("ship_date")),
        "notice_date": _to_date_str(parsed_row.get("notice_date")),
        "invoice_date": _to_date_str(parsed_row.get("invoice_date")),
        "invoice_formal": _to_bool(parsed_row.get("invoice_formal")),
        "payment_notice_received": _to_bool(parsed_row.get("payment_notice_received")),
        "source_doc_type": document_type,
    }

    if document_type == "order":
        has_order_closed = _column_present(source_columns, "order_closed")
        has_line_closed = _column_present(source_columns, "line_closed")
        has_latest_outbound_date = _column_present(source_columns, "latest_outbound_date")
        has_order_outbound_status = _column_present(source_columns, "order_outbound_status")
        has_line_outbound_status = _column_present(source_columns, "line_outbound_status")
        has_executed_shipped_qty = _column_present(source_columns, "executed_shipped_qty")
        has_invoiced_qty = _column_present(source_columns, "invoiced_qty")
        has_uninvoiced_qty = _column_present(source_columns, "uninvoiced_qty")
        has_line_invoice_status = _column_present(source_columns, "line_invoice_status")
        skip_scan_uninvoiced_qty = _uninvoiced_qty_for_skip_scan(parsed_row.get("uninvoiced_qty"), has_uninvoiced_qty)

        core["order_closed"] = _to_closed_bool(parsed_row.get("order_closed")) if has_order_closed else None
        core["line_closed"] = _to_closed_bool(parsed_row.get("line_closed")) if has_line_closed else None
        core["latest_outbound_date"] = (
            _to_date_str(parsed_row.get("latest_outbound_date")) if has_latest_outbound_date else None
        )
        core["order_outbound_status"] = (
            _to_outbound_status(parsed_row.get("order_outbound_status")) if has_order_outbound_status else None
        )
        core["line_outbound_status"] = (
            _to_outbound_status(parsed_row.get("line_outbound_status")) if has_line_outbound_status else None
        )
        core["executed_shipped_qty"] = _order_qty_zero_if_empty(
            parsed_row.get("executed_shipped_qty"), has_executed_shipped_qty
        )
        core["invoiced_qty"] = _order_qty_zero_if_empty(parsed_row.get("invoiced_qty"), has_invoiced_qty)
        core["uninvoiced_qty"] = _order_qty_zero_if_empty(parsed_row.get("uninvoiced_qty"), has_uninvoiced_qty)
        core["order_unshipped_qty"] = derive_order_unshipped_qty(
            core.get("quantity"),
            core.get("executed_shipped_qty"),
        )
        core["line_invoice_status"] = (
            _to_line_invoice_status(parsed_row.get("line_invoice_status")) if has_line_invoice_status else None
        )
        core["scan_state"] = _derive_order_scan_state(core, skip_scan_uninvoiced_qty=skip_scan_uninvoiced_qty)

    if document_type == "payment_notice":
        core["payment_notice_received"] = True
    if document_type == "invoice" and core["invoice_formal"] is None:
        core["invoice_formal"] = True
    if document_type == "shipment" and core["ship_date"] is None:
        core["ship_date"] = _to_date_str(parsed_row.get("biz_date"))

    ext = {
        k: v
        for k, v in parsed_row.items()
        if k not in core
    }
    if document_type == "order":
        ext["order_closed_raw"] = parsed_row.get("order_closed")
        ext["line_closed_raw"] = parsed_row.get("line_closed")
        ext["latest_outbound_date_raw"] = parsed_row.get("latest_outbound_date")
        ext["order_outbound_status_raw"] = parsed_row.get("order_outbound_status")
        ext["line_outbound_status_raw"] = parsed_row.get("line_outbound_status")
        ext["executed_shipped_qty_raw"] = parsed_row.get("executed_shipped_qty")
        ext["invoiced_qty_raw"] = parsed_row.get("invoiced_qty")
        ext["uninvoiced_qty_raw"] = parsed_row.get("uninvoiced_qty")
        ext["line_invoice_status_raw"] = parsed_row.get("line_invoice_status")
        ext["due_date_raw"] = parsed_row.get("due_date")
        ext["document_no_raw"] = parsed_row.get("customer_order_no")
        ext["entry_line_no_raw"] = parsed_row.get("entry_line_no")
        ext["amount_raw"] = parsed_row.get("amount")
        ext["order_total_amount_raw"] = parsed_row.get("order_total_amount")
        ext["order_closed_column_present"] = _column_present(source_columns, "order_closed")
        ext["line_closed_column_present"] = _column_present(source_columns, "line_closed")
        ext["order_outbound_status_column_present"] = _column_present(source_columns, "order_outbound_status")
        ext["line_outbound_status_column_present"] = _column_present(source_columns, "line_outbound_status")
        ext["review_required_columns_present"] = {
            field: _column_present(source_columns, field) for field in _ORDER_REVIEW_REQUIRED_FIELDS
        }

    return {
        "core": core,
        "ext": ext,
        "attachments": {
            "file_id": file_id,
            "filename": filename,
        },
        "trace": {
            "source_row": source_row,
            "normalized_version": "v1",
        },
    }



def get_core(payload_json: dict[str, Any]) -> dict[str, Any]:
    core = payload_json.get("core", {})
    return core if isinstance(core, dict) else {}


def is_effective_order_payload(payload_json: dict[str, Any]) -> bool:
    core = get_core(payload_json)
    return _is_effective_order_core(core)


def parse_date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        return dt_parser.parse(str(value)).date()
    except Exception:
        return None
