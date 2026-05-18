from __future__ import annotations

import io
import time
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.db.models import Alert
from app.db.session import SessionLocal
from app.main import app


client = TestClient(app)


def _wait_for_task_done(task_id: str, headers: dict[str, str], timeout_sec: float = 5.0) -> str:
    deadline = time.time() + timeout_sec
    status = "queued"
    while time.time() < deadline:
        resp = client.get(f"/v1/tasks/{task_id}", headers=headers)
        assert resp.status_code == 200
        status = resp.json()["status"]
        if status in {"succeeded", "failed"}:
            return status
        time.sleep(0.1)
    return status


def _wait_for_alert_item(
    job_id: str,
    alert_type: str,
    headers: dict[str, str],
    expected_status: str | None = None,
    timeout_sec: float = 6.0,
):
    deadline = time.time() + timeout_sec
    latest = None
    while time.time() < deadline:
        resp = client.get(f"/v1/alerts?job_id={job_id}", headers=headers)
        assert resp.status_code == 200
        items = resp.json()
        for it in items:
            if it["alert_type"] == alert_type:
                latest = it
                if expected_status is None or it["status"] == expected_status:
                    return it
        time.sleep(0.15)
    return latest


def _count_open_alerts(job_id: str, alert_type: str, headers: dict[str, str]) -> int:
    resp = client.get(f"/v1/alerts?job_id={job_id}", headers=headers)
    assert resp.status_code == 200
    items = resp.json()
    return len([it for it in items if it["alert_type"] == alert_type and it["status"] == "open"])


def _build_order_csv(
    *,
    customer: str,
    contract_no: str,
    customer_order_no: str,
    entry_line_no: str | None = None,
    item_code: str,
    item_name: str,
    quantity: float,
    amount: float,
    order_date: str,
    due_date: str,
    latest_outbound_date: str,
    executed_shipped_qty: float,
    uninvoiced_qty: float,
) -> str:
    entry_line_text = "" if entry_line_no is None else entry_line_no
    return (
        "客户,合同号,客户订单号,分录行号,料号,品名,数量,金额,订单日期,交期,最近出库日期,行已执行已出库数量,行未开票数量\n"
        f"{customer},{contract_no},{customer_order_no},{entry_line_text},{item_code},{item_name},{quantity},{amount},"
        f"{order_date},{due_date},{latest_outbound_date},{executed_shipped_qty},{uninvoiced_qty}\n"
    )


