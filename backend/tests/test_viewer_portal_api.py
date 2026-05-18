from __future__ import annotations

import io
import time
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.db.models import Alert
from app.db.session import SessionLocal
from app.main import app


client = TestClient(app)


def _unique_phone(offset: int = 0) -> str:
    return f"138{(time.time_ns() + offset) % 100000000:08d}"


def _create_due_alert_job() -> str:
    headers = {"X-Role": "admin"}
    create_resp = client.post("/v1/upload-jobs", headers=headers)
    assert create_resp.status_code == 200
    job_id = create_resp.json()["id"]

    due = (date.today() + timedelta(days=1)).isoformat()
    csv_content = (
        "客户,客户订单号,分录行号,料号,品名,数量,订单日期,交期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        "A客户,SO-VIEW-001,1,ITEM-1,产品A,100,2026-03-18,{},,20,0,80\n"
    ).format(due)
    upload_resp = client.post(
        f"/v1/upload-jobs/{job_id}/files",
        headers=headers,
        data={"document_type": "order"},
        files={"upload": ("viewer-order.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert upload_resp.status_code == 200

    task_resp = client.post("/v1/tasks/orchestrate", headers=headers, json={"job_id": job_id})
    assert task_resp.status_code == 200
    task_id = task_resp.json()["id"]
    for _ in range(20):
        info = client.get(f"/v1/tasks/{task_id}", headers=headers)
        assert info.status_code == 200
        status = info.json()["status"]
        if status in {"succeeded", "failed"}:
            break
        time.sleep(0.1)
    assert status == "succeeded"
    return job_id


def _create_viewer_mix_job(suffix: str) -> str:
    headers = {"X-Role": "admin"}
    create_resp = client.post("/v1/upload-jobs", headers=headers)
    assert create_resp.status_code == 200
    job_id = create_resp.json()["id"]

    due = (date.today() + timedelta(days=1)).isoformat()
    outbound_95 = (date.today() - timedelta(days=95)).isoformat()
    outbound_85 = (date.today() - timedelta(days=85)).isoformat()
    outbound_65 = (date.today() - timedelta(days=65)).isoformat()
    csv_content = (
        "客户,客户订单号,分录行号,料号,品名,数量,金额,订单日期,交期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户{suffix},SO-VIEW-{suffix}-INV-A1,1,ITEM-A1,产品A1,100,52000,2026-01-05,2026-01-20,{outbound_95},100,0,100\n"
        f"A客户{suffix},SO-VIEW-{suffix}-INV-A2,2,ITEM-A2,产品A2,50,,2026-01-06,2026-01-20,{outbound_65},50,10,40\n"
        f"B客户{suffix},SO-VIEW-{suffix}-INV-B1,1,ITEM-B1,产品B1,80,18000,2026-01-07,2026-01-20,{outbound_85},80,0,80\n"
        f"C客户{suffix},SO-VIEW-{suffix}-INV-C1,1,ITEM-C1,产品C1,20,9000,2026-01-08,2026-01-20,{outbound_65},20,0,20\n"
        f"A客户{suffix},SO-VIEW-{suffix}-DUE-A3,3,ITEM-D1,产品D1,30,3000,2026-04-01,{due},,0,0,0\n"
    )
    upload_resp = client.post(
        f"/v1/upload-jobs/{job_id}/files",
        headers=headers,
        data={"document_type": "order"},
        files={"upload": (f"viewer-mix-{suffix}.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert upload_resp.status_code == 200

    task_resp = client.post("/v1/tasks/orchestrate", headers=headers, json={"job_id": job_id})
    assert task_resp.status_code == 200
    task_id = task_resp.json()["id"]
    for _ in range(20):
        info = client.get(f"/v1/tasks/{task_id}", headers=headers)
        assert info.status_code == 200
        status = info.json()["status"]
        if status in {"succeeded", "failed"}:
            break
        time.sleep(0.1)
    assert status == "succeeded"
    return job_id


def _create_viewer_mix_job_with_unique_orders(suffix: str) -> str:
    headers = {"X-Role": "admin"}
    create_resp = client.post("/v1/upload-jobs", headers=headers)
    assert create_resp.status_code == 200
    job_id = create_resp.json()["id"]

    due = (date.today() + timedelta(days=1)).isoformat()
    outbound_95 = (date.today() - timedelta(days=95)).isoformat()
    outbound_85 = (date.today() - timedelta(days=85)).isoformat()
    outbound_65 = (date.today() - timedelta(days=65)).isoformat()
    csv_content = (
        "客户,客户订单号,分录行号,料号,品名,数量,金额,订单日期,交期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户{suffix},SO-VIEW-{suffix}-INV-A1,1,ITEM-A1,产品A1,100,52000,2026-01-05,2026-01-20,{outbound_95},100,0,100\n"
        f"A客户{suffix},SO-VIEW-{suffix}-INV-A2,2,ITEM-A2,产品A2,50,,2026-01-06,2026-01-20,{outbound_65},50,10,40\n"
        f"B客户{suffix},SO-VIEW-{suffix}-INV-B1,1,ITEM-B1,产品B1,80,18000,2026-01-07,2026-01-20,{outbound_85},80,0,80\n"
        f"C客户{suffix},SO-VIEW-{suffix}-INV-C1,1,ITEM-C1,产品C1,20,9000,2026-01-08,2026-01-20,{outbound_65},20,0,20\n"
        f"A客户{suffix},SO-VIEW-{suffix}-DUE-A3,3,ITEM-D1,产品D1,30,3000,2026-04-01,{due},,0,0,0\n"
    )
    upload_resp = client.post(
        f"/v1/upload-jobs/{job_id}/files",
        headers=headers,
        data={"document_type": "order"},
        files={"upload": (f"viewer-mix-unique-{suffix}.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert upload_resp.status_code == 200

    task_resp = client.post("/v1/tasks/orchestrate", headers=headers, json={"job_id": job_id})
    assert task_resp.status_code == 200
    task_id = task_resp.json()["id"]
    for _ in range(20):
        info = client.get(f"/v1/tasks/{task_id}", headers=headers)
        assert info.status_code == 200
        status = info.json()["status"]
        if status in {"succeeded", "failed"}:
            break
        time.sleep(0.1)
    assert status == "succeeded"
    return job_id


def test_viewer_portal_login_alerts_and_read_flow():
    headers = {"X-Role": "admin"}
    job_id = _create_due_alert_job()
    phone = _unique_phone(1)

    create_account = client.post(
        "/v1/admin/viewer-accounts",
        headers=headers,
        json={
            "phone": phone,
            "display_name": "姚建锋",
            "role": "viewer_yao",
            "password": "viewer-pass-1",
        },
    )
    assert create_account.status_code == 200

    login_resp = client.post(
        "/v1/viewer/auth/login",
        json={"phone": phone, "password": "viewer-pass-1"},
    )
    assert login_resp.status_code == 200

    me_resp = client.get("/v1/viewer/me")
    assert me_resp.status_code == 200
    assert me_resp.json()["display_name"] == "姚建锋"

    overview_resp = client.get("/v1/viewer/overview")
    assert overview_resp.status_code == 200
    overview = overview_resp.json()
    assert overview["today_new_count"] >= 1
    assert overview["open_unshipped_count"] >= 1

    alerts_resp = client.get("/v1/viewer/alerts", params={"tab": "unshipped", "state": "open"})
    assert alerts_resp.status_code == 200
    alerts = alerts_resp.json()
    assert len(alerts) >= 1
    item = alerts[0]
    assert item["alert_type"] == "due_before_ship"
    assert item["is_unread_change"] is True

    detail_resp = client.get(f"/v1/viewer/alerts/{item['id']}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["group_id"]

    source_resp = client.get(f"/v1/viewer/alerts/{item['id']}/source-row")
    assert source_resp.status_code == 200
    assert source_resp.json()["source_row"] == 1

    read_resp = client.post(f"/v1/viewer/alerts/{item['id']}/read")
    assert read_resp.status_code == 200

    after_read_resp = client.get("/v1/viewer/alerts", params={"tab": "unshipped", "state": "open"})
    assert after_read_resp.status_code == 200
    after_item = after_read_resp.json()[0]
    assert after_item["is_unread_change"] is False
    assert after_item["change_label"] is None

    legacy_alerts_resp = client.get(f"/v1/alerts?job_id={job_id}", headers=headers)
    assert legacy_alerts_resp.status_code == 200
    assert len(legacy_alerts_resp.json()) >= 1


def test_viewer_account_reset_and_disable():
    headers = {"X-Role": "admin"}
    phone = _unique_phone(2)
    create_account = client.post(
        "/v1/admin/viewer-accounts",
        headers=headers,
        json={
            "phone": phone,
            "display_name": "老板娘",
            "role": "viewer_boss",
            "password": "viewer-pass-2",
        },
    )
    assert create_account.status_code == 200
    account_id = create_account.json()["id"]

    first_login = client.post(
        "/v1/viewer/auth/login",
        json={"phone": phone, "password": "viewer-pass-2"},
    )
    assert first_login.status_code == 200

    reset_resp = client.post(
        f"/v1/admin/viewer-accounts/{account_id}/reset-password",
        headers=headers,
        json={"password": "viewer-pass-3"},
    )
    assert reset_resp.status_code == 200

    old_login = client.post(
        "/v1/viewer/auth/login",
        json={"phone": phone, "password": "viewer-pass-2"},
    )
    assert old_login.status_code == 401

    new_login = client.post(
        "/v1/viewer/auth/login",
        json={"phone": phone, "password": "viewer-pass-3"},
    )
    assert new_login.status_code == 200

    disable_resp = client.patch(
        f"/v1/admin/viewer-accounts/{account_id}",
        headers=headers,
        json={"is_active": False},
    )
    assert disable_resp.status_code == 200
    assert disable_resp.json()["is_active"] is False

    disabled_login = client.post(
        "/v1/viewer/auth/login",
        json={"phone": phone, "password": "viewer-pass-3"},
    )
    assert disabled_login.status_code == 403


def test_viewer_uninvoiced_customer_ranking_and_reminder_switch():
    headers = {"X-Role": "admin"}
    suffix = str(time.time_ns())[-6:]
    _create_viewer_mix_job(suffix)
    phone = _unique_phone(3)

    create_account = client.post(
        "/v1/admin/viewer-accounts",
        headers=headers,
        json={
            "phone": phone,
            "display_name": "老板娘",
            "role": "viewer_boss",
            "password": "viewer-pass-9",
        },
    )
    assert create_account.status_code == 200

    login_resp = client.post(
        "/v1/viewer/auth/login",
        json={"phone": phone, "password": "viewer-pass-9"},
    )
    assert login_resp.status_code == 200

    before_overview = client.get("/v1/viewer/overview")
    assert before_overview.status_code == 200
    before_open_uninvoiced_count = before_overview.json()["open_uninvoiced_count"]

    customers_resp = client.get("/v1/viewer/uninvoiced/customers", params={"state": "open"})
    assert customers_resp.status_code == 200
    customers = customers_resp.json()
    assert len(customers) >= 3
    assert customers[0]["customer"] == f"A客户{suffix}"
    assert customers[0]["alert_count"] == 2
    assert customers[0]["has_missing_amount"] is True
    assert customers[0]["known_amount_total"] >= 52000

    customer_detail = client.get(
        "/v1/viewer/uninvoiced/customer-detail",
        params={"customer": f"A客户{suffix}", "state": "open"},
    )
    assert customer_detail.status_code == 200
    detail_payload = customer_detail.json()
    assert detail_payload["alert_count"] == 2
    assert [item["customer_order_no"] for item in detail_payload["items"]] == [
        f"SO-VIEW-{suffix}-INV-A1",
        f"SO-VIEW-{suffix}-INV-A2",
    ]

    settings_before = client.get("/v1/admin/viewer-reminder-settings", headers=headers)
    assert settings_before.status_code == 200
    setting_items = settings_before.json()["items"]
    target = next(
        item
        for item in setting_items
        if item["customer"] == f"A客户{suffix}" and item["alert_type"] == "ship_after_no_finance"
    )
    assert target["is_enabled"] is True
    assert target["open_alert_count"] == 2
    assert target["has_missing_amount"] is True

    disable_resp = client.put(
        "/v1/admin/viewer-reminder-settings",
        headers=headers,
        json={
            "customer": f"A客户{suffix}",
            "alert_type": "ship_after_no_finance",
            "enabled": False,
            "reason": "这家客户本轮允许延期",
            "operator_name": "月总",
        },
    )
    assert disable_resp.status_code == 200

    settings_after_disable = client.get("/v1/admin/viewer-reminder-settings", headers=headers)
    assert settings_after_disable.status_code == 200
    after_disable_payload = settings_after_disable.json()
    changed = next(
        item
        for item in after_disable_payload["items"]
        if item["customer"] == f"A客户{suffix}" and item["alert_type"] == "ship_after_no_finance"
    )
    assert changed["is_enabled"] is False
    assert changed["last_operator_name"] == "月总"
    assert changed["last_reason"] == "这家客户本轮允许延期"
    assert after_disable_payload["logs"][0]["customer"] == f"A客户{suffix}"
    assert after_disable_payload["logs"][0]["alert_type"] == "ship_after_no_finance"
    assert after_disable_payload["logs"][0]["is_enabled"] is False

    customers_after_disable = client.get("/v1/viewer/uninvoiced/customers", params={"state": "open"})
    assert customers_after_disable.status_code == 200
    assert all(item["customer"] != f"A客户{suffix}" for item in customers_after_disable.json())

    customer_detail_after_disable = client.get(
        "/v1/viewer/uninvoiced/customer-detail",
        params={"customer": f"A客户{suffix}", "state": "open"},
    )
    assert customer_detail_after_disable.status_code == 404

    open_uninvoiced_alerts_after_disable = client.get("/v1/viewer/alerts", params={"tab": "uninvoiced", "state": "open"})
    assert open_uninvoiced_alerts_after_disable.status_code == 200
    assert all(item["customer"] != f"A客户{suffix}" for item in open_uninvoiced_alerts_after_disable.json())

    open_unshipped_after_disable = client.get("/v1/viewer/alerts", params={"tab": "unshipped", "state": "open"})
    assert open_unshipped_after_disable.status_code == 200
    assert any(item["customer"] == f"A客户{suffix}" for item in open_unshipped_after_disable.json())

    overview_after_disable = client.get("/v1/viewer/overview")
    assert overview_after_disable.status_code == 200
    assert overview_after_disable.json()["open_uninvoiced_count"] == before_open_uninvoiced_count - 2

    enable_resp = client.put(
        "/v1/admin/viewer-reminder-settings",
        headers=headers,
        json={
            "customer": f"A客户{suffix}",
            "alert_type": "ship_after_no_finance",
            "enabled": True,
            "reason": "恢复正常催票",
            "operator_name": "月总",
        },
    )
    assert enable_resp.status_code == 200

    customers_after_enable = client.get("/v1/viewer/uninvoiced/customers", params={"state": "open"})
    assert customers_after_enable.status_code == 200
    assert any(item["customer"] == f"A客户{suffix}" for item in customers_after_enable.json())

    overview_after_enable = client.get("/v1/viewer/overview")
    assert overview_after_enable.status_code == 200
    assert overview_after_enable.json()["open_uninvoiced_count"] == before_open_uninvoiced_count


def test_admin_customer_overview_summary_and_detail():
    headers = {"X-Role": "admin"}
    suffix = str(time.time_ns())[-6:]
    _create_viewer_mix_job(suffix)

    customers_resp = client.get(
        "/v1/admin/customer-overview/customers",
        headers=headers,
        params={"keyword": f"A客户{suffix}"},
    )
    assert customers_resp.status_code == 200
    customers = customers_resp.json()
    assert len(customers) == 1
    target = customers[0]
    assert target["customer"] == f"A客户{suffix}"
    assert target["open_alert_count"] == 3
    assert target["open_unshipped_count"] == 1
    assert target["open_uninvoiced_count"] == 2
    assert target["known_uninvoiced_amount_total"] >= 52000
    assert target["has_missing_amount"] is True
    assert target["job_count"] == 1
    assert target["file_count"] == 1
    assert target["record_count"] == 3

    detail_resp = client.get(
        "/v1/admin/customer-overview/detail",
        headers=headers,
        params={"customer": f"A客户{suffix}"},
    )
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["summary"]["customer"] == f"A客户{suffix}"
    assert len(detail["unshipped_items"]) == 1
    assert len(detail["uninvoiced_items"]) == 2
    assert detail["unshipped_items"][0]["customer_order_no"] == f"SO-VIEW-{suffix}-DUE-A3"
    assert detail["uninvoiced_items"][0]["customer_order_no"] == f"SO-VIEW-{suffix}-INV-A1"
    assert detail["uninvoiced_items"][1]["customer_order_no"] == f"SO-VIEW-{suffix}-INV-A2"
    assert detail["uninvoiced_items"][0]["job_id"]
    assert detail["uninvoiced_items"][0]["file_id"]
    assert detail["uninvoiced_items"][0]["record_id"]


def test_viewer_and_admin_uninvoiced_views_dedupe_internal_placeholder_duplicates():
    headers = {"X-Role": "admin"}
    suffix = str(time.time_ns())[-6:]
    _create_viewer_mix_job_with_unique_orders(suffix)

    phone = _unique_phone(33)
    create_account = client.post(
        "/v1/admin/viewer-accounts",
        headers=headers,
        json={
            "phone": phone,
            "display_name": "老板娘",
            "role": "viewer_boss",
            "password": "viewer-pass-dedupe",
        },
    )
    assert create_account.status_code == 200

    login_resp = client.post(
        "/v1/viewer/auth/login",
        json={"phone": phone, "password": "viewer-pass-dedupe"},
    )
    assert login_resp.status_code == 200

    baseline_overview = client.get("/v1/viewer/overview")
    assert baseline_overview.status_code == 200
    baseline_open_uninvoiced_count = baseline_overview.json()["open_uninvoiced_count"]

    with SessionLocal() as db:
        original = (
            db.query(Alert)
            .filter(Alert.alert_type == "ship_after_no_finance", Alert.status == "open")
            .order_by(Alert.created_at.asc())
            .all()
        )
        target = next(
            item
            for item in original
            if str((item.payload_json or {}).get("customer_order_no") or "").strip() == f"SO-VIEW-{suffix}-INV-A1"
        )
        base_payload = dict(target.payload_json or {})
        for index in range(2):
            dup_payload = dict(base_payload)
            dup_payload["customer_order_no"] = f"AMOUNT-FIX-dup-{index}"
            dup_payload["record_id"] = ""
            dup_payload["source_row"] = None
            db.add(
                Alert(
                    job_id=target.job_id,
                    group_id=target.group_id,
                    alert_type=target.alert_type,
                    status="open",
                    severity=target.severity,
                    message=target.message,
                    payload_json=dup_payload,
                )
            )
        db.commit()

    overview_resp = client.get("/v1/viewer/overview")
    assert overview_resp.status_code == 200
    overview = overview_resp.json()
    assert overview["open_uninvoiced_count"] == baseline_open_uninvoiced_count

    customers_resp = client.get("/v1/viewer/uninvoiced/customers", params={"state": "open"})
    assert customers_resp.status_code == 200
    customers = customers_resp.json()
    target_customer = next(item for item in customers if item["customer"] == f"A客户{suffix}")
    assert target_customer["alert_count"] == 2
    assert target_customer["known_amount_total"] == 52000.0

    customer_detail = client.get(
        "/v1/viewer/uninvoiced/customer-detail",
        params={"customer": f"A客户{suffix}", "state": "open"},
    )
    assert customer_detail.status_code == 200
    detail_payload = customer_detail.json()
    assert detail_payload["alert_count"] == 2
    assert detail_payload["known_amount_total"] == 52000.0
    assert [item["customer_order_no"] for item in detail_payload["items"]] == [
        f"SO-VIEW-{suffix}-INV-A1",
        f"SO-VIEW-{suffix}-INV-A2",
    ]

    customers_admin = client.get(
        "/v1/admin/customer-overview/customers",
        headers=headers,
        params={"keyword": f"A客户{suffix}"},
    )
    assert customers_admin.status_code == 200
    admin_target = customers_admin.json()[0]
    assert admin_target["open_alert_count"] == 3
    assert admin_target["open_uninvoiced_count"] == 2
    assert admin_target["known_uninvoiced_amount_total"] == 52000.0

    detail_admin = client.get(
        "/v1/admin/customer-overview/detail",
        headers=headers,
        params={"customer": f"A客户{suffix}"},
    )
    assert detail_admin.status_code == 200
    admin_detail = detail_admin.json()
    assert len(admin_detail["uninvoiced_items"]) == 2
    assert [item["customer_order_no"] for item in admin_detail["uninvoiced_items"]] == [
        f"SO-VIEW-{suffix}-INV-A1",
        f"SO-VIEW-{suffix}-INV-A2",
    ]

    settings_resp = client.get("/v1/admin/viewer-reminder-settings", headers=headers)
    assert settings_resp.status_code == 200
    setting = next(
        item
        for item in settings_resp.json()["items"]
        if item["customer"] == f"A客户{suffix}" and item["alert_type"] == "ship_after_no_finance"
    )
    assert setting["open_alert_count"] == 2
    assert setting["known_amount_total"] == 52000.0
