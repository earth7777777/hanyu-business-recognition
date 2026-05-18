from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.services.alert_engine import RULE_DUE_BEFORE_SHIP, RULE_SHIP_AFTER_NO_FINANCE, run_alerts
from app.services.match_engine import run_match


@dataclass
class FakeRecord:
    id: str
    document_type: str
    payload_json: dict
    created_at: datetime | None = None
    source_row: int = 0


TEMPLATE = {
    "quantity_tolerance_pct": 0.05,
    "amount_tolerance_pct": 0.05,
    "date_tolerance_days": 7,
}


def _payload(core: dict) -> dict:
    return {"core": core, "ext": {}, "attachments": {}, "trace": {}}


def _line(
    *,
    record_id: str,
    source_row: int,
    line_key: str,
    customer: str,
    quantity: float | None,
    executed_shipped_qty: float | None,
    scan_state: str | None = None,
    customer_order_no: str | None = None,
    item_code: str | None = None,
    item_name: str | None = None,
    amount: float | None = None,
    due_date: str | None = None,
    latest_outbound_date: str | None = None,
    uninvoiced_qty: float | None = None,
    order_closed: bool | None = None,
    line_closed: bool | None = None,
) -> dict:
    return {
        "record_id": record_id,
        "source_row": source_row,
        "line_key": line_key,
        "line_key_confidence": "high",
        "scan_state": scan_state,
        "customer": customer,
        "customer_order_no": customer_order_no,
        "item_code": item_code,
        "item_name": item_name,
        "quantity": quantity,
        "amount": amount,
        "executed_shipped_qty": executed_shipped_qty,
        "due_date": due_date,
        "latest_outbound_date": latest_outbound_date,
        "uninvoiced_qty": uninvoiced_qty,
        "order_closed": order_closed,
        "line_closed": line_closed,
    }


def test_quantity_primary_amount_auxiliary():
    order = FakeRecord(
        id="o1",
        document_type="order",
        payload_json=_payload(
            {
                "customer": "A",
                "contract_no": "C-1",
                "item_code": "I-1",
                "item_name": "item",
                "quantity": 100,
                "amount": 1000,
                "biz_date": "2026-03-10",
            }
        ),
    )
    shipment_good = FakeRecord(
        id="s1",
        document_type="shipment",
        payload_json=_payload(
            {
                "customer": "A",
                "contract_no": "C-1",
                "item_code": "I-1",
                "item_name": "item",
                "quantity": 100,
                "amount": 1100,
                "ship_date": "2026-03-11",
            }
        ),
    )
    shipment_bad_qty = FakeRecord(
        id="s2",
        document_type="shipment",
        payload_json=_payload(
            {
                "customer": "A",
                "contract_no": "C-1",
                "item_code": "I-1",
                "item_name": "item",
                "quantity": 50,
                "amount": 1000,
                "ship_date": "2026-03-11",
            }
        ),
    )

    groups = run_match([order, shipment_good, shipment_bad_qty], TEMPLATE)

    anchor_group = [g for g in groups if g.get("anchor_record_id") == "o1"][0]
    assert "s1" in anchor_group["records"]["shipment"]
    assert "s2" not in anchor_group["records"]["shipment"]


