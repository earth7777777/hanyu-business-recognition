from __future__ import annotations

import io
import time
from datetime import date, datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.db.models import Alert, ViewerAccount, ViewerDevice, ViewerSession
from app.db.session import SessionLocal
from app.main import app
from app.services.viewer_auth import _resolve_account_by_token, hash_password, issue_viewer_session


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
        "客户,客户订单号,分录行号,料号,品名,数量,金额,订单日期,交期,出库状态,行出库状态,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户{suffix},SO-VIEW-{suffix}-INV-A1,1,ITEM-A1,产品A1,100,52000,2026-01-05,2026-01-20,部分出库,全部出库,{outbound_95},100,0,100\n"
        f"A客户{suffix},SO-VIEW-{suffix}-INV-A2,2,ITEM-A2,产品A2,50,,2026-01-06,2026-01-20,部分出库,全部出库,{outbound_65},50,10,40\n"
        f"B客户{suffix},SO-VIEW-{suffix}-INV-B1,1,ITEM-B1,产品B1,80,18000,2026-01-07,2026-01-20,全部出库,全部出库,{outbound_85},80,0,80\n"
        f"C客户{suffix},SO-VIEW-{suffix}-INV-C1,1,ITEM-C1,产品C1,20,9000,2026-01-08,2026-01-20,全部出库,全部出库,{outbound_65},20,0,20\n"
        f"A客户{suffix},SO-VIEW-{suffix}-DUE-A3,3,ITEM-D1,产品D1,30,3000,2026-04-01,{due},未出库,未出库,,0,0,0\n"
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
        "客户,客户订单号,分录行号,料号,品名,数量,金额,订单日期,交期,出库状态,行出库状态,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户{suffix},SO-VIEW-{suffix}-INV-A1,1,ITEM-A1,产品A1,100,52000,2026-01-05,2026-01-20,部分出库,全部出库,{outbound_95},100,0,100\n"
        f"A客户{suffix},SO-VIEW-{suffix}-INV-A2,2,ITEM-A2,产品A2,50,,2026-01-06,2026-01-20,部分出库,全部出库,{outbound_65},50,10,40\n"
        f"B客户{suffix},SO-VIEW-{suffix}-INV-B1,1,ITEM-B1,产品B1,80,18000,2026-01-07,2026-01-20,全部出库,全部出库,{outbound_85},80,0,80\n"
        f"C客户{suffix},SO-VIEW-{suffix}-INV-C1,1,ITEM-C1,产品C1,20,9000,2026-01-08,2026-01-20,全部出库,全部出库,{outbound_65},20,0,20\n"
        f"A客户{suffix},SO-VIEW-{suffix}-DUE-A3,3,ITEM-D1,产品D1,30,3000,2026-04-01,{due},未出库,未出库,,0,0,0\n"
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


