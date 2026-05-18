from __future__ import annotations

from datetime import date
from typing import Any

from app.services.normalize_service import parse_date


RULE_DUE_BEFORE_SHIP = "due_before_ship"
RULE_SHIP_AFTER_NO_FINANCE = "ship_after_no_finance"
_ZERO_IF_EMPTY_QTY_FIELDS = {"executed_shipped_qty", "invoiced_qty", "uninvoiced_qty"}


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_business_qty(value: Any, *, field: str) -> float | None:
    parsed = _to_float(value)
    if parsed is not None:
        return parsed
    if field in _ZERO_IF_EMPTY_QTY_FIELDS:
        return 0.0
    return None


def _is_diff(left: float, right: float) -> bool:
    return abs(left - right) > 1e-9


def _fmt_num(value: float | None) -> str:
    if value is None:
        return "未知"
    rounded = round(value, 6)
    if abs(rounded) < 1e-12:
        rounded = 0.0
    text = f"{rounded:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _order_unshipped_qty(quantity: float | None, executed_shipped_qty: float | None) -> float | None:
    if quantity is None or executed_shipped_qty is None:
        return None
    return max(quantity - executed_shipped_qty, 0.0)


def _doc_no(line: dict[str, Any]) -> str:
    value = str(line.get("customer_order_no") or "").strip()
    return value or "未知单据"


def _item_label(line: dict[str, Any]) -> str:
    item_name = str(line.get("item_name") or "").strip()
    item_code = str(line.get("item_code") or "").strip()
    if item_name and item_code:
        return f"{item_name} / {item_code}"
    if item_name:
        return item_name
    if item_code:
        return item_code
    return "未知商品"


def _build_due_messages(
    line: dict[str, Any],
    due: date,
    days_until_due: int,
    quantity: float,
    executed_shipped_qty: float,
) -> tuple[str, str]:
    doc_no = _doc_no(line)
    item = _item_label(line)
    due_text = due.isoformat()
    quantity_text = _fmt_num(quantity)
    shipped_text = _fmt_num(executed_shipped_qty)
    pending_text = _fmt_num(max(quantity - executed_shipped_qty, 0.0))
    if days_until_due < 0:
        overdue_days = abs(days_until_due)
        short = (
            f"订单〔{doc_no}〕-〔{item}〕未发齐，交期已过〔{overdue_days}〕天，"
            f"应发〔{quantity_text}〕，已发〔{shipped_text}〕。"
        )
        long = (
            f"订单〔{doc_no}〕的产品〔{item}〕未发齐，预计交货日期〔{due_text}〕，"
            f"交期已过〔{overdue_days}〕天，应发〔{quantity_text}〕，已发〔{shipped_text}〕，"
            f"未发〔{pending_text}〕，请尽快补发。"
        )
        return short, long

    short = (
        f"订单〔{doc_no}〕-〔{item}〕距交期还有〔{days_until_due}〕天，"
        f"未发齐，应发〔{quantity_text}〕，已发〔{shipped_text}〕。"
    )
    long = (
        f"订单〔{doc_no}〕的产品〔{item}〕未发齐，预计交货日期〔{due_text}〕，"
        f"距交期还有〔{days_until_due}〕天，应发〔{quantity_text}〕，已发〔{shipped_text}〕，"
        f"未发〔{pending_text}〕，请提前安排发货。"
    )
    return short, long


def _build_invoice_messages(
    line: dict[str, Any],
    latest_outbound_date: date,
    days_after_outbound: int,
    uninvoiced_qty: float,
    amount: float | None,
    order_unshipped_qty: float | None = None,
) -> tuple[str, str]:
    doc_no = _doc_no(line)
    item = _item_label(line)
    outbound_text = latest_outbound_date.isoformat()
    uninvoiced_text = _fmt_num(uninvoiced_qty)
    unshipped_text = _fmt_num(order_unshipped_qty)
    unshipped_suffix = (
        f"，当前未发货数量〔{unshipped_text}〕"
        if order_unshipped_qty is not None
        else ""
    )
    if amount is not None:
        amount_text = _fmt_num(amount)
        short = (
            f"订单〔{doc_no}〕产品〔{item}〕最近出库日期〔{outbound_text}〕，"
            f"距最近出库已〔{days_after_outbound}〕天，未开票数量〔{uninvoiced_text}〕，"
            f"金额〔{amount_text}〕{unshipped_suffix}，请尽快开票。"
        )
        long = (
            f"订单〔{doc_no}〕的产品〔{item}〕，最近出库日期是〔{outbound_text}〕，"
            f"距今已〔{days_after_outbound}〕天，未开票数量〔{uninvoiced_text}〕，"
            f"金额〔{amount_text}〕{unshipped_suffix}，请尽快开票。"
        )
        return short, long

    short = (
        f"订单〔{doc_no}〕产品〔{item}〕最近出库日期〔{outbound_text}〕，"
        f"距最近出库已〔{days_after_outbound}〕天，未开票数量〔{uninvoiced_text}〕{unshipped_suffix}，请尽快开票。"
    )
    long = (
        f"订单〔{doc_no}〕的产品〔{item}〕，最近出库日期是〔{outbound_text}〕，"
        f"距今已〔{days_after_outbound}〕天，未开票数量〔{uninvoiced_text}〕，金额暂无{unshipped_suffix}，请尽快开票。"
    )
    return short, long