def test_run_match_prefers_latest_order_numeric_fields_by_business_key():
    older = FakeRecord(
        id="o-old",
        document_type="order",
        created_at=datetime(2026, 3, 10, 8, 0, tzinfo=timezone.utc),
        payload_json=_payload(
            {
                "customer": "A",
                "contract_no": "C-2",
                "customer_order_no": "SO-2",
                "item_code": "I-2",
                "item_name": "item-2",
                "quantity": 100,
                "amount": 1000,
                "biz_date": "2026-03-10",
                "due_date": "2026-03-25",
                "executed_shipped_qty": 20,
                "latest_outbound_date": "2026-01-20",
                "uninvoiced_qty": 20,
                "order_closed": True,
                "line_closed": True,
            }
        ),
    )
    newer = FakeRecord(
        id="o-new",
        document_type="order",
        created_at=datetime(2026, 3, 11, 9, 0, tzinfo=timezone.utc),
        payload_json=_payload(
            {
                "customer": "A",
                "contract_no": "C-2",
                "customer_order_no": "SO-2",
                "item_code": "I-2",
                "item_name": "item-2",
                "quantity": 100,
                "amount": 1000,
                "biz_date": "2026-03-11",
                "due_date": "2026-03-26",
                "executed_shipped_qty": 100,
                "latest_outbound_date": "2026-03-15",
                "uninvoiced_qty": 0,
                "order_closed": False,
                "line_closed": False,
            }
        ),
    )

    groups = run_match([older, newer], TEMPLATE)

    assert len(groups) == 1
    group = groups[0]
    assert group["anchor_record_id"] == "o-new"
    assert set(group["records"]["order"]) == {"o-old", "o-new"}
    assert group["aggregate"]["winner_record_id"] == "o-new"
    assert group["aggregate"]["quantity"] == 100.0
    assert group["aggregate"]["executed_shipped_qty"] == 100.0
    assert group["aggregate"]["uninvoiced_qty"] == 0.0
    assert group["aggregate"]["order_closed"] is False
    assert group["aggregate"]["line_closed"] is False


def test_run_match_uses_entry_line_no_as_primary_line_identity():
    older = FakeRecord(
        id="o-entry-old",
        document_type="order",
        created_at=datetime(2026, 3, 10, 8, 0, tzinfo=timezone.utc),
        payload_json=_payload(
            {
                "customer": "A",
                "contract_no": "C-3",
                "customer_order_no": "SO-3",
                "entry_line_no": "1",
                "item_code": "I-SAME",
                "item_name": "item-same",
                "quantity": 100,
                "amount": 1000,
                "biz_date": "2026-03-10",
                "due_date": "2026-03-25",
                "executed_shipped_qty": 20,
            }
        ),
    )
    newer = FakeRecord(
        id="o-entry-new",
        document_type="order",
        created_at=datetime(2026, 3, 11, 9, 0, tzinfo=timezone.utc),
        payload_json=_payload(
            {
                "customer": "A",
                "contract_no": "C-3",
                "customer_order_no": "SO-3",
                "entry_line_no": "1",
                "item_code": "I-SAME",
                "item_name": "item-same",
                "quantity": 100,
                "amount": 1000,
                "biz_date": "2026-03-10",
                "due_date": "2026-03-26",
                "executed_shipped_qty": 100,
            }
        ),
    )

    groups = run_match([older, newer], TEMPLATE)

    assert len(groups) == 1
    group = groups[0]
    assert set(group["records"]["order"]) == {"o-entry-old", "o-entry-new"}
    candidate = group["aggregate"]["line_candidates"][0]
    assert candidate["entry_line_no"] == "1"
    assert candidate["record_id"] == "o-entry-new"


def test_run_match_entry_line_no_still_requires_aux_match():
    older = FakeRecord(
        id="o-entry-aux-old",
        document_type="order",
        created_at=datetime(2026, 3, 10, 8, 0, tzinfo=timezone.utc),
        payload_json=_payload(
            {
                "customer": "A",
                "contract_no": "C-3A",
                "customer_order_no": "SO-3A",
                "entry_line_no": "7",
                "item_code": "I-OLD",
                "item_name": "item-old",
                "quantity": 100,
                "amount": 1000,
                "biz_date": "2026-03-10",
                "due_date": "2026-03-25",
                "executed_shipped_qty": 20,
            }
        ),
    )
    newer = FakeRecord(
        id="o-entry-aux-new",
        document_type="order",
        created_at=datetime(2026, 3, 11, 9, 0, tzinfo=timezone.utc),
        payload_json=_payload(
            {
                "customer": "A",
                "contract_no": "C-3A",
                "customer_order_no": "SO-3A",
                "entry_line_no": "7",
                "item_code": "I-NEW",
                "item_name": "item-new",
                "quantity": 100,
                "amount": 1000,
                "biz_date": "2026-03-10",
                "due_date": "2026-03-26",
                "executed_shipped_qty": 100,
            }
        ),
    )

    groups = run_match([older, newer], TEMPLATE)

    assert len(groups) == 2


