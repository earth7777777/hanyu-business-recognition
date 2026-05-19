from __future__ import annotations

import io
import time
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)



def test_end_to_end_order_to_alert():
    headers = {"X-Role": "admin"}

    current_rule = client.get("/v1/config/rule_parameters", headers=headers)
    assert current_rule.status_code == 200
    rule_value = current_rule.json()["value"]
    enabled = dict(rule_value.get("enabled") or {})
    enabled["due_before_ship"] = True
    rule_value["enabled"] = enabled
    ensure_rule = client.put("/v1/config/rule_parameters", headers=headers, json={"value": rule_value})
    assert ensure_rule.status_code == 200

    create_resp = client.post("/v1/upload-jobs", headers=headers)
    assert create_resp.status_code == 200
    job_id = create_resp.json()["id"]

    due = (date.today() + timedelta(days=2)).isoformat()
    csv_content = (
        "客户,合同号,客户订单号,分录行号,料号,品名,数量,金额,订单日期,交期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        "A客户,HT-001,SO-001,1,ITEM-1,产品A,100,1000,2026-03-18,{},,80,0,20\n"
    ).format(due)

    upload_resp = client.post(
        f"/v1/upload-jobs/{job_id}/files",
        headers=headers,
        data={"document_type": "order"},
        files={"upload": ("order.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert upload_resp.status_code == 200
    assert upload_resp.json()["parsed_count"] >= 1

    task_resp = client.post("/v1/tasks/lobster-feed", headers=headers, json={"job_id": job_id})
    assert task_resp.status_code == 200
    task_id = task_resp.json()["id"]

    # Background task completes quickly; poll briefly for deterministic CI.
    status = "queued"
    for _ in range(20):
        info = client.get(f"/v1/tasks/{task_id}", headers=headers)
        assert info.status_code == 200
        status = info.json()["status"]
        if status in {"succeeded", "failed"}:
            break
        time.sleep(0.1)

    assert status == "succeeded"

    alerts_resp = client.get(f"/v1/alerts?job_id={job_id}", headers=headers)
    assert alerts_resp.status_code == 200
    alerts = alerts_resp.json()
    assert len(alerts) >= 1
    assert any(a["alert_type"] == "due_before_ship" for a in alerts)


def test_orchestrate_route_and_legacy_alias():
    headers = {"X-Role": "admin"}

    create_resp = client.post("/v1/upload-jobs", headers=headers)
    assert create_resp.status_code == 200
    job_id = create_resp.json()["id"]

    due = (date.today() + timedelta(days=2)).isoformat()
    csv_content = (
        "客户,合同号,客户订单号,分录行号,料号,品名,数量,金额,订单日期,交期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        "B客户,HT-002,SO-002,1,ITEM-2,产品B,50,500,2026-03-18,{},,0,0,50\n"
    ).format(due)

    upload_resp = client.post(
        f"/v1/upload-jobs/{job_id}/files",
        headers=headers,
        data={"document_type": "order"},
        files={"upload": ("order.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert upload_resp.status_code == 200

    r_new = client.post("/v1/tasks/orchestrate", headers=headers, json={"job_id": job_id})
    assert r_new.status_code == 200
    assert r_new.json()["task_type"] == "orchestrate"

    r_legacy = client.post("/v1/tasks/lobster-feed", headers=headers, json={"job_id": job_id})
    assert r_legacy.status_code == 200
    assert r_legacy.json()["task_type"] == "lobster_feed"


def test_three_order_lines_should_trigger_two_ship_after_no_finance_alerts():
    headers = {"X-Role": "admin"}
    order_no = f"3541-2508130046-{time.time_ns()}"

    current_rule = client.get("/v1/config/rule_parameters", headers=headers)
    assert current_rule.status_code == 200
    original_rule = current_rule.json()["value"]

    rule_value = dict(original_rule)
    enabled = dict(rule_value.get("enabled") or {})
    enabled["due_before_ship"] = False
    enabled["ship_after_no_finance"] = True
    rule_value["enabled"] = enabled
    rule_value["ship_after_no_finance_days"] = 60
    ensure_rule = client.put("/v1/config/rule_parameters", headers=headers, json={"value": rule_value})
    assert ensure_rule.status_code == 200

    try:
        create_resp = client.post("/v1/upload-jobs", headers=headers)
        assert create_resp.status_code == 200
        job_id = create_resp.json()["id"]

        csv_content = (
            "客户,客户订单号,分录行号,商品名称,商品编码,数量,订单日期,预计交货日期,出库状态,行出库状态,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量,行开票状态\n"
            f"安吉热威,{order_no},1,上泵体,0101-HET-8.5S3828-5,81,2025-08-01,2025-08-22,部分出库,全部出库,2025-08-23,81,,81,未开票\n"
            f"安吉热威,{order_no},2,微动开关安装架,0101-HET-8.5S3828-7,3600,2025-08-01,2025-08-22,部分出库,全部出库,2025-08-16,3600,3122,478,部分开票\n"
            f"安吉热威,{order_no},3,上泵体,0101-HET-8.5S3832-2,45,2025-08-01,2025-08-22,部分出库,全部出库,2025-08-23,45,45,,全部开票\n"
        )

        upload_resp = client.post(
            f"/v1/upload-jobs/{job_id}/files",
            headers=headers,
            data={"document_type": "order"},
            files={"upload": ("sample-order.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
        )
        assert upload_resp.status_code == 200
        assert upload_resp.json()["parsed_count"] == 3

        task_resp = client.post("/v1/tasks/orchestrate", headers=headers, json={"job_id": job_id})
        assert task_resp.status_code == 200
        task_id = task_resp.json()["id"]

        status = "queued"
        for _ in range(30):
            info = client.get(f"/v1/tasks/{task_id}", headers=headers)
            assert info.status_code == 200
            status = info.json()["status"]
            if status in {"succeeded", "failed"}:
                break
            time.sleep(0.1)
        assert status == "succeeded"

        alerts_resp = client.get(f"/v1/alerts?job_id={job_id}", headers=headers)
        assert alerts_resp.status_code == 200
        items = [
            a
            for a in alerts_resp.json()
            if a["alert_type"] == "ship_after_no_finance" and a["status"] == "open"
        ]
        assert len(items) == 2
        source_rows = {it["payload"].get("source_row") for it in items}
        assert source_rows == {1, 2}
        assert all(it["payload"].get("record_id") for it in items)
    finally:
        restore = client.put("/v1/config/rule_parameters", headers=headers, json={"value": original_rule})
        assert restore.status_code == 200
