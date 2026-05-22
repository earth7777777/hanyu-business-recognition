from __future__ import annotations

from datetime import datetime
from typing import Any


_INTERNAL_ORDER_PREFIXES = ("AMOUNT-FIX-",)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _clean_key(value: Any) -> str:
    return _clean_text(value).lower()


def _number_from_text(value: Any) -> float | None:
    cleaned = _clean_text(value).replace(",", "")
    cleaned = "".join(ch for ch in cleaned if ch.isdigit() or ch in {".", "-"})
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def uninvoiced_amount_from_payload(payload: dict[str, Any], message: str) -> float | None:
    amount = _number_from_text(payload.get("amount"))
    if amount is not None:
        return amount
    marker = "金额〔"
    if marker in message:
        suffix = message.split(marker, 1)[1]
        return _number_from_text(suffix.split("〕", 1)[0])
    return None


def actual_uninvoiced_amount_from_payload(payload: dict[str, Any], message: str = "") -> float | None:
    uninvoiced_qty = _number_from_text(payload.get("uninvoiced_qty"))
    if uninvoiced_qty is None:
        return None
    if uninvoiced_qty <= 0:
        return 0.0

    unit_price = _number_from_text(payload.get("tax_inclusive_unit_price"))
    if unit_price is not None and not _is_zero_number(unit_price):
        return round(float(unit_price) * float(uninvoiced_qty), 6)

    line_amount = uninvoiced_amount_from_payload(payload, message)
    line_quantity = _number_from_text(payload.get("quantity"))
    if line_amount is None or _is_zero_number(line_amount) or line_quantity is None or line_quantity <= 0:
        return None
    return round(float(line_amount) * float(uninvoiced_qty) / float(line_quantity), 6)


def is_internal_placeholder_order_no(value: Any) -> bool:
    raw = _clean_text(value).upper()
    if not raw:
        return False
    return any(raw.startswith(prefix) for prefix in _INTERNAL_ORDER_PREFIXES)


def viewer_display_order_no(value: Any) -> str | None:
    raw = _clean_text(value)
    if not raw or is_internal_placeholder_order_no(raw):
        return None
    return raw


def dedupe_uninvoiced_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(entries) <= 1:
        return list(entries)

    groups: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        groups.setdefault(_merge_key(entry), []).append(entry)

    deduped: list[dict[str, Any]] = []
    for group_entries in groups.values():
        real_entries = [entry for entry in group_entries if _real_order_key(entry)]
        if real_entries:
            picked: dict[str, dict[str, Any]] = {}
            for entry in real_entries:
                key = _real_distinct_key(entry)
                current = picked.get(key)
                if current is None or _entry_rank(entry) > _entry_rank(current):
                    picked[key] = entry
            deduped.extend(picked.values())
            continue

        best = max(group_entries, key=_entry_rank)
        deduped.append(best)

    return deduped


def _payload(entry: dict[str, Any]) -> dict[str, Any]:
    payload = entry.get("payload")
    return payload if isinstance(payload, dict) else {}


def _item_identity_key(payload: dict[str, Any]) -> str:
    item_code = _clean_key(payload.get("item_code"))
    if item_code:
        return f"code:{item_code}"
    item_name = _clean_key(payload.get("item_name"))
    if item_name:
        return f"name:{item_name}"
    return ""


def _float_key(value: float | None) -> str:
    if value is None:
        return ""
    return f"{round(float(value), 6):.6f}"


def _is_zero_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and abs(float(value)) < 1e-9


def _real_order_key(entry: dict[str, Any]) -> str:
    payload = _payload(entry)
    raw = viewer_display_order_no(payload.get("customer_order_no"))
    return _clean_key(raw)


def _entry_rank(entry: dict[str, Any]) -> tuple[int, int, int, int, float, float]:
    payload = _payload(entry)
    last_changed_at = entry.get("last_changed_at")
    created_at = entry.get("created_at")
    last_changed_ts = last_changed_at.timestamp() if isinstance(last_changed_at, datetime) else 0.0
    created_ts = created_at.timestamp() if isinstance(created_at, datetime) else 0.0
    return (
        1 if _real_order_key(entry) else 0,
        1 if _clean_text(payload.get("record_id")) else 0,
        1 if _clean_text(payload.get("source_row")) else 0,
        1 if _clean_text(payload.get("entry_line_no")) else 0,
        last_changed_ts,
        created_ts,
    )


def _identity_strength(entry: dict[str, Any]) -> int:
    payload = _payload(entry)
    score = 0
    if _clean_key(payload.get("entry_line_no")):
        score += 1
    if _item_identity_key(payload):
        score += 1
    if _clean_key(payload.get("latest_outbound_date")):
        score += 1
    if _float_key(uninvoiced_amount_from_payload(payload, _clean_text(entry.get("message")))):
        score += 1
    if _float_key(_number_from_text(payload.get("uninvoiced_qty"))):
        score += 1
    return score


def _merge_key(entry: dict[str, Any]) -> str:
    payload = _payload(entry)
    customer = _clean_key(entry.get("customer"))
    entry_line_no = _clean_key(payload.get("entry_line_no"))
    item_key = _item_identity_key(payload)
    outbound_date = _clean_key(payload.get("latest_outbound_date"))
    amount_key = _float_key(uninvoiced_amount_from_payload(payload, _clean_text(entry.get("message"))))
    uninvoiced_qty_key = _float_key(_number_from_text(payload.get("uninvoiced_qty")))
    real_order_key = _real_order_key(entry)
    raw_order_key = _clean_key(payload.get("customer_order_no"))

    parts = [customer]
    if _identity_strength(entry) >= 2:
        parts.extend([entry_line_no, item_key, outbound_date, amount_key, uninvoiced_qty_key])
        return "|".join(parts)

    parts.extend([real_order_key or raw_order_key, entry_line_no, item_key, outbound_date, amount_key, uninvoiced_qty_key])
    return "|".join(parts)


def _real_distinct_key(entry: dict[str, Any]) -> str:
    payload = _payload(entry)
    customer = _clean_key(entry.get("customer"))
    order_key = _real_order_key(entry)
    entry_line_no = _clean_key(payload.get("entry_line_no"))
    item_key = _item_identity_key(payload)
    outbound_date = _clean_key(payload.get("latest_outbound_date"))
    amount_key = _float_key(uninvoiced_amount_from_payload(payload, _clean_text(entry.get("message"))))
    uninvoiced_qty_key = _float_key(_number_from_text(payload.get("uninvoiced_qty")))
    return "|".join([customer, order_key, entry_line_no, item_key, outbound_date, amount_key, uninvoiced_qty_key])