def test_run_match_falls_back_without_entry_line_no():
    line1 = FakeRecord(
        id="o-no-entry-1",
        document_type="order",
        created_at=datetime(2026, 3, 10, 8, 0, tzinfo=timezone.utc),
        payload_json=_payload(
            {
                "customer": "A",
                "contract_no": "C-4",
                "customer_order_no": "SO-4",
                "item_code": "I-1",
                "item_name": "item-1",
                "quantity": 100,
                "amount": 1000,
                "biz_date": "2026-03-10",
            }
        ),
    )
    line2 = FakeRecord(
        id="o-no-entry-2",
        document_type="order",
        created_at=datetime(2026, 3, 10, 9, 0, tzinfo=timezone.utc),
        payload_json=_payload(
            {
                "customer": "A",
                "contract_no": "C-4",
                "customer_order_no": "SO-4",
                "item_code": "I-2",
                "item_name": "item-2",
                "quantity": 100,
                "amount": 1000,
                "biz_date": "2026-03-10",
            }
        ),
    )

    groups = run_match([line1, line2], TEMPLATE)

    assert len(groups) == 2


def test_two_alert_rules_use_order_numeric_fields():
    today = date(2026, 3, 19)
    groups = [
        {
            "group_key": "g1",
            "aggregate": {
                "line_candidates": [
                    _line(
                        record_id="r1",
                        source_row=1,
                        line_key="line:r1",
                        customer="A",
                        quantity=100,
                        executed_shipped_qty=80,
                        due_date=(today + timedelta(days=2)).isoformat(),
                        order_closed=True,
                        line_closed=True,
                    )
                ],
            },
        },
        {
            "group_key": "g2",
            "aggregate": {
                "line_candidates": [
                    _line(
                        record_id="r2",
                        source_row=2,
                        line_key="line:r2",
                        customer="B",
                        quantity=100,
                        executed_shipped_qty=20,
                        latest_outbound_date=(today - timedelta(days=60)).isoformat(),
                        uninvoiced_qty=20,
                        order_closed=True,
                        line_closed=True,
                    )
                ],
            },
        },
    ]
    rules = {
        "enabled": {"due_before_ship": True, "ship_after_no_finance": True},
        "due_before_ship_days": 5,
        "ship_after_no_finance_days": 60,
    }

    alerts = run_alerts(groups, rules, today=today)
    types = [a["alert_type"] for a in alerts]
    assert RULE_DUE_BEFORE_SHIP in types
    assert RULE_SHIP_AFTER_NO_FINANCE in types