def _create_job_and_upload_order(headers: dict[str, str], *, filename: str, csv_content: str) -> str:
    create_resp = client.post("/v1/upload-jobs", headers=headers)
    assert create_resp.status_code == 200
    job_id = create_resp.json()["id"]

    upload_resp = client.post(
        f"/v1/upload-jobs/{job_id}/files",
        headers=headers,
        data={"document_type": "order"},
        files={"upload": (filename, io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert upload_resp.status_code == 200
    assert upload_resp.json()["parse_status"] == "parsed"
    return job_id


def test_type_guard_blocks_high_confidence_mismatch():
    headers = {"X-Role": "admin"}
    create_resp = client.post("/v1/upload-jobs", headers=headers)
    assert create_resp.status_code == 200
    job_id = create_resp.json()["id"]

    # Strong shipment headers with explicit ship_date signal.
    csv_content = (
        "客户,合同号,客户订单号,料号,品名,发货数量,金额,发货日期\n"
        "A客户,HT-100,SO-100,ITEM-100,产品100,100,1000,2026-03-10\n"
    )
    resp = client.post(
        f"/v1/upload-jobs/{job_id}/files",
        headers=headers,
        data={"document_type": "order"},
        files={"upload": ("shipment_like.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert resp.status_code == 422
    body = resp.json().get("detail") or {}
    assert body.get("code") == "DOCUMENT_TYPE_MISMATCH"
    assert body.get("guard", {}).get("expected_type") == "order"
    assert body.get("guard", {}).get("detected_type") == "shipment"
    assert body.get("guard", {}).get("confidence") == "high"


def test_type_guard_blocks_high_confidence_mismatch_for_image(monkeypatch):
    headers = {"X-Role": "admin"}
    create_resp = client.post("/v1/upload-jobs", headers=headers)
    assert create_resp.status_code == 200
    job_id = create_resp.json()["id"]

    monkeypatch.setattr(
        "app.services.parsers.ocr_any",
        lambda p: (
            "客户: A客户\n合同号: HT-300\n订单号: SO-300\n"
            "料号: ITEM-300\n品名: 产品300\n发货数量: 100\n金额: 1000\n发货日期: 2026-03-19\n"
        ),
    )

    # OCR内容明显是发货单，但调用方故意声明为invoice。
    resp = client.post(
        f"/v1/upload-jobs/{job_id}/files",
        headers=headers,
        data={"document_type": "invoice"},
        files={"upload": ("shipment_like.png", io.BytesIO(b"fake"), "image/png")},
    )
    assert resp.status_code == 422
    body = resp.json().get("detail") or {}
    assert body.get("code") == "DOCUMENT_TYPE_MISMATCH"
    assert body.get("guard", {}).get("source") == "ocr"
    assert body.get("guard", {}).get("expected_type") == "invoice"
    assert body.get("guard", {}).get("detected_type") == "shipment"
    assert body.get("guard", {}).get("confidence") == "high"


def test_order_numeric_update_drives_alert_and_preserves_manual_override(monkeypatch):
    headers = {"X-Role": "admin"}

    current_rule = client.get("/v1/config/rule_parameters", headers=headers)
    assert current_rule.status_code == 200
    original_rule = current_rule.json()["value"]

    # Keep only ship_after_no_finance rule for deterministic assertion.
    rule_resp = client.put(
        "/v1/config/rule_parameters",
        headers=headers,
        json={
            "value": {
                "enabled": {
                    "due_before_ship": False,
                    "ship_after_no_finance": True,
                },
                "due_before_ship_days": 5,
                "ship_after_no_finance_days": 60,
            }
        },
    )
    assert rule_resp.status_code == 200

    try:
        create_resp = client.post("/v1/upload-jobs", headers=headers)
        assert create_resp.status_code == 200
        job_id = create_resp.json()["id"]

        outbound_date = (date.today() - timedelta(days=60)).isoformat()
        order_csv = (
            "客户,合同号,客户订单号,料号,品名,数量,金额,订单日期,交期,关闭状态,行关闭状态,最近出库日期,行已执行已出库数量,行未开票数量\n"
            f"A客户,HT-200,SO-200,ITEM-200,产品200,100,1000,2026-03-01,{(date.today() + timedelta(days=10)).isoformat()},已关闭,已关闭,{outbound_date},20,20\n"
        )

        r_order = client.post(
            f"/v1/upload-jobs/{job_id}/files",
            headers=headers,
            data={"document_type": "order"},
            files={"upload": ("order.csv", io.BytesIO(order_csv.encode("utf-8")), "text/csv")},
        )
        assert r_order.status_code == 200

        def _fake_ocr(path):
            return (
                "客户: A客户\n合同号: HT-200\n订单号: SO-200\n"
                "料号: ITEM-200\n品名: 产品200\n金额: 1000\n开票日期: 2026-03-19\n正式发票: 是\n"
            )

        monkeypatch.setattr("app.services.parsers.ocr_any", _fake_ocr)

        open_item = _wait_for_alert_item(job_id, "ship_after_no_finance", headers, expected_status="open")
        assert open_item is not None
        assert open_item["payload"]["order_closed"] is True
        assert open_item["payload"]["line_closed"] is True
        assert open_item["payload"]["record_id"]
        assert open_item["payload"]["source_row"] == 1
        assert str(open_item["payload"]["line_key"]).startswith("line:")
        assert "manual_override_state" in open_item["payload"]
        assert "manual_override_reason" in open_item["payload"]

        with SessionLocal() as db:
            alert = db.query(Alert).filter(Alert.job_id == job_id, Alert.alert_type == "ship_after_no_finance").one()
            payload = dict(alert.payload_json or {})
            payload["manual_override_state"] = "suppressed"
            payload["manual_override_by"] = "qa-admin"
            payload["manual_override_at"] = "2026-03-23T10:00:00+08:00"
            payload["manual_override_reason"] = "预留埋口验收"
            alert.payload_json = payload
            db.commit()

        # Invoice upload may trigger recompute, but it is not a hard judgment basis in this round.
        r_invoice = client.post(
            f"/v1/upload-jobs/{job_id}/files",
            headers=headers,
            data={"document_type": "invoice"},
            files={"upload": ("invoice.png", io.BytesIO(b"fake"), "image/png")},
        )
        assert r_invoice.status_code == 200
        assert r_invoice.json()["parse_status"] == "parsed"

        still_open = _wait_for_alert_item(job_id, "ship_after_no_finance", headers, expected_status="open")
        assert still_open is not None
        assert still_open["payload"]["manual_override_state"] == "suppressed"

        updated_order_csv = (
            "客户,合同号,客户订单号,料号,品名,数量,金额,订单日期,交期,关闭状态,行关闭状态,最近出库日期,行已执行已出库数量,行未开票数量\n"
            f"A客户,HT-200,SO-200,ITEM-200,产品200,100,1000,2026-03-02,{(date.today() + timedelta(days=10)).isoformat()},未关闭,未关闭,{outbound_date},20,0\n"
        )
        r_order_fix = client.post(
            f"/v1/upload-jobs/{job_id}/files",
            headers=headers,
            data={"document_type": "order"},
            files={"upload": ("order-update.csv", io.BytesIO(updated_order_csv.encode("utf-8")), "text/csv")},
        )
        assert r_order_fix.status_code == 200
        assert r_order_fix.json()["parse_status"] == "parsed"

        resolved_item = _wait_for_alert_item(job_id, "ship_after_no_finance", headers, expected_status="resolved")
        assert resolved_item is not None
        assert resolved_item["payload"]["manual_override_state"] == "suppressed"
        assert resolved_item["payload"]["manual_override_by"] == "qa-admin"
    finally:
        restore = client.put(
            "/v1/config/rule_parameters",
            headers=headers,
            json={"value": original_rule},
        )
        assert restore.status_code == 200


def test_cross_job_resolves_old_open_alert_when_new_job_no_longer_hits():
    headers = {"X-Role": "admin"}
    current_rule = client.get("/v1/config/rule_parameters", headers=headers)
    assert current_rule.status_code == 200
    original_rule = current_rule.json()["value"]

    rule_resp = client.put(
        "/v1/config/rule_parameters",
        headers=headers,
        json={
            "value": {
                "enabled": {
                    "due_before_ship": False,
                    "ship_after_no_finance": True,
                },
                "due_before_ship_days": 5,
                "ship_after_no_finance_days": 60,
            }
        },
    )
    assert rule_resp.status_code == 200

    try:
        today = date.today()
        outbound_date = (today - timedelta(days=60)).isoformat()
        due_date = (today + timedelta(days=7)).isoformat()

        csv_a = _build_order_csv(
            customer="A客户",
            contract_no="HT-301",
            customer_order_no="SO-301",
            entry_line_no="1",
            item_code="ITEM-301",
            item_name="产品301",
            quantity=100,
            amount=1000,
            order_date=today.isoformat(),
            due_date=due_date,
            latest_outbound_date=outbound_date,
            executed_shipped_qty=20,
            uninvoiced_qty=20,
        )
        job_a = _create_job_and_upload_order(headers, filename="order-a.csv", csv_content=csv_a)
        open_a = _wait_for_alert_item(job_a, "ship_after_no_finance", headers, expected_status="open")
        assert open_a is not None

        csv_b = _build_order_csv(
            customer="A客户",
            contract_no="HT-301",
            customer_order_no="SO-301",
            entry_line_no="1",
            item_code="ITEM-301",
            item_name="产品301",
            quantity=100,
            amount=1000,
            order_date=today.isoformat(),
            due_date=due_date,
            latest_outbound_date=outbound_date,
            executed_shipped_qty=20,
            uninvoiced_qty=0,
        )
        job_b = _create_job_and_upload_order(headers, filename="order-b.csv", csv_content=csv_b)

        resolved_a = _wait_for_alert_item(job_a, "ship_after_no_finance", headers, expected_status="resolved")
        assert resolved_a is not None
        payload_a = resolved_a.get("payload") or {}
        assert payload_a.get("resolved_task_id")
        assert payload_a.get("superseded_by_job_id") == job_b
        assert payload_a.get("resolution_reason") == "superseded_by_newer_job_line"
        assert str(payload_a.get("superseded_by_alert_key") or "").startswith("ship_after_no_finance|line:")

        assert _count_open_alerts(job_a, "ship_after_no_finance", headers) == 0
        assert _count_open_alerts(job_b, "ship_after_no_finance", headers) == 0
    finally:
        restore = client.put(
            "/v1/config/rule_parameters",
            headers=headers,
            json={"value": original_rule},
        )
        assert restore.status_code == 200


def test_cross_job_keeps_only_new_job_open_when_new_job_still_hits():
    headers = {"X-Role": "admin"}
    current_rule = client.get("/v1/config/rule_parameters", headers=headers)
    assert current_rule.status_code == 200
    original_rule = current_rule.json()["value"]

    rule_resp = client.put(
        "/v1/config/rule_parameters",
        headers=headers,
        json={
            "value": {
                "enabled": {
                    "due_before_ship": False,
                    "ship_after_no_finance": True,
                },
                "due_before_ship_days": 5,
                "ship_after_no_finance_days": 60,
            }
        },
    )
    assert rule_resp.status_code == 200

    try:
        today = date.today()
        outbound_date = (today - timedelta(days=60)).isoformat()
        due_date = (today + timedelta(days=7)).isoformat()

        csv_a = _build_order_csv(
            customer="B客户",
            contract_no="HT-302",
            customer_order_no="SO-302",
            entry_line_no="1",
            item_code="ITEM-302",
            item_name="产品302",
            quantity=120,
            amount=1500,
            order_date=today.isoformat(),
            due_date=due_date,
            latest_outbound_date=outbound_date,
            executed_shipped_qty=30,
            uninvoiced_qty=30,
        )
        job_a = _create_job_and_upload_order(headers, filename="order-a.csv", csv_content=csv_a)
        open_a = _wait_for_alert_item(job_a, "ship_after_no_finance", headers, expected_status="open")
        assert open_a is not None

        csv_b = _build_order_csv(
            customer="B客户",
            contract_no="HT-302",
            customer_order_no="SO-302",
            entry_line_no="1",
            item_code="ITEM-302",
            item_name="产品302",
            quantity=120,
            amount=1500,
            order_date=today.isoformat(),
            due_date=due_date,
            latest_outbound_date=outbound_date,
            executed_shipped_qty=30,
            uninvoiced_qty=10,
        )
        job_b = _create_job_and_upload_order(headers, filename="order-b.csv", csv_content=csv_b)

        open_b = _wait_for_alert_item(job_b, "ship_after_no_finance", headers, expected_status="open")
        assert open_b is not None

        resolved_a = _wait_for_alert_item(job_a, "ship_after_no_finance", headers, expected_status="resolved")
        assert resolved_a is not None
        payload_a = resolved_a.get("payload") or {}
        assert payload_a.get("resolved_task_id")
        assert payload_a.get("superseded_by_job_id") == job_b
        assert payload_a.get("resolution_reason") == "superseded_by_newer_job_line"

        assert _count_open_alerts(job_a, "ship_after_no_finance", headers) == 0
        assert _count_open_alerts(job_b, "ship_after_no_finance", headers) == 1
    finally:
        restore = client.put(
            "/v1/config/rule_parameters",
            headers=headers,
            json={"value": original_rule},
        )
        assert restore.status_code == 200


def test_cross_job_entry_line_no_still_requires_aux_match():
    headers = {"X-Role": "admin"}
    current_rule = client.get("/v1/config/rule_parameters", headers=headers)
    assert current_rule.status_code == 200
    original_rule = current_rule.json()["value"]

    rule_resp = client.put(
        "/v1/config/rule_parameters",
        headers=headers,
        json={
            "value": {
                "enabled": {
                    "due_before_ship": False,
                    "ship_after_no_finance": True,
                },
                "due_before_ship_days": 5,
                "ship_after_no_finance_days": 60,
            }
        },
    )
    assert rule_resp.status_code == 200

    try:
        today = date.today()
        outbound_date = (today - timedelta(days=60)).isoformat()
        due_date = (today + timedelta(days=7)).isoformat()

        csv_a = _build_order_csv(
            customer="C客户",
            contract_no="HT-303",
            customer_order_no="SO-303",
            entry_line_no="7",
            item_code="ITEM-OLD",
            item_name="旧产品名",
            quantity=120,
            amount=1500,
            order_date=today.isoformat(),
            due_date=due_date,
            latest_outbound_date=outbound_date,
            executed_shipped_qty=30,
            uninvoiced_qty=30,
        )
        job_a = _create_job_and_upload_order(headers, filename="order-a.csv", csv_content=csv_a)
        open_a = _wait_for_alert_item(job_a, "ship_after_no_finance", headers, expected_status="open")
        assert open_a is not None

        csv_b = _build_order_csv(
            customer="C客户",
            contract_no="HT-303",
            customer_order_no="SO-303",
            entry_line_no="7",
            item_code="ITEM-NEW",
            item_name="新产品名",
            quantity=120,
            amount=1500,
            order_date=today.isoformat(),
            due_date=due_date,
            latest_outbound_date=outbound_date,
            executed_shipped_qty=30,
            uninvoiced_qty=0,
        )
        job_b = _create_job_and_upload_order(headers, filename="order-b.csv", csv_content=csv_b)

        assert _count_open_alerts(job_a, "ship_after_no_finance", headers) == 1
        assert _count_open_alerts(job_b, "ship_after_no_finance", headers) == 0
    finally:
        restore = client.put(
            "/v1/config/rule_parameters",
            headers=headers,
            json={"value": original_rule},
        )
        assert restore.status_code == 200