def _line_inputs(group: dict[str, Any]) -> list[dict[str, Any]]:
    agg = group.get("aggregate", {})
    candidates = agg.get("line_candidates")
    if isinstance(candidates, list) and candidates:
        return [c for c in candidates if isinstance(c, dict)]

    # Backward compatibility for historical aggregates.
    return [
        {
            "record_id": agg.get("winner_record_id"),
            "source_row": None,
            "line_key": agg.get("winner_record_id"),
            "line_key_confidence": "legacy",
            "scan_state": agg.get("scan_state"),
            "customer": agg.get("customer"),
            "customer_order_no": None,
            "entry_line_no": agg.get("entry_line_no"),
            "item_code": None,
            "item_name": None,
            "quantity": agg.get("quantity"),
            "amount": agg.get("amount"),
            "due_date": agg.get("due_date"),
            "executed_shipped_qty": agg.get("executed_shipped_qty"),
            "latest_outbound_date": agg.get("latest_outbound_date"),
            "invoiced_qty": agg.get("invoiced_qty"),
            "uninvoiced_qty": agg.get("uninvoiced_qty"),
            "line_invoice_status": agg.get("line_invoice_status"),
            "order_closed": agg.get("order_closed"),
            "line_closed": agg.get("line_closed"),
            "latest_record_basis": agg.get("latest_record_basis"),
        }
    ]


def run_alerts(groups: list[dict[str, Any]], rule_params: dict[str, Any], today: date | None = None) -> list[dict[str, Any]]:
    today = today or date.today()
    enabled = rule_params.get("enabled", {})
    n_due = int(rule_params.get("due_before_ship_days", 5))
    n_ship = int(rule_params.get("ship_after_no_finance_days", 60))

    alerts: list[dict[str, Any]] = []

    for group in groups:
        group_key = group.get("group_key")
        for line in _line_inputs(group):
            if line.get("scan_state") == "completed_skip_scan":
                continue

            quantity = _to_float(line.get("quantity"))
            amount = _to_float(line.get("amount"))
            executed_shipped_qty = _to_business_qty(line.get("executed_shipped_qty"), field="executed_shipped_qty")
            due = parse_date(line.get("due_date"))
            latest_outbound_date = parse_date(line.get("latest_outbound_date"))
            uninvoiced_qty = _to_business_qty(line.get("uninvoiced_qty"), field="uninvoiced_qty")

            payload_common = {
                "record_id": line.get("record_id"),
                "source_row": line.get("source_row"),
                "line_key": line.get("line_key"),
                "line_key_confidence": line.get("line_key_confidence"),
                "order_closed": line.get("order_closed"),
                "line_closed": line.get("line_closed"),
                "latest_record_basis": line.get("latest_record_basis"),
                "customer_order_no": line.get("customer_order_no"),
                "entry_line_no": line.get("entry_line_no"),
                "item_code": line.get("item_code"),
                "item_name": line.get("item_name"),
                "amount": amount,
                "order_unshipped_qty": _order_unshipped_qty(quantity, executed_shipped_qty),
            }

            if enabled.get(RULE_DUE_BEFORE_SHIP, True):
                if due and quantity is not None and executed_shipped_qty is not None and _is_diff(quantity, executed_shipped_qty):
                    days_until_due = (due - today).days
                    if days_until_due <= n_due:
                        severity = "high" if days_until_due < 0 else "medium"
                        short_msg, long_msg = _build_due_messages(
                            line=line,
                            due=due,
                            days_until_due=days_until_due,
                            quantity=quantity,
                            executed_shipped_qty=executed_shipped_qty,
                        )
                        alerts.append(
                            {
                                "group_key": group_key,
                                "alert_type": RULE_DUE_BEFORE_SHIP,
                                "severity": severity,
                                "message": short_msg,
                                "payload": {
                                    **payload_common,
                                    "days_until_due": days_until_due,
                                    "quantity": quantity,
                                    "executed_shipped_qty": executed_shipped_qty,
                                    "due_date": due.isoformat(),
                                    "n_days": n_due,
                                    "message_short": short_msg,
                                    "message_long": long_msg,
                                },
                            }
                        )

            if enabled.get(RULE_SHIP_AFTER_NO_FINANCE, True):
                if (
                    latest_outbound_date
                    and executed_shipped_qty is not None
                    and executed_shipped_qty > 0
                    and uninvoiced_qty is not None
                    and uninvoiced_qty > 0
                ):
                    days_after_outbound = (today - latest_outbound_date).days
                    if days_after_outbound >= n_ship:
                        short_msg, long_msg = _build_invoice_messages(
                            line=line,
                            latest_outbound_date=latest_outbound_date,
                            days_after_outbound=days_after_outbound,
                            uninvoiced_qty=uninvoiced_qty,
                            amount=amount,
                            order_unshipped_qty=payload_common.get("order_unshipped_qty"),
                        )
                        alerts.append(
                            {
                                "group_key": group_key,
                                "alert_type": RULE_SHIP_AFTER_NO_FINANCE,
                                "severity": "high" if days_after_outbound >= (n_ship * 2) else "medium",
                                "message": short_msg,
                                "payload": {
                                    **payload_common,
                                    "days_after_outbound": days_after_outbound,
                                    "executed_shipped_qty": executed_shipped_qty,
                                    "uninvoiced_qty": uninvoiced_qty,
                                    "latest_outbound_date": latest_outbound_date.isoformat(),
                                    "n_days": n_ship,
                                    "message_short": short_msg,
                                    "message_long": long_msg,
                                },
                            }
                        )

    return alerts