def test_ship_after_no_finance_uses_60_day_threshold_and_order_digits():
    today = date(2026, 3, 23)
    groups = [
        {
            "group_key": "g59",
            "aggregate": {
                "line_candidates": [
                    _line(
                        record_id="r59",
                        source_row=1,
                        line_key="line:r59",
                        customer="A",
                        quantity=100,
                        executed_shipped_qty=20,
                        latest_outbound_date=(today - timedelta(days=59)).isoformat(),
                        uninvoiced_qty=20,
                    )
                ],
            },
        },
        {
            "group_key": "g60",
            "aggregate": {
                "line_candidates": [
                    _line(
                        record_id="r60",
                        source_row=2,
                        line_key="line:r60",
                        customer="B",
                        quantity=100,
                        executed_shipped_qty=20,
                        latest_outbound_date=(today - timedelta(days=60)).isoformat(),
                        uninvoiced_qty=20,
                    )
                ],
            },
        },
        {
            "group_key": "g0",
            "aggregate": {
                "line_candidates": [
                    _line(
                        record_id="r0",
                        source_row=3,
                        line_key="line:r0",
                        customer="C",
                        quantity=100,
                        executed_shipped_qty=0,
                        latest_outbound_date=(today - timedelta(days=80)).isoformat(),
                        uninvoiced_qty=20,
                    )
                ],
            },
        },
        {
            "group_key": "gdone",
            "aggregate": {
                "line_candidates": [
                    _line(
                        record_id="rdone",
                        source_row=4,
                        line_key="line:rdone",
                        customer="D",
                        quantity=100,
                        executed_shipped_qty=20,
                        latest_outbound_date=(today - timedelta(days=80)).isoformat(),
                        uninvoiced_qty=0,
                    )
                ],
            },
        },
    ]
    rules = {
        "enabled": {"due_before_ship": False, "ship_after_no_finance": True},
        "due_before_ship_days": 5,
        "ship_after_no_finance_days": 60,
    }

    alerts = run_alerts(groups, rules, today=today)
    assert len(alerts) == 1
    assert alerts[0]["alert_type"] == RULE_SHIP_AFTER_NO_FINANCE
    assert alerts[0]["group_key"] == "g60"


def test_completed_skip_scan_lines_do_not_enter_alerts():
    today = date(2026, 3, 23)
    groups = [
        {
            "group_key": "g-due-skip",
            "aggregate": {
                "line_candidates": [
                    _line(
                        record_id="r-due-skip",
                        source_row=1,
                        line_key="line:r-due-skip",
                        customer="A",
                        quantity=100,
                        executed_shipped_qty=80,
                        due_date=(today + timedelta(days=1)).isoformat(),
                        scan_state="completed_skip_scan",
                    )
                ],
            },
        },
        {
            "group_key": "g-invoice-skip",
            "aggregate": {
                "line_candidates": [
                    _line(
                        record_id="r-invoice-skip",
                        source_row=2,
                        line_key="line:r-invoice-skip",
                        customer="B",
                        quantity=100,
                        executed_shipped_qty=20,
                        latest_outbound_date=(today - timedelta(days=80)).isoformat(),
                        uninvoiced_qty=20,
                        scan_state="completed_skip_scan",
                    )
                ],
            },
        },
    ]
    rules = {
        "enabled": {"due_before_ship": True, "ship_after_no_finance": True},
        "due_before_ship_days": 5,
        "ship_after_no_finance_days": 60,
    }

    alerts = run_alerts(groups, rules, today=today)
    assert alerts == []


def test_due_before_ship_treats_blank_executed_qty_as_zero():
    today = date(2026, 3, 30)
    groups = [
        {
            "group_key": "g-due-blank-executed",
            "aggregate": {
                "line_candidates": [
                    _line(
                        record_id="r-due-blank-executed",
                        source_row=1,
                        line_key="line:r-due-blank-executed",
                        customer="A",
                        customer_order_no="SO-BLANK-EXEC",
                        item_code="ITEM-BLANK-EXEC",
                        quantity=100,
                        executed_shipped_qty=None,
                        due_date=(today + timedelta(days=3)).isoformat(),
                    )
                ],
            },
        }
    ]
    rules = {
        "enabled": {"due_before_ship": True, "ship_after_no_finance": False},
        "due_before_ship_days": 5,
        "ship_after_no_finance_days": 60,
    }

    alerts = run_alerts(groups, rules, today=today)
    assert len(alerts) == 1
    assert alerts[0]["alert_type"] == RULE_DUE_BEFORE_SHIP
    assert alerts[0]["payload"]["executed_shipped_qty"] == 0.0