def _create_viewer_same_order_lines_job(suffix: str) -> str:
    headers = {"X-Role": "admin"}
    create_resp = client.post("/v1/upload-jobs", headers=headers)
    assert create_resp.status_code == 200
    job_id = create_resp.json()["id"]

    outbound_95 = (date.today() - timedelta(days=95)).isoformat()
    csv_content = (
        "客户,客户订单号,分录行号,料号,品名,数量,金额,订单日期,交期,出库状态,行出库状态,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"同单客户{suffix},SO-VIEW-{suffix}-SAME,1,ITEM-SAME-1,同单产品1,100,1000,2026-01-05,2026-01-20,全部出库,全部出库,{outbound_95},100,0,100\n"
        f"同单客户{suffix},SO-VIEW-{suffix}-SAME,2,ITEM-SAME-2,同单产品2,50,500,2026-01-05,2026-01-20,全部出库,全部出库,{outbound_95},50,0,50\n"
    )
    upload_resp = client.post(
        f"/v1/upload-jobs/{job_id}/files",
        headers=headers,
        data={"document_type": "order"},
        files={"upload": (f"viewer-same-order-{suffix}.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
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


def _create_viewer_sorting_job(suffix: str) -> str:
    headers = {"X-Role": "admin"}
    create_resp = client.post("/v1/upload-jobs", headers=headers)
    assert create_resp.status_code == 200
    job_id = create_resp.json()["id"]

    outbound_95 = (date.today() - timedelta(days=95)).isoformat()
    csv_content = (
        "客户,客户订单号,分录行号,料号,品名,数量,金额,订单日期,交期,出库状态,行出库状态,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"客户排序巨星A{suffix},SO-VIEW-{suffix}-SORT-A,1,ITEM-SORT-A,排序产品A,100,1000,2026-01-05,2026-01-20,全部出库,全部出库,{outbound_95},100,0,100\n"
        f"客户排序普通{suffix},SO-VIEW-{suffix}-SORT-C,1,ITEM-SORT-C,排序产品C,50,500,2026-01-05,2026-01-20,全部出库,全部出库,{outbound_95},50,0,50\n"
        f"客户排序巨星B{suffix},SO-VIEW-{suffix}-SORT-B,1,ITEM-SORT-B,排序产品B,10,100,2026-01-05,2026-01-20,全部出库,全部出库,{outbound_95},10,0,10\n"
        f"订单排序客户{suffix},SO-VIEW-{suffix}-DETAIL-OLD,1,ITEM-DETAIL-OLD,旧单产品,100,1000,2026-01-05,2026-01-20,全部出库,全部出库,{outbound_95},100,0,100\n"
        f"订单排序客户{suffix},SO-VIEW-{suffix}-DETAIL-NEW,1,ITEM-DETAIL-NEW,新单产品,10,100,2026-02-05,2026-02-20,全部出库,全部出库,{outbound_95},10,0,10\n"
    )
    upload_resp = client.post(
        f"/v1/upload-jobs/{job_id}/files",
        headers=headers,
        data={"document_type": "order"},
        files={"upload": (f"viewer-sorting-{suffix}.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
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


def test_viewer_login_records_device_for_admin_review():
    headers = {"X-Role": "admin"}
    phone = _unique_phone(20)
    create_account = client.post(
        "/v1/admin/viewer-accounts",
        headers=headers,
        json={
            "phone": phone,
            "display_name": "老板娘",
            "role": "viewer_boss",
            "password": "viewer-pass-device",
        },
    )
    assert create_account.status_code == 200
    account_id = create_account.json()["id"]

    login_payload = {
        "phone": phone,
        "password": "viewer-pass-device",
        "device": {
            "device_id": "boss-phone-device-001",
            "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Version/17.0 Mobile Safari/604.1",
            "platform": "iPhone",
            "language": "zh-CN",
            "timezone": "Asia/Shanghai",
            "screen": "390x844",
        },
    }
    first_login = client.post("/v1/viewer/auth/login", json=login_payload, headers={"X-Forwarded-For": "1.2.3.4"})
    assert first_login.status_code == 200
    second_login = client.post("/v1/viewer/auth/login", json=login_payload, headers={"X-Forwarded-For": "1.2.3.4"})
    assert second_login.status_code == 200

    with SessionLocal() as db:
        devices = db.query(ViewerDevice).filter(ViewerDevice.account_id == account_id).all()
        assert len(devices) == 1
        device = devices[0]
        assert device.device_key == "boss-phone-device-001"
        assert device.device_type == "iPhone"
        assert device.browser_name == "Safari"
        assert device.ip_address == "1.2.3.4"
        assert device.login_count == 2
        assert device.device_remark == ""

    account_list = client.get("/v1/admin/viewer-accounts", headers=headers)
    assert account_list.status_code == 200
    account = next(item for item in account_list.json() if item["id"] == account_id)
    assert len(account["devices"]) == 1
    device_id = account["devices"][0]["id"]
    assert account["devices"][0]["device_name"] == "iPhone / Safari"
    assert account["devices"][0]["device_remark"] == ""
    assert account["devices"][0]["screen_size"] == "390x844"

    rename_device = client.patch(
        f"/v1/admin/viewer-accounts/{account_id}/devices/{device_id}",
        headers=headers,
        json={"device_remark": "老板娘手机"},
    )
    assert rename_device.status_code == 200
    assert rename_device.json()["device_remark"] == "老板娘手机"

    third_login = client.post("/v1/viewer/auth/login", json=login_payload, headers={"X-Forwarded-For": "1.2.3.4"})
    assert third_login.status_code == 200

    with SessionLocal() as db:
        device = db.query(ViewerDevice).filter(ViewerDevice.id == device_id).one()
        assert device.login_count == 3
        assert device.device_name == "iPhone / Safari"
        assert device.device_remark == "老板娘手机"

    account_list = client.get("/v1/admin/viewer-accounts", headers=headers)
    assert account_list.status_code == 200
    account = next(item for item in account_list.json() if item["id"] == account_id)
    assert account["devices"][0]["device_remark"] == "老板娘手机"


def test_viewer_session_last_seen_recent_request_does_not_write():
    phone = _unique_phone(21)
    with SessionLocal() as db:
        account = ViewerAccount(
            phone=phone,
            display_name="姚建锋",
            role="viewer_yao",
            password_hash=hash_password("viewer-pass-recent"),
            is_active=True,
        )
        db.add(account)
        db.commit()
        db.refresh(account)
        token = issue_viewer_session(db, account)

        original_commit = db.commit

        def fail_if_commit_called():
            raise AssertionError("recent viewer session should not update last_seen_at")

        db.commit = fail_if_commit_called
        try:
            resolved = _resolve_account_by_token(db, token)
            assert resolved.id == account.id
        finally:
            db.commit = original_commit


def test_viewer_session_last_seen_write_conflict_does_not_block_request():
    phone = _unique_phone(22)
    with SessionLocal() as db:
        account = ViewerAccount(
            phone=phone,
            display_name="姚建锋",
            role="viewer_yao",
            password_hash=hash_password("viewer-pass-conflict"),
            is_active=True,
        )
        db.add(account)
        db.commit()
        db.refresh(account)
        token = issue_viewer_session(db, account)

        session = (
            db.query(ViewerSession)
            .filter(ViewerSession.account_id == account.id)
            .order_by(ViewerSession.created_at.desc())
            .first()
        )
        assert session is not None
        session.last_seen_at = datetime.now(timezone.utc) - timedelta(days=1)
        db.commit()

        original_commit = db.commit
        commit_calls = 0

        def raise_write_conflict():
            nonlocal commit_calls
            commit_calls += 1
            raise OperationalError(
                "UPDATE viewer_sessions SET last_seen_at=:last_seen_at",
                {},
                Exception("Record has changed since last read in table 'viewer_sessions'"),
            )

        db.commit = raise_write_conflict
        try:
            resolved = _resolve_account_by_token(db, token)
            assert resolved.id == account.id
            assert commit_calls == 1
        finally:
            db.commit = original_commit


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
    assert customers[0]["related_order_count"] == 2
    assert customers[0]["has_missing_amount"] is True
    assert customers[0]["known_amount_total"] >= 52000

    customer_detail = client.get(
        "/v1/viewer/uninvoiced/customer-detail",
        params={"customer": f"A客户{suffix}", "state": "open"},
    )
    assert customer_detail.status_code == 200
    detail_payload = customer_detail.json()
    assert detail_payload["alert_count"] == 2
    assert detail_payload["related_order_count"] == 2
    assert [item["customer_order_no"] for item in detail_payload["items"]] == [
        f"SO-VIEW-{suffix}-INV-A2",
        f"SO-VIEW-{suffix}-INV-A1",
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


def test_viewer_uninvoiced_related_order_count_counts_orders_not_product_lines():
    headers = {"X-Role": "admin"}
    suffix = str(time.time_ns())[-6:]
    customer = f"同单客户{suffix}"
    _create_viewer_same_order_lines_job(suffix)

    phone = _unique_phone(35)
    create_account = client.post(
        "/v1/admin/viewer-accounts",
        headers=headers,
        json={
            "phone": phone,
            "display_name": "老板娘",
            "role": "viewer_boss",
            "password": "viewer-pass-order-count",
        },
    )
    assert create_account.status_code == 200
    login_resp = client.post(
        "/v1/viewer/auth/login",
        json={"phone": phone, "password": "viewer-pass-order-count"},
    )
    assert login_resp.status_code == 200

    customers_resp = client.get(
        "/v1/viewer/uninvoiced/customers",
        params={"state": "open", "customer": customer},
    )
    assert customers_resp.status_code == 200
    customers = customers_resp.json()
    assert len(customers) == 1
    assert customers[0]["alert_count"] == 2
    assert customers[0]["related_order_count"] == 1
    assert customers[0]["known_amount_total"] == 1500.0

    customer_detail = client.get(
        "/v1/viewer/uninvoiced/customer-detail",
        params={"customer": customer, "state": "open"},
    )
    assert customer_detail.status_code == 200
    detail_payload = customer_detail.json()
    assert detail_payload["alert_count"] == 2
    assert detail_payload["related_order_count"] == 1
    assert [item["customer_order_no"] for item in detail_payload["items"]] == [
        f"SO-VIEW-{suffix}-SAME",
        f"SO-VIEW-{suffix}-SAME",
    ]


def test_viewer_uninvoiced_days_recalculate_from_latest_outbound_date():
    headers = {"X-Role": "admin"}
    suffix = str(time.time_ns())[-6:]
    customer = f"同单客户{suffix}"
    _create_viewer_same_order_lines_job(suffix)

    with SessionLocal() as db:
        target_alerts = [
            alert
            for alert in db.query(Alert).filter(Alert.alert_type == "ship_after_no_finance").all()
            if isinstance(alert.payload_json, dict)
            and alert.payload_json.get("customer_order_no") == f"SO-VIEW-{suffix}-SAME"
        ]
        assert len(target_alerts) == 2
        for alert in target_alerts:
            payload = dict(alert.payload_json)
            payload["days_after_outbound"] = 94
            alert.payload_json = payload
        db.commit()

    phone = _unique_phone(37)
    create_account = client.post(
        "/v1/admin/viewer-accounts",
        headers=headers,
        json={
            "phone": phone,
            "display_name": "老板娘",
            "role": "viewer_boss",
            "password": "viewer-pass-days",
        },
    )
    assert create_account.status_code == 200
    login_resp = client.post(
        "/v1/viewer/auth/login",
        json={"phone": phone, "password": "viewer-pass-days"},
    )
    assert login_resp.status_code == 200

    customers_resp = client.get(
        "/v1/viewer/uninvoiced/customers",
        params={"state": "open", "customer": customer},
    )
    assert customers_resp.status_code == 200
    assert customers_resp.json()[0]["overdue_max_days"] == 35

    customer_detail = client.get(
        "/v1/viewer/uninvoiced/customer-detail",
        params={"customer": customer, "state": "open"},
    )
    assert customer_detail.status_code == 200
    detail_payload = customer_detail.json()
    assert detail_payload["overdue_max_days"] == 35
    assert {item["current_days_after_outbound"] for item in detail_payload["items"]} == {95}

    order_alerts = client.get(
        "/v1/viewer/alerts",
        params={"tab": "uninvoiced", "state": "open", "customer": customer},
    )
    assert order_alerts.status_code == 200
    assert {item["current_days_after_outbound"] for item in order_alerts.json()} == {95}

    detail_resp = client.get(f"/v1/viewer/alerts/{order_alerts.json()[0]['id']}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["current_days_after_outbound"] == 95


def test_viewer_uninvoiced_customer_and_detail_order_follow_export_sorting():
    headers = {"X-Role": "admin"}
    suffix = str(time.time_ns())[-6:]
    _create_viewer_sorting_job(suffix)

    phone = _unique_phone(36)
    create_account = client.post(
        "/v1/admin/viewer-accounts",
        headers=headers,
        json={
            "phone": phone,
            "display_name": "老板娘",
            "role": "viewer_boss",
            "password": "viewer-pass-sorting",
        },
    )
    assert create_account.status_code == 200
    login_resp = client.post(
        "/v1/viewer/auth/login",
        json={"phone": phone, "password": "viewer-pass-sorting"},
    )
    assert login_resp.status_code == 200

    customers_resp = client.get(
        "/v1/viewer/uninvoiced/customers",
        params={"state": "open", "customer": "客户排序"},
    )
    assert customers_resp.status_code == 200
    assert [item["customer"] for item in customers_resp.json()] == [
        f"客户排序巨星A{suffix}",
        f"客户排序巨星B{suffix}",
        f"客户排序普通{suffix}",
    ]

    detail_resp = client.get(
        "/v1/viewer/uninvoiced/customer-detail",
        params={"customer": f"订单排序客户{suffix}", "state": "open"},
    )
    assert detail_resp.status_code == 200
    assert [item["customer_order_no"] for item in detail_resp.json()["items"]] == [
        f"SO-VIEW-{suffix}-DETAIL-NEW",
        f"SO-VIEW-{suffix}-DETAIL-OLD",
    ]

    order_view_customers = client.get(
        "/v1/viewer/alerts",
        params={"tab": "uninvoiced", "state": "open", "customer": "客户排序"},
    )
    assert order_view_customers.status_code == 200
    order_view_customer_items = [
        item for item in order_view_customers.json() if str(item["customer"]).endswith(suffix)
    ]
    assert [item["customer"] for item in order_view_customer_items] == [
        f"客户排序巨星A{suffix}",
        f"客户排序巨星B{suffix}",
        f"客户排序普通{suffix}",
    ]
    assert [item["viewer_sort_index"] for item in order_view_customer_items] == sorted(
        item["viewer_sort_index"] for item in order_view_customer_items
    )

    order_view_detail = client.get(
        "/v1/viewer/alerts",
        params={"tab": "uninvoiced", "state": "open", "customer": f"订单排序客户{suffix}"},
    )
    assert order_view_detail.status_code == 200
    assert [item["customer_order_no"] for item in order_view_detail.json()] == [
        f"SO-VIEW-{suffix}-DETAIL-NEW",
        f"SO-VIEW-{suffix}-DETAIL-OLD",
    ]


def test_viewer_uninvoiced_customer_amount_uses_actual_uninvoiced_amount():
    headers = {"X-Role": "admin"}
    suffix = str(time.time_ns())[-6:]
    customer = f"口径客户{suffix}"
    create_resp = client.post("/v1/upload-jobs", headers=headers)
    assert create_resp.status_code == 200
    job_id = create_resp.json()["id"]

    outbound = (date.today() - timedelta(days=95)).isoformat()
    csv_content = (
        "客户,客户订单号,分录行号,料号,品名,数量,价税合计,含税单价,订单日期,交期,出库状态,行出库状态,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"{customer},SO-VIEW-{suffix}-PARTIAL,1,ITEM-PART,部分开票产品,100,1000,10,2026-01-05,2026-01-20,全部出库,全部出库,{outbound},100,90,10\n"
    )
    upload_resp = client.post(
        f"/v1/upload-jobs/{job_id}/files",
        headers=headers,
        data={"document_type": "order"},
        files={"upload": (f"viewer-amount-{suffix}.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
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

    phone = _unique_phone(34)
    create_account = client.post(
        "/v1/admin/viewer-accounts",
        headers=headers,
        json={
            "phone": phone,
            "display_name": "老板娘",
            "role": "viewer_boss",
            "password": "viewer-pass-amount",
        },
    )
    assert create_account.status_code == 200
    login_resp = client.post(
        "/v1/viewer/auth/login",
        json={"phone": phone, "password": "viewer-pass-amount"},
    )
    assert login_resp.status_code == 200

    customers_resp = client.get(
        "/v1/viewer/uninvoiced/customers",
        params={"state": "open", "customer": customer},
    )
    assert customers_resp.status_code == 200
    customers = customers_resp.json()
    assert len(customers) == 1
    assert customers[0]["known_amount_total"] == 100.0

    customer_detail_resp = client.get(
        "/v1/viewer/uninvoiced/customer-detail",
        params={"state": "open", "customer": customer},
    )
    assert customer_detail_resp.status_code == 200
    customer_detail = customer_detail_resp.json()
    assert len(customer_detail["items"]) == 1
    assert customer_detail["items"][0]["actual_uninvoiced_amount"] == 100.0

    alerts_resp = client.get(
        "/v1/viewer/alerts",
        params={"tab": "uninvoiced", "state": "open", "customer": customer},
    )
    assert alerts_resp.status_code == 200
    alerts = alerts_resp.json()
    assert len(alerts) == 1
    assert alerts[0]["actual_uninvoiced_amount"] == 100.0

    detail_resp = client.get(f"/v1/viewer/alerts/{alerts[0]['id']}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["actual_uninvoiced_amount"] == 100.0
    assert detail["payload"]["amount"] == 1000.0

    admin_resp = client.get(
        "/v1/admin/customer-overview/customers",
        headers=headers,
        params={"keyword": customer},
    )
    assert admin_resp.status_code == 200
    assert admin_resp.json()[0]["known_uninvoiced_amount_total"] == 100.0


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
    assert target_customer["related_order_count"] == 2
    assert target_customer["known_amount_total"] == 52000.0

    customer_detail = client.get(
        "/v1/viewer/uninvoiced/customer-detail",
        params={"customer": f"A客户{suffix}", "state": "open"},
    )
    assert customer_detail.status_code == 200
    detail_payload = customer_detail.json()
    assert detail_payload["alert_count"] == 2
    assert detail_payload["related_order_count"] == 2
    assert detail_payload["known_amount_total"] == 52000.0
    assert [item["customer_order_no"] for item in detail_payload["items"]] == [
        f"SO-VIEW-{suffix}-INV-A2",
        f"SO-VIEW-{suffix}-INV-A1",
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
