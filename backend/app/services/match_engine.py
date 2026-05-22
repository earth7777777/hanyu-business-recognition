from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.services.normalize_service import get_core, parse_date
from app.services.order_governance import build_match_business_key, build_strict_identity_key


@dataclass
class RecordView:
    id: str
    document_type: str
    core: dict[str, Any]
    ext: dict[str, Any]
    created_at: datetime | None = None
    source_row: int = 0


def _norm_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().lower()


def _key_customer_order(core: dict[str, Any]) -> str:
    return _norm_text(core.get("customer_order_no") or core.get("contract_no"))


def _key_contract_or_order(core: dict[str, Any]) -> str:
    return _norm_text(core.get("contract_no") or core.get("customer_order_no"))


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_diff(base: float, other: float) -> float:
    if base == 0:
        return 0.0 if other == 0 else 1.0
    return abs(base - other) / abs(base)


def _score(order: RecordView, candidate: RecordView, template: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    oc = order.core
    cc = candidate.core
    score = 0.0
    explain: dict[str, Any] = {}

    customer_match = _norm_text(oc.get("customer")) == _norm_text(cc.get("customer"))
    if customer_match:
        score += 3.0
    explain["customer_match"] = customer_match

    contract_match = _key_contract_or_order(oc) != "" and _key_contract_or_order(oc) == _key_contract_or_order(cc)
    if contract_match:
        score += 3.0
    explain["contract_or_order_match"] = contract_match

    item_code_match = _norm_text(oc.get("item_code")) != "" and _norm_text(oc.get("item_code")) == _norm_text(
        cc.get("item_code")
    )
    item_name_match = _norm_text(oc.get("item_name")) != "" and _norm_text(oc.get("item_name")) == _norm_text(
        cc.get("item_name")
    )
    if item_code_match:
        score += 2.0
    elif item_name_match:
        score += 1.2
    explain["item_code_match"] = item_code_match
    explain["item_name_match"] = item_name_match

    qty_tol = float(template.get("quantity_tolerance_pct", 0.05))
    ord_qty = _to_float(oc.get("quantity"))
    cand_qty = _to_float(cc.get("quantity"))
    quantity_match = None
    quantity_failed = False
    if ord_qty is not None and cand_qty is not None:
        quantity_match = _pct_diff(ord_qty, cand_qty) <= qty_tol
        if quantity_match:
            score += 2.0
        else:
            score -= 5.0
            quantity_failed = True
    explain["quantity_match"] = quantity_match

    amount_tol = float(template.get("amount_tolerance_pct", 0.05))
    ord_amount = _to_float(oc.get("amount"))
    cand_amount = _to_float(cc.get("amount"))
    amount_match = None
    if not quantity_failed and ord_amount is not None and cand_amount is not None:
        amount_match = _pct_diff(ord_amount, cand_amount) <= amount_tol
        if amount_match:
            score += 1.0
    explain["amount_match"] = amount_match

    date_tol = int(template.get("date_tolerance_days", 7))
    odate = parse_date(oc.get("biz_date") or oc.get("due_date"))
    cdate = parse_date(cc.get("biz_date") or cc.get("ship_date") or cc.get("invoice_date") or cc.get("notice_date"))
    date_match = None
    if odate and cdate:
        date_match = abs((odate - cdate).days) <= date_tol
        if date_match:
            score += 0.5
    explain["date_match"] = date_match

    return score, explain


def _base_signature(core: dict[str, Any]) -> str:
    return "|".join(
        [
            _norm_text(core.get("customer")),
            _key_contract_or_order(core),
            _norm_text(core.get("item_code")) or _norm_text(core.get("item_name")),
            _norm_text(core.get("biz_date") or core.get("due_date") or core.get("ship_date")),
        ]
    )


def _order_business_key(core: dict[str, Any]) -> str:
    key, _ = build_match_business_key(core)
    return key


def _stable_group_key(core: dict[str, Any], fallback_id: str) -> str:
    raw = _order_business_key(core)
    if not raw:
        return f"order:{fallback_id}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"order-line:{digest}"


def _winner_date(core: dict[str, Any]) -> int:
    winner_date = (
        parse_date(core.get("biz_date"))
        or parse_date(core.get("due_date"))
        or parse_date(core.get("latest_outbound_date"))
    )
    return winner_date.toordinal() if winner_date else -1


def _created_at_ordinal(value: datetime | None) -> float:
    if not isinstance(value, datetime):
        return float("-inf")
    return value.timestamp()


def _record_rank(rec: RecordView) -> tuple[int, float, int, str]:
    return (
        _winner_date(rec.core),
        _created_at_ordinal(rec.created_at),
        int(rec.source_row or 0),
        rec.id,
    )


def _pick_latest_order(records: list[RecordView]) -> RecordView | None:
    if not records:
        return None
    return max(records, key=_record_rank)


def _line_key_parts(rec: RecordView) -> tuple[str, str]:
    core = rec.core
    strict_identity = build_strict_identity_key(core)
    if strict_identity:
        return strict_identity, "high"

    customer_key = _norm_text(core.get("customer"))
    order_key = _key_customer_order(core)
    item_code = _norm_text(core.get("item_code"))
    item_name = _norm_text(core.get("item_name"))
    if item_code:
        raw = f"{customer_key}|{order_key}|code:{item_code}"
        return raw, "high"
    if item_name:
        raw = f"{customer_key}|{order_key}|name:{item_name}"
        return raw, "high"
    raw = f"{customer_key}|{order_key}|row:{int(rec.source_row or 0)}"
    return raw, "low"


def _line_key(rec: RecordView) -> tuple[str, str]:
    raw, confidence = _line_key_parts(rec)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
    return f"line:{digest}", confidence


def _build_line_candidate(rec: RecordView) -> dict[str, Any]:
    core = rec.core
    ext = rec.ext
    due_date = parse_date(core.get("due_date"))
    latest_outbound_date = parse_date(core.get("latest_outbound_date"))
    line_key, confidence = _line_key(rec)
    return {
        "record_id": rec.id,
        "source_row": int(rec.source_row or 0),
        "line_key": line_key,
        "line_key_confidence": confidence,
        "scan_state": core.get("scan_state"),
        "customer": str(core.get("customer") or ""),
        "customer_order_no": core.get("customer_order_no"),
        "entry_line_no": core.get("entry_line_no"),
        "item_code": core.get("item_code"),
        "item_name": core.get("item_name"),
        "quantity": _to_float(core.get("quantity")),
        "amount": _to_float(core.get("amount")),
        "order_total_amount": _to_float(core.get("order_total_amount")),
        "tax_inclusive_unit_price": _to_float(core.get("tax_inclusive_unit_price")),
        "due_date": due_date.isoformat() if due_date else None,
        "executed_shipped_qty": _to_float(core.get("executed_shipped_qty")),
        "latest_outbound_date": latest_outbound_date.isoformat() if latest_outbound_date else None,
        "order_outbound_status": core.get("order_outbound_status"),
        "line_outbound_status": core.get("line_outbound_status"),
        "order_outbound_status_raw": ext.get("order_outbound_status_raw"),
        "line_outbound_status_raw": ext.get("line_outbound_status_raw"),
        "invoiced_qty": _to_float(core.get("invoiced_qty")),
        "uninvoiced_qty": _to_float(core.get("uninvoiced_qty")),
        "line_invoice_status": core.get("line_invoice_status"),
        "order_closed": core.get("order_closed"),
        "line_closed": core.get("line_closed"),
        "latest_record_basis": "biz_date>created_at>source_row",
    }


def run_match(records: list[Any], template: dict[str, Any]) -> list[dict[str, Any]]:
    views = [
        RecordView(
            id=r.id,
            document_type=r.document_type,
            core=get_core(r.payload_json),
            ext=(
                r.payload_json.get("ext", {})
                if isinstance(r.payload_json, dict) and isinstance(r.payload_json.get("ext"), dict)
                else {}
            ),
            created_at=getattr(r, "created_at", None),
            source_row=int(getattr(r, "source_row", 0) or 0),
        )
        for r in records
    ]

    orders = [r for r in views if r.document_type == "order"]
    if not orders:
        groups = []
        for rec in views:
            groups.append(
                {
                    "group_key": f"unanchored:{rec.id}",
                    "anchor_record_id": rec.id,
                    "signature": _base_signature(rec.core),
                    "records": {
                        "order": [],
                        "shipment": [],
                        "payment_notice": [],
                        "invoice": [],
                        rec.document_type: [rec.id],
                    },
                    "scores": {},
                    "aggregate": _aggregate([rec]),
                }
            )
        return groups

    groups: dict[str, dict[str, Any]] = {}
    anchors: dict[str, RecordView] = {}
    orders_by_business_key: dict[str, list[RecordView]] = {}
    for order in orders:
        business_key = _order_business_key(order.core) or f"order:{order.id}"
        orders_by_business_key.setdefault(business_key, []).append(order)

    for grouped_orders in orders_by_business_key.values():
        winner = _pick_latest_order(grouped_orders)
        if not winner:
            continue
        key = _stable_group_key(winner.core, winner.id)
        anchors[key] = winner
        groups[key] = {
            "group_key": key,
            "anchor_record_id": winner.id,
            "signature": _base_signature(winner.core),
            "records": {
                "order": [order.id for order in grouped_orders],
                "shipment": [],
                "payment_notice": [],
                "invoice": [],
            },
            "scores": {},
            "aggregate": {},
        }

    threshold = 4.0
    for rec in views:
        if rec.document_type == "order":
            continue

        best_key = None
        best_score = -999.0
        best_explain = None
        for key, anchor in anchors.items():
            s, explain = _score(anchor, rec, template)
            if s > best_score:
                best_score = s
                best_key = key
                best_explain = explain

        if best_key and best_score >= threshold:
            groups[best_key]["records"][rec.document_type].append(rec.id)
            groups[best_key]["scores"][rec.id] = {"score": round(best_score, 3), "detail": best_explain}
        else:
            orphan_key = f"unmatched:{rec.id}"
            groups[orphan_key] = {
                "group_key": orphan_key,
                "anchor_record_id": rec.id,
                "signature": _base_signature(rec.core),
                "records": {
                    "order": [],
                    "shipment": [],
                    "payment_notice": [],
                    "invoice": [],
                    rec.document_type: [rec.id],
                },
                "scores": {},
                "aggregate": {},
            }

    by_id = {r.id: r for r in views}
    out: list[dict[str, Any]] = []
    for group in groups.values():
        recs = []
        for ids in group["records"].values():
            for rid in ids:
                if rid in by_id:
                    recs.append(by_id[rid])
        group["aggregate"] = _aggregate(recs)
        out.append(group)

    return out


def _aggregate(records: list[RecordView]) -> dict[str, Any]:
    orders = [rec for rec in records if rec.document_type == "order"]
    winner = _pick_latest_order(orders)

    order_qty = 0.0
    shipped_qty = 0.0
    order_amount = 0.0
    invoice_amount = 0.0
    customer = ""
    payment_notice_count = 0
    formal_invoice = False
    latest_ship_date: date | None = None
    due_date: date | None = None

    line_candidates_by_key: dict[str, tuple[tuple[int, float, int, str], dict[str, Any]]] = {}

    for rec in records:
        core = rec.core
        customer = customer or str(core.get("customer") or "")
        qty = _to_float(core.get("quantity")) or 0.0
        amount = _to_float(core.get("amount")) or 0.0

        if rec.document_type == "order":
            order_qty += qty
            order_amount += amount
            dd = parse_date(core.get("due_date"))
            if dd and (due_date is None or dd < due_date):
                due_date = dd

            candidate = _build_line_candidate(rec)
            key = str(candidate.get("line_key") or "")
            rank = _record_rank(rec)
            prev = line_candidates_by_key.get(key)
            if not prev or rank > prev[0]:
                line_candidates_by_key[key] = (rank, candidate)
        elif rec.document_type == "shipment":
            shipped_qty += qty
            sd = parse_date(core.get("ship_date"))
            if sd and (latest_ship_date is None or sd > latest_ship_date):
                latest_ship_date = sd
        elif rec.document_type == "payment_notice":
            payment_notice_count += 1
        elif rec.document_type == "invoice":
            invoice_amount += amount
            if core.get("invoice_formal") is True:
                formal_invoice = True

    line_candidates = [item[1] for item in line_candidates_by_key.values()]
    line_candidates.sort(key=lambda x: (int(x.get("source_row") or 0), str(x.get("record_id") or "")))

    winner_core = winner.core if winner else {}
    winner_ext = winner.ext if winner else {}
    quantity = _to_float(winner_core.get("quantity"))
    amount = _to_float(winner_core.get("amount"))
    tax_inclusive_unit_price = _to_float(winner_core.get("tax_inclusive_unit_price"))
    executed_shipped_qty = _to_float(winner_core.get("executed_shipped_qty"))
    invoiced_qty = _to_float(winner_core.get("invoiced_qty"))
    uninvoiced_qty = _to_float(winner_core.get("uninvoiced_qty"))
    latest_outbound_date = parse_date(winner_core.get("latest_outbound_date"))
    winner_due_date = parse_date(winner_core.get("due_date"))

    return {
        "customer": str(winner_core.get("customer") or customer or ""),
        "winner_record_id": winner.id if winner else None,
        "entry_line_no": winner_core.get("entry_line_no"),
        "quantity": quantity,
        "amount": amount,
        "tax_inclusive_unit_price": tax_inclusive_unit_price,
        "due_date": winner_due_date.isoformat() if winner_due_date else due_date.isoformat() if due_date else None,
        "executed_shipped_qty": executed_shipped_qty,
        "latest_outbound_date": latest_outbound_date.isoformat() if latest_outbound_date else None,
        "order_outbound_status": winner_core.get("order_outbound_status"),
        "line_outbound_status": winner_core.get("line_outbound_status"),
        "order_outbound_status_raw": winner_ext.get("order_outbound_status_raw"),
        "line_outbound_status_raw": winner_ext.get("line_outbound_status_raw"),
        "invoiced_qty": invoiced_qty,
        "uninvoiced_qty": uninvoiced_qty,
        "order_closed": winner_core.get("order_closed"),
        "line_closed": winner_core.get("line_closed"),
        "line_invoice_status": winner_core.get("line_invoice_status"),
        "scan_state": winner_core.get("scan_state"),
        "line_candidates": line_candidates,
        "line_candidate_count": len(line_candidates),
        "order_qty": round(order_qty, 6),
        "shipped_qty": round(shipped_qty, 6),
        "order_amount": round(order_amount, 6),
        "invoice_amount": round(invoice_amount, 6),
        "payment_notice_count": payment_notice_count,
        "formal_invoice": formal_invoice,
        "latest_ship_date": latest_ship_date.isoformat() if latest_ship_date else None,
        "latest_record_basis": "biz_date>created_at>source_row",
    }