def test_ship_after_no_finance_treats_blank_uninvoiced_qty_as_zero_and_skips_alert():
    today = date(2026, 3, 30)
    groups = [
        {
            "group_key": "g-invoice-blank-uninvoiced",
            "aggregate": {
                "line_candidates": [
                    _line(
                        record_id="r-invoice-blank-uninvoiced",
                        source_row=1,
                        line_key="line:r-invoice-blank-uninvoiced",
                        customer="A",
                        customer_order_no="SO-BLANK-UNINV",
                        item_code="ITEM-BLANK-UNINV",
                        quantity=100,
                        executed_shipped_qty=10,
                        latest_outbound_date=(today - timedelta(days=80)).isoformat(),
                        uninvoiced_qty=None,
                    )
                ],
            },
        }
    ]
    rules = {
        "enabled": {"due_before_ship": False, "ship_after_no_finance": True},
        "due_before_ship_days": 5,
        "ship_after_no_finance_days": 60,
    }

    alerts = run_alerts(groups, rules, today=today)
    assert alerts == []


def test_closed_status_is_display_only_for_alert_rules():
    today = date(2026, 3, 23)
    groups = [
        {
            "group_key": "due-closed",
            "aggregate": {
                "line_candidates": [
                    _line(
                        record_id="due-closed",
                        source_row=1,
                        line_key="line:due-closed",
                        customer="A",
                        quantity=100,
                        executed_shipped_qty=80,
                        due_date=(today + timedelta(days=1)).isoformat(),
                        order_closed=True,
                        line_closed=True,
                    )
                ],
            },
        },
        {
            "group_key": "invoice-closed",
            "aggregate": {
                "line_candidates": [
                    _line(
                        record_id="invoice-closed",
                        source_row=2,
                        line_key="line:invoice-closed",
                        customer="B",
                        quantity=100,
                        executed_shipped_qty=20,
                        latest_outbound_date=(today - timedelta(days=60)).isoformat(),
                        uninvoiced_qty=20,
                        order_closed=True,
                        line_closed=True,
                    )
                ],
            },
        },
    ]
    rules = {
        "enabled": {"due_before_ship": True, "ship_after_no_finance": True},
        "due_before_ship_days": 5,
        "ship_after_no_finance_days": 60,
    }

    alerts = run_alerts(groups, rules, today=today)
    assert {item["group_key"] for item in alerts} == {"due-closed", "invoice-closed"}


def test_three_order_lines_must_not_be_overwritten_by_single_winner():
    today = date(2026, 3, 23)
    groups = [
        {
            "group_key": "same-order",
            "aggregate": {
                "line_candidates": [
                    _line(
                        record_id="row1",
                        source_row=1,
                        line_key="line:row1",
                        customer="安吉热威",
                        quantity=81,
                        executed_shipped_qty=81,
                        latest_outbound_date="2025-08-23",
                        uninvoiced_qty=81,
                    ),
                    _line(
                        record_id="row2",
                        source_row=2,
                        line_key="line:row2",
                        customer="安吉热威",
                        quantity=3600,
                        executed_shipped_qty=3600,
                        latest_outbound_date="2025-08-16",
                        uninvoiced_qty=478,
                    ),
                    _line(
                        record_id="row3",
                        source_row=3,
                        line_key="line:row3",
                        customer="安吉热威",
                        quantity=45,
                        executed_shipped_qty=45,
                        latest_outbound_date="2025-08-23",
                        uninvoiced_qty=None,
                    ),
                ],
            },
        }
    ]
    rules = {
        "enabled": {"due_before_ship": False, "ship_after_no_finance": True},
        "due_before_ship_days": 5,
        "ship_after_no_finance_days": 60,
    }

    alerts = run_alerts(groups, rules, today=today)
    assert len(alerts) == 2
    source_rows = {a["payload"]["source_row"] for a in alerts}
    assert source_rows == {1, 2}
    assert all(a["payload"]["record_id"] in {"row1", "row2"} for a in alerts)


def test_humanized_messages_include_short_and_long_versions():
    today = date(2026, 3, 23)
    groups = [
        {
            "group_key": "invoice-with-amount",
            "aggregate": {
                "line_candidates": [
                    _line(
                        record_id="r-invoice-1",
                        source_row=1,
                        line_key="line:invoice-1",
                        customer="A客户",
                        customer_order_no="SO-1001",
                        item_code="ITEM-001",
                        item_name="齿轮泵",
                        quantity=10,
                        amount=12600.5,
                        executed_shipped_qty=10,
                        latest_outbound_date=(today - timedelta(days=60)).isoformat(),
                        uninvoiced_qty=3,
                    )
                ],
            },
        },
        {
            "group_key": "invoice-no-amount",
            "aggregate": {
                "line_candidates": [
                    _line(
                        record_id="r-invoice-2",
                        source_row=2,
                        line_key="line:invoice-2",
                        customer="B客户",
                        customer_order_no="SO-1002",
                        item_code="ITEM-002",
                        item_name="阀体",
                        quantity=28,
                        amount=None,
                        executed_shipped_qty=8,
                        latest_outbound_date=(today - timedelta(days=75)).isoformat(),
                        uninvoiced_qty=2,
                    )
                ],
            },
        },
        {
            "group_key": "due-message",
            "aggregate": {
                "line_candidates": [
                    _line(
                        record_id="r-due",
                        source_row=3,
                        line_key="line:due-1",
                        customer="C客户",
                        customer_order_no="SO-2001",
                        item_code="ITEM-003",
                        item_name="泵壳",
                        quantity=100,
                        executed_shipped_qty=60,
                        due_date=(today + timedelta(days=2)).isoformat(),
                    )
                ],
            },
        },
        {
            "group_key": "due-overdue",
            "aggregate": {
                "line_candidates": [
                    _line(
                        record_id="r-due-overdue",
                        source_row=4,
                        line_key="line:due-2",
                        customer="D客户",
                        customer_order_no="SO-2002",
                        item_code="ITEM-004",
                        item_name="壳体",
                        quantity=50,
                        executed_shipped_qty=10,
                        due_date=(today - timedelta(days=3)).isoformat(),
                    )
                ],
            },
        },
    ]
    rules = {
        "enabled": {"due_before_ship": True, "ship_after_no_finance": True},
        "due_before_ship_days": 5,
        "ship_after_no_finance_days": 60,
    }

    alerts = run_alerts(groups, rules, today=today)
    by_group = {a["group_key"]: a for a in alerts}

    invoice_with_amount = by_group["invoice-with-amount"]
    assert invoice_with_amount["alert_type"] == RULE_SHIP_AFTER_NO_FINANCE
    assert "距最近出库已〔60〕天" in invoice_with_amount["message"]
    assert "金额〔12600.5〕" in invoice_with_amount["message"]
    assert "当前未发货数量〔0〕" in invoice_with_amount["message"]
    assert invoice_with_amount["payload"]["order_unshipped_qty"] == 0.0
    assert "最近出库日期是〔2026-01-22〕" in invoice_with_amount["payload"]["message_long"]

    invoice_no_amount = by_group["invoice-no-amount"]
    assert invoice_no_amount["alert_type"] == RULE_SHIP_AFTER_NO_FINANCE
    assert "距最近出库已〔75〕天" in invoice_no_amount["message"]
    assert "金额〔" not in invoice_no_amount["message"]
    assert "当前未发货数量〔20〕" in invoice_no_amount["message"]
    assert invoice_no_amount["payload"]["order_unshipped_qty"] == 20.0
    assert "金额暂无" in invoice_no_amount["payload"]["message_long"]

    due_alert = by_group["due-message"]
    assert due_alert["alert_type"] == RULE_DUE_BEFORE_SHIP
    assert "距交期还有〔2〕天" in due_alert["message"]
    assert "应发〔100〕，已发〔60〕" in due_alert["message"]
    assert "未发〔40〕" in due_alert["payload"]["message_long"]

    due_overdue = by_group["due-overdue"]
    assert due_overdue["alert_type"] == RULE_DUE_BEFORE_SHIP
    assert "交期已过〔3〕天" in due_overdue["message"]
    assert "应发〔50〕，已发〔10〕" in due_overdue["message"]
    assert "交期已过〔3〕天" in due_overdue["payload"]["message_long"]
