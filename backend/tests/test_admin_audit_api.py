from __future__ import annotations

import io
import os
import shutil
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.settings import settings
from app.db.init_db import _backfill_lifecycle_defaults
from app.db.models import NormalizedRecord, TaskRun, UploadedFile
from app.db.session import SessionLocal
from app.main import app
from app.services import log_retention_service
from app.services import restore_drill_service


client = TestClient(app)


def _wait_task_done(task_id: str, headers: dict[str, str], timeout_sec: float = 5.0) -> str:
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


def _create_job(headers: dict[str, str]) -> str:
    create_job = client.post("/v1/upload-jobs", headers=headers)
    assert create_job.status_code == 200
    return create_job.json()["id"]


def _upload_order_csv(job_id: str, csv_content: str, headers: dict[str, str], filename: str = "order.csv") -> str:
    upload = client.post(
        f"/v1/upload-jobs/{job_id}/files",
        headers=headers,
        data={"document_type": "order"},
        files={"upload": (filename, io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert upload.status_code == 200
    return upload.json()["id"]


def _order_records(order_no: str) -> list[NormalizedRecord]:
    with SessionLocal() as db:
        rows = (
            db.query(NormalizedRecord)
            .filter(NormalizedRecord.document_type == "order")
            .order_by(NormalizedRecord.created_at.asc(), NormalizedRecord.source_row.asc())
            .all()
        )
        return [
            row
            for row in rows
            if (((row.payload_json or {}).get("core") or {}).get("customer_order_no")) == order_no
        ]


def test_admin_audit_stage1_endpoints():
    headers = {"X-Role": "admin"}

    create_job = client.post("/v1/upload-jobs", headers=headers)
    assert create_job.status_code == 200
    job_id = create_job.json()["id"]

    due = (date.today() + timedelta(days=2)).isoformat()
    csv_content = (
        "客户,合同号,客户订单号,分录行号,料号,品名,数量,金额,订单日期,交期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-900,SO-900,1,ITEM-900,产品900,100,1000,2026-03-18,{due},,0,0,100\n"
    )
    upload = client.post(
        f"/v1/upload-jobs/{job_id}/files",
        headers=headers,
        data={"document_type": "order"},
        files={"upload": ("order.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert upload.status_code == 200
    file_id = upload.json()["id"]

    task = client.post("/v1/tasks/orchestrate", headers=headers, json={"job_id": job_id})
    assert task.status_code == 200
    assert _wait_task_done(task.json()["id"], headers) == "succeeded"

    jobs = client.get("/v1/admin/jobs?page=1&size=20", headers=headers)
    assert jobs.status_code == 200
    rows = jobs.json()["items"]
    row = [x for x in rows if x["job_id"] == job_id][0]
    assert row["file_count"] >= 1
    assert row["latest_filename"] == "order.csv"

    files = client.get(f"/v1/admin/jobs/{job_id}/files", headers=headers)
    assert files.status_code == 200
    f0 = files.json()["items"][0]
    assert f0["file_id"] == file_id
    assert f0["filename"] == "order.csv"
    assert f0["file_size"] > 0
    assert f0["storage_key"]

    overview = client.get(f"/v1/admin/jobs/{job_id}/overview", headers=headers)
    assert overview.status_code == 200
    ov = overview.json()
    assert ov["normalized_record_count"] >= 1
    assert ov["latest_task_id"]

    detail = client.get(f"/v1/admin/files/{file_id}", headers=headers)
    assert detail.status_code == 200
    dv = detail.json()
    assert dv["job_id"] == job_id
    assert dv["filename"] == "order.csv"
    assert dv["preview_kind"] == "tabular"

    preview = client.get(f"/v1/admin/files/{file_id}/content?mode=preview", headers=headers)
    assert preview.status_code == 200
    pv = preview.json()
    assert pv["kind"] == "tabular"
    assert len(pv["rows"]) >= 1

    download = client.get(f"/v1/admin/files/{file_id}/content?mode=download", headers=headers)
    assert download.status_code == 200
    assert "attachment" in (download.headers.get("content-disposition") or "").lower()


def test_admin_duplicate_risk_remains_but_sources_endpoint_is_removed():
    headers = {"X-Role": "admin"}

    create_job_1 = client.post("/v1/upload-jobs", headers=headers)
    assert create_job_1.status_code == 200
    job_1 = create_job_1.json()["id"]
    create_job_2 = client.post("/v1/upload-jobs", headers=headers)
    assert create_job_2.status_code == 200
    job_2 = create_job_2.json()["id"]

    due = (date.today() + timedelta(days=2)).isoformat()
    csv_content = (
        "客户,合同号,客户订单号,料号,品名,数量,金额,订单日期,交期\n"
        f"A客户,HT-dup,SO-dup,ITEM-dup,产品dup,100,1000,2026-03-18,{due}\n"
    )

    up_1 = client.post(
        f"/v1/upload-jobs/{job_1}/files",
        headers=headers,
        data={"document_type": "order"},
        files={"upload": ("order-dup.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert up_1.status_code == 200
    file_1 = up_1.json()["id"]
    up_2 = client.post(
        f"/v1/upload-jobs/{job_1}/files",
        headers=headers,
        data={"document_type": "order"},
        files={"upload": ("order-dup.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert up_2.status_code == 200
    file_2 = up_2.json()["id"]
    up_3 = client.post(
        f"/v1/upload-jobs/{job_2}/files",
        headers=headers,
        data={"document_type": "order"},
        files={"upload": ("order-dup.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert up_3.status_code == 200
    file_3 = up_3.json()["id"]

    files_job_1 = client.get(f"/v1/admin/jobs/{job_1}/files", headers=headers)
    assert files_job_1.status_code == 200
    item_2 = [x for x in files_job_1.json()["items"] if x["file_id"] == file_2][0]
    assert item_2["duplicate_risk"] == "same_job"

    files_job_2 = client.get(f"/v1/admin/jobs/{job_2}/files", headers=headers)
    assert files_job_2.status_code == 200
    item_3 = [x for x in files_job_2.json()["items"] if x["file_id"] == file_3][0]
    assert item_3["duplicate_risk"] == "global"

    detail_3 = client.get(f"/v1/admin/files/{file_3}", headers=headers)
    assert detail_3.status_code == 200
    assert detail_3.json()["duplicate_risk"] == "global"

    dup_3 = client.get(f"/v1/admin/files/{file_3}/duplicates", headers=headers)
    assert dup_3.status_code == 404


def test_admin_records_view_and_scan_stats():
    headers = {"X-Role": "admin"}

    create_job = client.post("/v1/upload-jobs", headers=headers)
    assert create_job.status_code == 200
    job_id = create_job.json()["id"]

    due = (date.today() + timedelta(days=2)).isoformat()
    outbound = (date.today() - timedelta(days=10)).isoformat()
    csv_content = (
        "客户,合同号,客户订单号,料号,品名,数量,金额,订单日期,交期,最近出库日期,行已执行已出库数量,行未开票数量\n"
        f"A客户,HT-910,SO-910,ITEM-910,产品910,100,1000,2026-03-18,{due},{outbound},100,0\n"
        f"A客户,HT-910,SO-910,ITEM-911,产品911,100,1100,2026-03-18,{due},{outbound},60,40\n"
    )
    upload = client.post(
        f"/v1/upload-jobs/{job_id}/files",
        headers=headers,
        data={"document_type": "order"},
        files={"upload": ("order-scan.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert upload.status_code == 200
    file_id = upload.json()["id"]

    overview = client.get(f"/v1/admin/jobs/{job_id}/overview", headers=headers)
    assert overview.status_code == 200
    ov = overview.json()
    assert ov["total_record_count"] == 2
    assert ov["skip_scan_count"] == 1
    assert ov["effective_scan_count"] == 1

    detail = client.get(f"/v1/admin/files/{file_id}", headers=headers)
    assert detail.status_code == 200
    dv = detail.json()
    assert dv["total_record_count"] == 2
    assert dv["skip_scan_count"] == 1
    assert dv["effective_scan_count"] == 1

    records = client.get(f"/v1/admin/files/{file_id}/records", headers=headers)
    assert records.status_code == 200
    payload = records.json()
    assert payload["count"] == 2
    assert payload["total_record_count"] == 2
    assert payload["skip_scan_count"] == 1
    assert payload["effective_scan_count"] == 1
    assert payload["items"][0]["scan_state"] == "completed_skip_scan"
    assert "已完成，跳过扫描" in payload["items"][0]["scan_reason"]
    assert payload["items"][0]["entry_line_no"] in {"", None}
    assert payload["items"][1]["scan_state"] is None
    assert "仍参与提醒判断" in payload["items"][1]["scan_reason"]


def test_admin_records_view_includes_entry_line_no():
    headers = {"X-Role": "admin"}
    order_no = f"DOC-920-{uuid4().hex[:8]}"

    create_job = client.post("/v1/upload-jobs", headers=headers)
    assert create_job.status_code == 200
    job_id = create_job.json()["id"]

    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-920,{order_no},2.0,ITEM-920,产品920,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    upload = client.post(
        f"/v1/upload-jobs/{job_id}/files",
        headers=headers,
        data={"document_type": "order"},
        files={"upload": ("order-entry-line.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert upload.status_code == 200
    file_id = upload.json()["id"]

    records = client.get(f"/v1/admin/files/{file_id}/records", headers=headers)
    assert records.status_code == 200
    payload = records.json()
    assert payload["count"] == 1
    assert payload["items"][0]["entry_line_no"] == "2"
    assert payload["items"][0]["biz_date"] == "2026-03-18"
    assert payload["items"][0]["invoiced_qty"] == 0.0
    assert payload["items"][0]["order_unshipped_qty"] == 100.0
    assert payload["items"][0]["change_type"] == "new"
    assert payload["items"][0]["version_status"] == "current"
    assert payload["items"][0]["is_current_effective"] is True


def test_admin_records_view_includes_outbound_statuses():
    headers = {"X-Role": "admin"}
    order_no = f"DOC-921-{uuid4().hex[:8]}"

    create_job = client.post("/v1/upload-jobs", headers=headers)
    assert create_job.status_code == 200
    job_id = create_job.json()["id"]

    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,出库状态,行出库状态,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-921,{order_no},1,ITEM-921,产品921,100,1000,2026-03-18,2026-03-20,部分出库,全部出库,2026-03-21,100,40,60\n"
    )
    upload = client.post(
        f"/v1/upload-jobs/{job_id}/files",
        headers=headers,
        data={"document_type": "order"},
        files={"upload": ("order-outbound-status.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert upload.status_code == 200
    file_id = upload.json()["id"]

    records = client.get(f"/v1/admin/files/{file_id}/records", headers=headers)
    assert records.status_code == 200
    payload = records.json()
    assert payload["count"] == 1
    assert payload["items"][0]["order_outbound_status"] == "partially_outbound"
    assert payload["items"][0]["line_outbound_status"] == "fully_outbound"


def test_upload_job_summary_matches_admin_job_summary_counts():
    headers = {"X-Role": "admin"}
    active_job_id = _create_job(headers)
    recycle_job_id = _create_job(headers)
    empty_job_id = _create_job(headers)

    active_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        "A客户,HT-SUMMARY,DOC-SUMMARY-A,1,ITEM-A,产品A,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    recycle_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        "A客户,HT-SUMMARY,DOC-SUMMARY-B,1,ITEM-B,产品B,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    _upload_order_csv(active_job_id, active_csv, headers, filename="summary-active.csv")
    recycle_file_id = _upload_order_csv(recycle_job_id, recycle_csv, headers, filename="summary-recycle.csv")

    recycle_resp = client.post(
        f"/v1/admin/files/{recycle_file_id}/soft-delete",
        headers=headers,
        json={"reason": "汇总对账测试"},
    )
    assert recycle_resp.status_code == 200

    upload_summary = client.get("/v1/upload-jobs/summary", headers=headers)
    assert upload_summary.status_code == 200
    admin_summary = client.get("/v1/admin/jobs?page=1&size=100", headers=headers)
    assert admin_summary.status_code == 200
    upload_non_empty = client.get("/v1/upload-jobs/summary?batch_view=non_empty", headers=headers)
    assert upload_non_empty.status_code == 200
    admin_non_empty = client.get("/v1/admin/jobs?page=1&size=100&batch_view=non_empty", headers=headers)
    assert admin_non_empty.status_code == 200

    upload_items = upload_summary.json()["items"]
    admin_items = admin_summary.json()["items"]
    assert [item["job_id"] for item in upload_items] == [item["job_id"] for item in admin_items]
    assert empty_job_id in {item["job_id"] for item in upload_items}
    assert empty_job_id not in {item["job_id"] for item in upload_non_empty.json()["items"]}
    assert empty_job_id not in {item["job_id"] for item in admin_non_empty.json()["items"]}

    upload_by_job = {item["job_id"]: item for item in upload_items}
    admin_by_job = {item["job_id"]: item for item in admin_items}

    for job_id in (active_job_id, recycle_job_id, empty_job_id):
        assert upload_by_job[job_id]["active_file_count"] == admin_by_job[job_id]["active_file_count"]
        assert upload_by_job[job_id]["recycle_bin_file_count"] == admin_by_job[job_id]["recycle_bin_file_count"]
        assert upload_by_job[job_id]["active_record_count"] == admin_by_job[job_id]["active_record_count"]
        assert upload_by_job[job_id]["recycle_bin_record_count"] == admin_by_job[job_id]["recycle_bin_record_count"]
        assert upload_by_job[job_id]["is_empty_shell"] == admin_by_job[job_id]["is_empty_shell"]

    assert upload_by_job[active_job_id]["active_file_count"] == 1
    assert upload_by_job[active_job_id]["current_effective_record_count"] == 1
    assert upload_by_job[active_job_id]["is_empty_shell"] is False
    assert upload_by_job[active_job_id]["has_anomaly"] is False
    assert upload_by_job[active_job_id]["anomaly_codes"] == []

    assert upload_by_job[recycle_job_id]["active_file_count"] == 0
    assert upload_by_job[recycle_job_id]["recycle_bin_file_count"] == 1
    assert upload_by_job[recycle_job_id]["recycle_bin_record_count"] == 1
    assert upload_by_job[recycle_job_id]["is_empty_shell"] is False

    assert upload_by_job[empty_job_id]["active_file_count"] == 0
    assert upload_by_job[empty_job_id]["recycle_bin_file_count"] == 0
    assert upload_by_job[empty_job_id]["total_record_count"] == 0
    assert upload_by_job[empty_job_id]["is_empty_shell"] is True


def test_job_summary_uploaded_product_row_count_excludes_total_rows_and_keeps_auto_deleted_duplicates():
    headers = {"X-Role": "admin"}
    first_job_id = _create_job(headers)
    duplicate_job_id = _create_job(headers)
    order_no = f"DOC-ROW-COUNT-{uuid4().hex[:8]}"
    contract_no = f"HT-ROW-COUNT-{uuid4().hex[:8]}"
    item_code = f"ITEM-ROW-COUNT-{uuid4().hex[:8]}"

    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,{contract_no},{order_no},1,{item_code},产品ROW-COUNT,100,1000,2026-03-18,2026-03-20,2026-03-19,80,50,30\n"
        + ",".join(["总计", "", "", "", "", "", "100", "1000", "", "", "", "", "", ""])
        + "\n"
    )
    _upload_order_csv(first_job_id, csv_content, headers, filename="row-count.csv")
    _upload_order_csv(duplicate_job_id, csv_content, headers, filename="row-count.csv")

    upload_summary = client.get("/v1/upload-jobs/summary", headers=headers)
    assert upload_summary.status_code == 200
    admin_summary = client.get("/v1/admin/jobs?page=1&size=100", headers=headers)
    assert admin_summary.status_code == 200

    upload_by_job = {item["job_id"]: item for item in upload_summary.json()["items"]}
    admin_by_job = {item["job_id"]: item for item in admin_summary.json()["items"]}

    assert upload_by_job[first_job_id]["uploaded_product_row_count"] == 1
    assert admin_by_job[first_job_id]["uploaded_product_row_count"] == 1
    assert upload_by_job[first_job_id]["total_record_count"] == 1

    assert upload_by_job[duplicate_job_id]["uploaded_product_row_count"] == 1
    assert admin_by_job[duplicate_job_id]["uploaded_product_row_count"] == 1
    assert upload_by_job[duplicate_job_id]["total_record_count"] == 0
    assert upload_by_job[duplicate_job_id]["auto_deleted_duplicate_count"] == 1


def test_upload_job_summary_supports_top_region_lifecycle_views():
    headers = {"X-Role": "admin"}

    current_job_id = _create_job(headers)
    empty_job_id = _create_job(headers)
    recycle_job_id = _create_job(headers)
    review_job_id = _create_job(headers)
    archived_job_id = _create_job(headers)
    special_job_id = _create_job(headers)

    current_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-TOP-CURRENT-{uuid4().hex[:8]},DOC-TOP-CURRENT-{uuid4().hex[:8]},1,ITEM-TOP-CURRENT,产品TOP-CURRENT,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    recycle_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-TOP-RECYCLE-{uuid4().hex[:8]},DOC-TOP-RECYCLE-{uuid4().hex[:8]},1,ITEM-TOP-RECYCLE,产品TOP-RECYCLE,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    review_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-TOP-REVIEW-{uuid4().hex[:8]},DOC-TOP-REVIEW-{uuid4().hex[:8]},1,ITEM-TOP-REVIEW,,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    archived_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-TOP-ARCHIVE-{uuid4().hex[:8]},DOC-TOP-ARCHIVE-{uuid4().hex[:8]},1,ITEM-TOP-ARCHIVE,产品TOP-ARCHIVE,100,1000,2026-03-18,2026-03-20,2026-03-19,100,100,0\n"
    )
    special_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-TOP-SPECIAL-{uuid4().hex[:8]},DOC-TOP-SPECIAL-{uuid4().hex[:8]},1,ITEM-TOP-SPECIAL,产品TOP-SPECIAL,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )

    _upload_order_csv(current_job_id, current_csv, headers, filename="top-current.csv")
    recycle_file_id = _upload_order_csv(recycle_job_id, recycle_csv, headers, filename="top-recycle.csv")
    _upload_order_csv(review_job_id, review_csv, headers, filename="top-review.csv")
    _upload_order_csv(archived_job_id, archived_csv, headers, filename="top-archived.csv")
    special_file_id = _upload_order_csv(special_job_id, special_csv, headers, filename="top-special.csv")

    recycle_resp = client.post(
        f"/v1/admin/files/{recycle_file_id}/soft-delete",
        headers=headers,
        json={"reason": "区域视角测试"},
    )
    assert recycle_resp.status_code == 200

    special_record_id = client.get(f"/v1/admin/files/{special_file_id}/records", headers=headers).json()["items"][0]["record_id"]
    special_resp = client.post(
        f"/v1/admin/records/{special_record_id}/special-case",
        headers=headers,
        json={"special_case_reason": "其他特殊完成", "special_case_note": "区域视角测试"},
    )
    assert special_resp.status_code == 200

    current_view = client.get("/v1/upload-jobs/summary?batch_view=all&lifecycle_view=current", headers=headers)
    assert current_view.status_code == 200
    assert current_view.json()["lifecycle_view"] == "current"
    current_ids = {item["job_id"] for item in current_view.json()["items"]}
    assert current_job_id in current_ids
    assert review_job_id not in current_ids
    assert empty_job_id in current_ids
    assert recycle_job_id not in current_ids
    assert archived_job_id not in current_ids
    assert special_job_id not in current_ids

    current_non_empty = client.get("/v1/upload-jobs/summary?batch_view=non_empty&lifecycle_view=current", headers=headers)
    assert current_non_empty.status_code == 200
    assert empty_job_id not in {item["job_id"] for item in current_non_empty.json()["items"]}

    review_view = client.get("/v1/upload-jobs/summary?batch_view=all&lifecycle_view=review_queue", headers=headers)
    assert review_view.status_code == 200
    review_ids = {item["job_id"] for item in review_view.json()["items"]}
    assert review_job_id in review_ids
    assert current_job_id not in review_ids
    assert recycle_job_id not in review_ids
    assert archived_job_id not in review_ids
    assert special_job_id not in review_ids

    recycle_view = client.get("/v1/upload-jobs/summary?batch_view=all&lifecycle_view=recycle_bin", headers=headers)
    assert recycle_view.status_code == 200
    recycle_ids = {item["job_id"] for item in recycle_view.json()["items"]}
    assert recycle_job_id in recycle_ids
    assert current_job_id not in recycle_ids
    assert review_job_id not in recycle_ids
    assert archived_job_id not in recycle_ids
    assert special_job_id not in recycle_ids

    archived_view = client.get("/v1/upload-jobs/summary?batch_view=all&lifecycle_view=archived", headers=headers)
    assert archived_view.status_code == 200
    archived_ids = {item["job_id"] for item in archived_view.json()["items"]}
    assert archived_job_id in archived_ids
    assert current_job_id not in archived_ids
    assert review_job_id not in archived_ids
    assert recycle_job_id not in archived_ids
    assert special_job_id not in archived_ids

    special_view = client.get("/v1/upload-jobs/summary?batch_view=all&lifecycle_view=special_case", headers=headers)
    assert special_view.status_code == 200
    special_ids = {item["job_id"] for item in special_view.json()["items"]}
    assert special_job_id in special_ids
    assert current_job_id not in special_ids
    assert review_job_id not in special_ids
    assert recycle_job_id not in special_ids
    assert archived_job_id not in special_ids


def test_upload_job_summary_supports_top_business_views():
    headers = {"X-Role": "admin"}

    unshipped_job_id = _create_job(headers)
    uninvoiced_job_id = _create_job(headers)
    no_ship_job_id = _create_job(headers)
    completed_job_id = _create_job(headers)

    unshipped_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-BIZ-UNSHIPPED-{uuid4().hex[:8]},DOC-BIZ-UNSHIPPED-{uuid4().hex[:8]},1,ITEM-BIZ-UNSHIPPED,产品BIZ-UNSHIPPED,100,1000,2026-03-18,2026-03-20,2026-03-19,60,60,40\n"
    )
    uninvoiced_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-BIZ-UNINVOICED-{uuid4().hex[:8]},DOC-BIZ-UNINVOICED-{uuid4().hex[:8]},1,ITEM-BIZ-UNINVOICED,产品BIZ-UNINVOICED,100,1000,2026-03-18,2026-03-20,2026-03-19,100,40,60\n"
    )
    no_ship_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-BIZ-NOSHIP-{uuid4().hex[:8]},DOC-BIZ-NOSHIP-{uuid4().hex[:8]},1,ITEM-BIZ-NOSHIP,产品BIZ-NOSHIP,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    completed_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-BIZ-COMPLETE-{uuid4().hex[:8]},DOC-BIZ-COMPLETE-{uuid4().hex[:8]},1,ITEM-BIZ-COMPLETE,产品BIZ-COMPLETE,100,1000,2026-03-18,2026-03-20,2026-03-19,100,100,0\n"
    )

    _upload_order_csv(unshipped_job_id, unshipped_csv, headers, filename="biz-unshipped.csv")
    _upload_order_csv(uninvoiced_job_id, uninvoiced_csv, headers, filename="biz-uninvoiced.csv")
    _upload_order_csv(no_ship_job_id, no_ship_csv, headers, filename="biz-noship.csv")
    _upload_order_csv(completed_job_id, completed_csv, headers, filename="biz-completed.csv")

    unshipped_view = client.get(
        "/v1/upload-jobs/summary?batch_view=all&lifecycle_view=current&business_view=unshipped",
        headers=headers,
    )
    assert unshipped_view.status_code == 200
    assert unshipped_view.json()["business_view"] == "unshipped"
    unshipped_ids = {item["job_id"] for item in unshipped_view.json()["items"]}
    assert unshipped_job_id in unshipped_ids
    assert no_ship_job_id in unshipped_ids
    assert uninvoiced_job_id not in unshipped_ids
    assert completed_job_id not in unshipped_ids

    uninvoiced_view = client.get(
        "/v1/upload-jobs/summary?batch_view=all&lifecycle_view=current&business_view=uninvoiced",
        headers=headers,
    )
    assert uninvoiced_view.status_code == 200
    assert uninvoiced_view.json()["business_view"] == "uninvoiced"
    uninvoiced_ids = {item["job_id"] for item in uninvoiced_view.json()["items"]}
    assert uninvoiced_job_id in uninvoiced_ids
    assert unshipped_job_id not in uninvoiced_ids
    assert no_ship_job_id not in uninvoiced_ids
    assert completed_job_id not in uninvoiced_ids


def test_job_and_file_anomaly_labels_cover_missing_columns_parse_failed_and_review_queue():
    headers = {"X-Role": "admin"}

    missing_identity_job_id = _create_job(headers)
    missing_identity_csv = (
        "客户,合同号,单据编号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        "A客户,HT-ANOM-ID,DOC-ANOM-ID,ITEM-ANOM-ID,产品ANOM-ID,100,1000,2026-03-18,2026-03-20,2026-03-19,80,50,30\n"
    )
    missing_identity_file_id = _upload_order_csv(
        missing_identity_job_id, missing_identity_csv, headers, filename="anom-missing-identity.csv"
    )

    missing_status_job_id = _create_job(headers)
    missing_status_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量\n"
        "A客户,HT-ANOM-STATUS,DOC-ANOM-STATUS,1,ITEM-ANOM-STATUS,产品ANOM-STATUS,100,1000,2026-03-18,2026-03-20,2026-03-19,80,50\n"
    )
    _upload_order_csv(missing_status_job_id, missing_status_csv, headers, filename="anom-missing-status.csv")

    parse_failed_job_id = _create_job(headers)
    parse_failed_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        "A客户,HT-ANOM-PARSE,DOC-ANOM-PARSE,1,ITEM-ANOM-PARSE,产品ANOM-PARSE,100,1000,2026-03-18,2026-03-20,2026-03-19,80,50,30\n"
    )
    parse_failed_file_id = _upload_order_csv(parse_failed_job_id, parse_failed_csv, headers, filename="anom-parse-failed.csv")
    with SessionLocal() as db:
        uploaded_file = db.get(UploadedFile, parse_failed_file_id)
        assert uploaded_file is not None
        uploaded_file.parse_status = "failed"
        uploaded_file.parse_error = "模拟解析失败"
        db.commit()

    upload_summary = client.get("/v1/upload-jobs/summary", headers=headers)
    assert upload_summary.status_code == 200
    upload_by_job = {item["job_id"]: item for item in upload_summary.json()["items"]}

    assert upload_by_job[missing_identity_job_id]["has_anomaly"] is True
    assert "missing_identity_columns" in upload_by_job[missing_identity_job_id]["anomaly_codes"]
    assert "review_queue" in upload_by_job[missing_identity_job_id]["anomaly_codes"]

    assert upload_by_job[missing_status_job_id]["has_anomaly"] is True
    assert "missing_status_columns" in upload_by_job[missing_status_job_id]["anomaly_codes"]
    assert "review_queue" in upload_by_job[missing_status_job_id]["anomaly_codes"]

    assert upload_by_job[parse_failed_job_id]["has_anomaly"] is True
    assert "parse_failed" in upload_by_job[parse_failed_job_id]["anomaly_codes"]

    files = client.get(f"/v1/admin/jobs/{missing_identity_job_id}/files", headers=headers)
    assert files.status_code == 200
    file_item = next(item for item in files.json()["items"] if item["file_id"] == missing_identity_file_id)
    assert file_item["has_anomaly"] is True
    assert "missing_identity_columns" in file_item["anomaly_codes"]
    assert "review_queue" in file_item["anomaly_codes"]


def test_admin_review_queue_and_batch_duplicate_cleanup_counts():
    headers = {"X-Role": "admin"}
    order_no = f"DOC-REVIEW-QUEUE-{uuid4().hex[:8]}"

    strict_job_id = _create_job(headers)
    strict_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-REVIEW,{order_no},1,ITEM-REVIEW,产品REVIEW,100,1000,2026-03-18,2026-03-20,2026-03-19,80,50,30\n"
    )
    _upload_order_csv(strict_job_id, strict_csv, headers, filename="review-base.csv")

    duplicate_job_id = _create_job(headers)
    duplicate_file_id = _upload_order_csv(duplicate_job_id, strict_csv, headers, filename="review-duplicate.csv")

    legacy_job_id = _create_job(headers)
    legacy_csv = (
        "客户,合同号,单据编号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-REVIEW,{order_no},ITEM-REVIEW,产品REVIEW,100,1000,2026-03-18,2026-03-20,2026-03-19,80,50,30\n"
    )
    legacy_file_id = _upload_order_csv(legacy_job_id, legacy_csv, headers, filename="review-legacy.csv")
    legacy_record_id = client.get(f"/v1/admin/files/{legacy_file_id}/records", headers=headers).json()["items"][0]["record_id"]

    upload_summary = client.get("/v1/upload-jobs/summary", headers=headers)
    assert upload_summary.status_code == 200
    admin_summary = client.get("/v1/admin/jobs?page=1&size=100", headers=headers)
    assert admin_summary.status_code == 200

    upload_by_job = {item["job_id"]: item for item in upload_summary.json()["items"]}
    admin_by_job = {item["job_id"]: item for item in admin_summary.json()["items"]}
    assert upload_by_job[duplicate_job_id]["auto_deleted_duplicate_count"] == 1
    assert admin_by_job[duplicate_job_id]["auto_deleted_duplicate_count"] == 1
    assert upload_by_job[legacy_job_id]["auto_deleted_duplicate_count"] == 0
    assert upload_by_job[legacy_job_id]["review_queue_record_count"] == 1
    assert admin_by_job[legacy_job_id]["review_queue_record_count"] == 1
    assert upload_by_job[legacy_job_id]["review_released_record_count"] == 0

    file_detail = client.get(f"/v1/admin/files/{duplicate_file_id}", headers=headers)
    assert file_detail.status_code == 200
    assert file_detail.json()["auto_deleted_duplicate_count"] == 1

    review_queue = client.get("/v1/admin/review-queue?object_type=record", headers=headers)
    assert review_queue.status_code == 200
    review_items = review_queue.json()["items"]
    review_ids = {item["object_id"] for item in review_items}
    assert legacy_record_id in review_ids
    legacy_item = next(item for item in review_items if item["object_id"] == legacy_record_id)
    assert legacy_item["identity_mode"] == "legacy_fallback"
    assert legacy_item["version_status"] == "review_pending"

    recycle_ids = {item["object_id"] for item in client.get("/v1/admin/recycle-bin?object_type=record", headers=headers).json()["items"]}
    archived_ids = {item["object_id"] for item in client.get("/v1/admin/archived?object_type=record", headers=headers).json()["items"]}
    assert legacy_record_id not in recycle_ids
    assert legacy_record_id not in archived_ids

    review_queue_files = client.get("/v1/admin/review-queue?object_type=file", headers=headers)
    assert review_queue_files.status_code == 200
    review_file_ids = {item["object_id"] for item in review_queue_files.json()["items"]}
    assert legacy_file_id in review_file_ids

    review_queue_all = client.get("/v1/admin/review-queue?object_type=all", headers=headers)
    assert review_queue_all.status_code == 200
    all_items = review_queue_all.json()["items"]
    assert any(item["object_type"] == "file" and item["object_id"] == legacy_file_id for item in all_items)
    assert any(item["object_type"] == "record" and item["object_id"] == legacy_record_id for item in all_items)

    review_files = client.get(f"/v1/admin/jobs/{legacy_job_id}/files?lifecycle_view=review_queue", headers=headers)
    assert review_files.status_code == 200
    assert review_files.json()["count"] == 1
    assert review_files.json()["items"][0]["file_id"] == legacy_file_id
    assert review_files.json()["items"][0]["review_queue_record_count"] == 1
    assert review_files.json()["items"][0]["review_released_record_count"] == 0

    review_overview = client.get(f"/v1/admin/jobs/{legacy_job_id}/overview?lifecycle_view=review_queue", headers=headers)
    assert review_overview.status_code == 200
    assert review_overview.json()["normalized_record_count"] == 1
    assert review_overview.json()["open_alert_count"] == 0


def test_operations_summary_exposes_health_backup_alert_and_archive_entry():
    headers = {"X-Role": "admin"}

    policy_resp = client.get("/v1/config/operations_monitoring_policy", headers=headers)
    assert policy_resp.status_code == 200
    assert "db_backup_enabled" in policy_resp.json()["value"]
    assert "db_backup_target_path" in policy_resp.json()["value"]
    assert "file_backup_enabled" in policy_resp.json()["value"]
    assert "file_backup_target_path" in policy_resp.json()["value"]
    assert "log_cleanup_enabled" in policy_resp.json()["value"]
    assert "log_cleanup_schedule_time" in policy_resp.json()["value"]
    assert "log_retention_days" in policy_resp.json()["value"]
    runtime_resp = client.get("/v1/config/operations_runtime_status", headers=headers)
    assert runtime_resp.status_code == 200
    assert "last_started_at" in runtime_resp.json()["value"]["db_backup"]
    assert "last_finished_at" in runtime_resp.json()["value"]["db_backup"]
    assert "last_snapshot_label" in runtime_resp.json()["value"]["db_backup"]
    assert "last_started_at" in runtime_resp.json()["value"]["file_backup"]
    assert "last_finished_at" in runtime_resp.json()["value"]["file_backup"]
    assert "last_snapshot_label" in runtime_resp.json()["value"]["file_backup"]
    assert "last_started_at" in runtime_resp.json()["value"]["log_cleanup"]
    assert "last_finished_at" in runtime_resp.json()["value"]["log_cleanup"]
    assert "last_removed_file_count" in runtime_resp.json()["value"]["log_cleanup"]

    client.put(
        "/v1/config/operations_monitoring_policy",
        headers=headers,
        json={
            "value": {
                "review_queue_warn_threshold": 1,
                "parse_failed_warn_threshold": 1,
                "failed_task_warn_threshold": 1,
                "backup_overdue_hours": 1,
                "archive_run_overdue_hours": 1,
                "db_backup_enabled": True,
                "db_backup_target_path": "/backup/db",
                "file_backup_enabled": True,
                "file_backup_target_path": "/backup/uploads",
            }
        },
    )
    active_job_id = _create_job(headers)
    active_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        "A客户,HT-OPS-A,DOC-OPS-A,1,ITEM-OPS-A,产品OPS-A,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    _upload_order_csv(active_job_id, active_csv, headers, filename="ops-active.csv")

    review_job_id = _create_job(headers)
    review_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        "A客户,HT-OPS-REVIEW,DOC-OPS-REVIEW,1,ITEM-OPS-REVIEW,,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    _upload_order_csv(review_job_id, review_csv, headers, filename="ops-review.csv")

    parse_failed_job_id = _create_job(headers)
    parse_failed_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        "A客户,HT-OPS-FAIL,DOC-OPS-FAIL,1,ITEM-OPS-FAIL,产品OPS-FAIL,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    parse_failed_file_id = _upload_order_csv(parse_failed_job_id, parse_failed_csv, headers, filename="ops-parse-failed.csv")

    recycle_job_id = _create_job(headers)
    recycle_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        "A客户,HT-OPS-RECYCLE,DOC-OPS-RECYCLE,1,ITEM-OPS-RECYCLE,产品OPS-RECYCLE,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    recycle_file_id = _upload_order_csv(recycle_job_id, recycle_csv, headers, filename="ops-recycle.csv")
    recycle_resp = client.post(
        f"/v1/admin/files/{recycle_file_id}/soft-delete",
        headers=headers,
        json={"reason": "运营状态测试"},
    )
    assert recycle_resp.status_code == 200

    with SessionLocal() as db:
        uploaded_file = db.get(UploadedFile, parse_failed_file_id)
        assert uploaded_file is not None
        uploaded_file.parse_status = "failed"
        uploaded_file.parse_error = "模拟解析失败"

        recycle_file = db.get(UploadedFile, recycle_file_id)
        assert recycle_file is not None
        recycle_file.deleted_at = datetime.now(timezone.utc) - timedelta(days=120)
        recycle_record = (
            db.query(NormalizedRecord)
            .filter(NormalizedRecord.file_id == recycle_file_id)
            .order_by(NormalizedRecord.created_at.asc())
            .first()
        )
        assert recycle_record is not None
        recycle_record.deleted_at = datetime.now(timezone.utc) - timedelta(days=120)

        failed_task = TaskRun(
            job_id=parse_failed_job_id,
            task_type="orchestrate",
            status="failed",
            input_json={"job_id": parse_failed_job_id},
            output_json={},
            error="模拟任务失败",
            created_by="admin",
        )
        db.add(failed_task)
        db.commit()

    client.put(
        "/v1/config/operations_runtime_status",
        headers=headers,
        json={
            "value": {
                "db_backup": {
                    "last_success_at": None,
                    "last_status": "failed",
                    "last_error": "db-backup-error",
                    "last_started_at": "2026-03-20T00:00:00+00:00",
                    "last_finished_at": "2026-03-20T00:10:00+00:00",
                    "last_snapshot_label": "db-backup-20260320.sql.gz",
                },
                "file_backup": {
                    "last_success_at": "2026-03-20T00:00:00+00:00",
                    "last_status": "succeeded",
                    "last_error": "",
                    "last_started_at": "2026-03-20T01:00:00+00:00",
                    "last_finished_at": "2026-03-20T01:20:00+00:00",
                    "last_snapshot_label": "uploads-backup-20260320.tar.gz",
                },
                "archive_run": {
                    "last_run_at": "2026-03-20T00:00:00+00:00",
                    "last_status": "failed",
                    "last_error": "archive-run-error",
                    "last_archived_file_count": 0,
                    "last_archived_record_count": 0,
                },
            }
        },
    )

    summary = client.get("/v1/admin/operations/summary", headers=headers)
    assert summary.status_code == 200
    payload = summary.json()

    assert payload["health"]["total_jobs"] >= 4
    assert payload["health"]["review_queue_jobs"] >= 1
    assert payload["health"]["parse_failed_jobs"] >= 1
    assert payload["health"]["failed_task_jobs"] >= 1
    assert payload["backup"]["db_backup"]["enabled"] is True
    assert payload["backup"]["db_backup"]["target_path"] == "/backup/db"
    assert payload["backup"]["db_backup"]["last_status"] == "failed"
    assert payload["backup"]["db_backup"]["last_started_at"] == "2026-03-20T00:00:00+00:00"
    assert payload["backup"]["db_backup"]["last_finished_at"] == "2026-03-20T00:10:00+00:00"
    assert payload["backup"]["db_backup"]["last_snapshot_label"] == "db-backup-20260320.sql.gz"
    assert payload["backup"]["file_backup"]["enabled"] is True
    assert payload["backup"]["file_backup"]["target_path"] == "/backup/uploads"
    assert payload["backup"]["file_backup"]["is_overdue"] is True
    assert payload["backup"]["file_backup"]["last_snapshot_label"] == "uploads-backup-20260320.tar.gz"
    assert payload["performance"]["slow_request_threshold_ms"] == 1500
    assert payload["performance"]["slow_request_keep_latest"] == 10
    assert payload["performance"]["slow_requests"]["total_count"] == 0
    assert payload["logs"]["log_cleanup"]["enabled"] is True
    assert payload["logs"]["log_cleanup"]["schedule_time"] == "03:00"
    assert payload["logs"]["log_cleanup"]["retention_days"] == 30
    assert payload["restore_drill"]["restore_drill"]["last_status"] == "unknown"
    assert "available_db_snapshot_label" in payload["restore_drill"]["restore_drill"]
    assert "available_file_snapshot_label" in payload["restore_drill"]["restore_drill"]
    assert payload["restore_drill"]["restore_drill"]["connection_ready"] is True
    assert payload["restore_drill"]["restore_drill"]["connection_source"] == "primary_sqlite"
    assert payload["archive"]["mode"] == "auto"
    assert payload["archive"]["candidate_record_count"] >= 0
    assert payload["archive"]["auto_archive_rule"] == "当前有效订单且发齐=数量、开齐=数量"
    assert payload["archive"]["archive_preview"]["last_status"] == "never"
    review_alert = next(item for item in payload["alerts"] if item["code"] == "review_queue_threshold")
    assert review_alert["title"] == "待复核积压"
    assert review_alert["current_value"] >= 1
    assert review_alert["threshold_value"] == 1
    assert review_alert["suggestion"]
    db_backup_alert = next(item for item in payload["alerts"] if item["code"] == "db_backup_failed")
    assert db_backup_alert["title"] == "数据库备份失败"
    assert db_backup_alert["suggestion"]
    codes = {item["code"] for item in payload["alerts"]}
    assert "review_queue_threshold" in codes
    assert "parse_failed_jobs_threshold" in codes
    assert "failed_task_jobs_threshold" in codes
    assert "db_backup_failed" in codes
    assert "file_backup_overdue" in codes
    assert "archive_run_failed" in codes


def test_manual_backup_run_updates_runtime_and_creates_local_files():
    headers = {"X-Role": "admin"}
    db_dir = Path("/tmp/hanyu-backup-test/db")
    uploads_dir = Path("/tmp/hanyu-backup-test/uploads")
    db_dir.mkdir(parents=True, exist_ok=True)
    uploads_dir.mkdir(parents=True, exist_ok=True)

    client.put(
        "/v1/config/operations_monitoring_policy",
        headers=headers,
        json={
            "value": {
                "db_backup_enabled": True,
                "db_backup_target_path": str(db_dir),
                "file_backup_enabled": True,
                "file_backup_target_path": str(uploads_dir),
                "backup_schedule_time": "02:00",
                "backup_retention_days": 30,
                "review_queue_warn_threshold": 10,
                "parse_failed_warn_threshold": 1,
                "failed_task_warn_threshold": 1,
                "backup_overdue_hours": 24,
                "archive_run_overdue_hours": 24,
            }
        },
    )

    job_id = _create_job(headers)
    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        "A客户,HT-BACKUP,DOC-BACKUP,1,ITEM-BACKUP,产品BACKUP,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    _upload_order_csv(job_id, csv_content, headers, filename="backup-source.csv")

    db_run = client.post("/v1/admin/operations/backup/database/run", headers=headers, json={})
    assert db_run.status_code == 200
    db_payload = db_run.json()
    assert db_payload["status"] == "succeeded"
    assert Path(db_payload["output_path"]).exists()

    file_run = client.post("/v1/admin/operations/backup/files/run", headers=headers, json={})
    assert file_run.status_code == 200
    file_payload = file_run.json()
    assert file_payload["status"] == "succeeded"
    assert Path(file_payload["output_path"]).exists()

    runtime = client.get("/v1/config/operations_runtime_status", headers=headers)
    assert runtime.status_code == 200
    runtime_value = runtime.json()["value"]
    assert runtime_value["db_backup"]["last_status"] == "succeeded"
    assert runtime_value["db_backup"]["last_snapshot_label"]
    assert runtime_value["db_backup"]["last_finished_at"]
    assert runtime_value["file_backup"]["last_status"] == "succeeded"
    assert runtime_value["file_backup"]["last_snapshot_label"]
    assert runtime_value["file_backup"]["last_finished_at"]

    operations = client.get("/v1/admin/operations/summary", headers=headers)
    assert operations.status_code == 200
    backup_block = operations.json()["backup"]
    assert backup_block["db_backup"]["enabled"] is True
    assert backup_block["db_backup"]["target_path"] == str(db_dir)
    assert backup_block["db_backup"]["schedule_time"] == "02:00"
    assert backup_block["db_backup"]["retention_days"] == 30
    assert backup_block["file_backup"]["enabled"] is True
    assert backup_block["file_backup"]["target_path"] == str(uploads_dir)
    assert backup_block["file_backup"]["schedule_time"] == "02:00"
    assert backup_block["file_backup"]["retention_days"] == 30


def test_manual_log_cleanup_updates_runtime_and_operations_summary():
    headers = {"X-Role": "admin"}
    temp_log_dir = Path("/tmp/hanyu-log-cleanup-test")
    if temp_log_dir.exists():
        shutil.rmtree(temp_log_dir)
    temp_log_dir.mkdir(parents=True, exist_ok=True)

    recent_file = temp_log_dir / "application.log"
    recent_file.write_text("recent log\n", encoding="utf-8")
    old_file = temp_log_dir / "application.log.2026-01-01"
    old_file.write_text("old log\n", encoding="utf-8")
    old_timestamp = time.time() - 40 * 24 * 3600
    os.utime(old_file, (old_timestamp, old_timestamp))

    original_log_root = log_retention_service.LOG_ROOT_DIR
    log_retention_service.LOG_ROOT_DIR = temp_log_dir
    try:
        client.put(
            "/v1/config/operations_monitoring_policy",
            headers=headers,
            json={
                "value": {
                    "log_cleanup_enabled": True,
                    "log_cleanup_schedule_time": "03:00",
                    "log_retention_days": 30,
                }
            },
        )

        cleanup = client.post("/v1/admin/operations/logs/cleanup/run", headers=headers, json={})
        assert cleanup.status_code == 200
        cleanup_payload = cleanup.json()
        assert cleanup_payload["status"] == "succeeded"
        assert cleanup_payload["removed_file_count"] == 1
        assert cleanup_payload["remaining_file_count"] >= 1
        assert not old_file.exists()
        assert recent_file.exists()

        runtime = client.get("/v1/config/operations_runtime_status", headers=headers)
        assert runtime.status_code == 200
        log_cleanup = runtime.json()["value"]["log_cleanup"]
        assert log_cleanup["last_status"] == "succeeded"
        assert log_cleanup["last_removed_file_count"] == 1
        assert log_cleanup["last_finished_at"]

        operations = client.get("/v1/admin/operations/summary", headers=headers)
        assert operations.status_code == 200
        logs_block = operations.json()["logs"]["log_cleanup"]
        assert logs_block["target_path"] == str(temp_log_dir)
        assert logs_block["schedule_time"] == "03:00"
        assert logs_block["retention_days"] == 30
        assert logs_block["current_file_count"] >= 1
        assert logs_block["last_removed_file_count"] == 1
    finally:
        log_retention_service.LOG_ROOT_DIR = original_log_root
        shutil.rmtree(temp_log_dir, ignore_errors=True)


def test_manual_restore_drill_uses_latest_backups_and_updates_runtime():
    headers = {"X-Role": "admin"}
    db_dir = Path("/tmp/hanyu-restore-drill-test/db")
    uploads_dir = Path("/tmp/hanyu-restore-drill-test/uploads")
    shutil.rmtree(db_dir.parent, ignore_errors=True)
    db_dir.mkdir(parents=True, exist_ok=True)
    uploads_dir.mkdir(parents=True, exist_ok=True)

    client.put(
        "/v1/config/operations_monitoring_policy",
        headers=headers,
        json={
            "value": {
                "db_backup_enabled": True,
                "db_backup_target_path": str(db_dir),
                "file_backup_enabled": True,
                "file_backup_target_path": str(uploads_dir),
                "backup_schedule_time": "02:00",
                "backup_retention_days": 30,
            }
        },
    )

    job_id = _create_job(headers)
    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        "A客户,HT-RESTORE-DRILL,DOC-RESTORE-DRILL,1,ITEM-RESTORE-DRILL,产品RESTORE-DRILL,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    _upload_order_csv(job_id, csv_content, headers, filename="restore-drill-source.csv")

    db_run = client.post("/v1/admin/operations/backup/database/run", headers=headers, json={})
    assert db_run.status_code == 200
    file_run = client.post("/v1/admin/operations/backup/files/run", headers=headers, json={})
    assert file_run.status_code == 200

    drill = client.post("/v1/admin/operations/restore-drill/run", headers=headers, json={})
    assert drill.status_code == 200
    drill_payload = drill.json()
    assert drill_payload["status"] == "succeeded"
    assert drill_payload["db_snapshot_label"]
    assert drill_payload["file_snapshot_label"]
    assert drill_payload["restored_job_count"] >= 1
    assert drill_payload["restored_uploaded_file_row_count"] >= 1
    assert drill_payload["restored_record_count"] >= 1
    assert drill_payload["restored_storage_file_count"] >= 1

    runtime = client.get("/v1/config/operations_runtime_status", headers=headers)
    assert runtime.status_code == 200
    restore_block = runtime.json()["value"]["restore_drill"]
    assert restore_block["last_status"] == "succeeded"
    assert restore_block["last_db_snapshot_label"] == drill_payload["db_snapshot_label"]
    assert restore_block["last_file_snapshot_label"] == drill_payload["file_snapshot_label"]
    assert restore_block["last_restored_record_count"] >= 1

    operations = client.get("/v1/admin/operations/summary", headers=headers)
    assert operations.status_code == 200
    summary_restore = operations.json()["restore_drill"]["restore_drill"]
    assert summary_restore["last_status"] == "succeeded"
    assert summary_restore["available_db_snapshot_label"] == drill_payload["db_snapshot_label"]
    assert summary_restore["available_file_snapshot_label"] == drill_payload["file_snapshot_label"]
    assert summary_restore["last_restored_storage_file_count"] >= 1


def test_restore_drill_requires_dedicated_database_url_for_mariadb():
    original_database_url = settings.database_url
    try:
        settings.database_url = "mysql+pymysql://main_user:pw@127.0.0.1:3306/sales_warning_v1?charset=utf8mb4"
        try:
            restore_drill_service._ensure_restore_drill_database_url({})
            assert False, "expected dedicated restore-drill URL requirement"
        except RuntimeError as exc:
            assert "dedicated restore-drill database URL" in str(exc)

        try:
            restore_drill_service._ensure_restore_drill_database_url(
                {"restore_drill_database_url": "mysql+pymysql://main_user:pw@127.0.0.1:3306/sales_warning_v1?charset=utf8mb4"}
            )
            assert False, "expected primary DATABASE_URL reuse rejection"
        except RuntimeError as exc:
            assert "must not reuse the primary DATABASE_URL" in str(exc)

        resolved_url, source = restore_drill_service._ensure_restore_drill_database_url(
            {"restore_drill_database_url": "mysql+pymysql://restore_user:pw@127.0.0.1:3306/mysql?charset=utf8mb4"}
        )
        assert resolved_url.endswith("/mysql?charset=utf8mb4")
        assert source == "policy"
    finally:
        settings.database_url = original_database_url


def test_slow_request_runtime_status_is_recorded_and_exposed_in_operations_summary():
    headers = {"X-Role": "admin"}
    client.put(
        "/v1/config/operations_monitoring_policy",
        headers=headers,
        json={
            "value": {
                "slow_request_threshold_ms": 1,
                "slow_request_keep_latest": 5,
            }
        },
    )

    job_id = _create_job(headers)
    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        "A客户,HT-SLOW-OPS,DOC-SLOW-OPS,1,ITEM-SLOW-OPS,产品SLOW-OPS,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    _upload_order_csv(job_id, csv_content, headers, filename="slow-ops.csv")

    jobs = client.get("/v1/admin/jobs?page=1&size=50", headers=headers)
    assert jobs.status_code == 200

    operations = client.get("/v1/admin/operations/summary", headers=headers)
    assert operations.status_code == 200
    performance = operations.json()["performance"]
    slow_block = performance["slow_requests"]
    assert performance["slow_request_threshold_ms"] == 1
    assert performance["slow_request_keep_latest"] == 5
    assert slow_block["total_count"] >= 1
    assert slow_block["last_seen_at"]
    assert any(item["path"] == "/v1/admin/jobs" for item in slow_block["recent_items"])


def test_missing_identity_value_enters_review_queue_and_does_not_affect_existing_current_chain():
    headers = {"X-Role": "admin"}
    order_no = f"DOC-REVIEW-DRIFT-ID-{uuid4().hex[:8]}"

    strict_job_id = _create_job(headers)
    strict_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-REVIEW-DRIFT-ID,{order_no},1,ITEM-REVIEW-DRIFT-ID,产品REVIEW-DRIFT-ID,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    strict_file_id = _upload_order_csv(strict_job_id, strict_csv, headers, filename="review-drift-id-base.csv")
    strict_record_id = client.get(f"/v1/admin/files/{strict_file_id}/records", headers=headers).json()["items"][0]["record_id"]

    drift_job_id = _create_job(headers)
    drift_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-REVIEW-DRIFT-ID,{order_no},1,ITEM-REVIEW-DRIFT-ID,,100,1000,2026-03-18,2026-03-20,2026-03-19,100,0,100\n"
    )
    drift_file_id = _upload_order_csv(drift_job_id, drift_csv, headers, filename="review-drift-id.csv")
    drift_record_id = client.get(f"/v1/admin/files/{drift_file_id}/records", headers=headers).json()["items"][0]["record_id"]

    review_queue = client.get("/v1/admin/review-queue?object_type=record", headers=headers)
    assert review_queue.status_code == 200
    review_item = next(item for item in review_queue.json()["items"] if item["object_id"] == drift_record_id)
    assert review_item["version_status"] == "review_pending"
    assert review_item["review_status"] == "pending_review"
    assert review_item["effective_status"] == "retained_not_effective"
    assert review_item["governance_reason"] == "pending_review_missing_identity_values:item_name"

    strict_overview = client.get(f"/v1/admin/jobs/{strict_job_id}/overview?lifecycle_view=current", headers=headers)
    assert strict_overview.status_code == 200
    assert strict_overview.json()["normalized_record_count"] == 1
    assert strict_overview.json()["open_alert_count"] == 1

    drift_overview = client.get(f"/v1/admin/jobs/{drift_job_id}/overview?lifecycle_view=current", headers=headers)
    assert drift_overview.status_code == 200
    assert drift_overview.json()["normalized_record_count"] == 1
    assert drift_overview.json()["open_alert_count"] == 0

    upload_summary = client.get("/v1/upload-jobs/summary", headers=headers)
    assert upload_summary.status_code == 200
    upload_by_job = {item["job_id"]: item for item in upload_summary.json()["items"]}
    assert "review_required_blank_values" in upload_by_job[drift_job_id]["anomaly_codes"]
    assert "review_queue" in upload_by_job[drift_job_id]["anomaly_codes"]
    assert "missing_identity_columns" not in upload_by_job[drift_job_id]["anomaly_codes"]

    drift_files = client.get(f"/v1/admin/jobs/{drift_job_id}/files", headers=headers)
    assert drift_files.status_code == 200
    drift_file_item = next(item for item in drift_files.json()["items"] if item["file_id"] == drift_file_id)
    assert "review_required_blank_values" in drift_file_item["anomaly_codes"]
    assert "review_queue" in drift_file_item["anomaly_codes"]
    assert "missing_identity_columns" not in drift_file_item["anomaly_codes"]

    strict_records = client.get(f"/v1/admin/files/{strict_file_id}/records", headers=headers)
    strict_item = next(item for item in strict_records.json()["items"] if item["record_id"] == strict_record_id)
    assert strict_item["effective_status"] == "current_effective"

    drift_records = client.get(f"/v1/admin/files/{drift_file_id}/records", headers=headers)
    drift_item = next(item for item in drift_records.json()["items"] if item["record_id"] == drift_record_id)
    assert drift_item["version_status"] == "review_pending"
    assert drift_item["review_status"] == "pending_review"
    assert drift_item["effective_status"] == "retained_not_effective"

    review_reason = client.get(f"/v1/admin/review-queue/records/{drift_record_id}/compare", headers=headers)
    assert review_reason.status_code == 200
    payload = review_reason.json()
    assert payload["review_record"]["record_id"] == drift_record_id
    assert payload["reason_code"] == "missing_identity_values"
    assert payload["reason_label"] == "认人字段有空值，系统先放入待复核区"
    assert payload["governance_reason"] == "pending_review_missing_identity_values:item_name"
    assert payload["missing_required_columns"] == []
    assert payload["blank_identity_values"] == ["item_name"]
    assert "primary_record" not in payload
    assert "identity_compare" not in payload
    assert "status_compare" not in payload


def test_review_queue_record_can_move_to_special_case_without_touching_current_chain():
    headers = {"X-Role": "admin"}
    order_no = f"DOC-REVIEW-SPECIAL-{uuid4().hex[:8]}"

    strict_job_id = _create_job(headers)
    strict_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-REVIEW-SPECIAL,{order_no},1,ITEM-REVIEW-SPECIAL,产品REVIEW-SPECIAL,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    strict_file_id = _upload_order_csv(strict_job_id, strict_csv, headers, filename="review-special-base.csv")
    strict_record_id = client.get(f"/v1/admin/files/{strict_file_id}/records", headers=headers).json()["items"][0]["record_id"]

    legacy_job_id = _create_job(headers)
    legacy_csv = (
        "客户,合同号,单据编号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-REVIEW-SPECIAL,{order_no},ITEM-REVIEW-SPECIAL,产品REVIEW-SPECIAL,100,1000,2026-03-18,2026-03-20,2026-03-19,100,100,0\n"
    )
    legacy_file_id = _upload_order_csv(legacy_job_id, legacy_csv, headers, filename="review-special-legacy.csv")
    legacy_record_id = client.get(f"/v1/admin/files/{legacy_file_id}/records", headers=headers).json()["items"][0]["record_id"]

    special_case = client.post(
        f"/v1/admin/records/{legacy_record_id}/special-case",
        headers=headers,
        json={"special_case_reason": "数量调整后完成", "special_case_note": "员工已确认"},
    )
    assert special_case.status_code == 200
    assert special_case.json()["lifecycle_state"] == "special_case"
    assert special_case.json()["version_status"] == "special_case_retained"
    assert special_case.json()["is_current_effective"] is False

    review_queue = client.get("/v1/admin/review-queue?object_type=record", headers=headers)
    assert review_queue.status_code == 200
    review_ids = {item["object_id"] for item in review_queue.json()["items"]}
    assert legacy_record_id not in review_ids

    special_items = client.get("/v1/admin/special-case?object_type=record", headers=headers)
    assert special_items.status_code == 200
    special_by_id = {item["object_id"]: item for item in special_items.json()["items"]}
    assert legacy_record_id in special_by_id
    assert special_by_id[legacy_record_id]["special_case_reason"] == "数量调整后完成"
    assert special_by_id[legacy_record_id]["special_case_note"] == "员工已确认"

    strict_overview = client.get(f"/v1/admin/jobs/{strict_job_id}/overview?lifecycle_view=current", headers=headers)
    assert strict_overview.status_code == 200
    assert strict_overview.json()["open_alert_count"] == 1

    strict_records = client.get(f"/v1/admin/files/{strict_file_id}/records", headers=headers)
    strict_item = next(item for item in strict_records.json()["items"] if item["record_id"] == strict_record_id)
    assert strict_item["effective_status"] == "current_effective"


def test_current_record_can_move_to_special_case_and_leave_current_scan():
    headers = {"X-Role": "admin"}
    order_no = f"DOC-CURRENT-SPECIAL-{uuid4().hex[:8]}"
    job_id = _create_job(headers)
    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-CURRENT-SPECIAL,{order_no},1,ITEM-CURRENT-SPECIAL,产品CURRENT-SPECIAL,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers, filename="current-special.csv")
    record_id = client.get(f"/v1/admin/files/{file_id}/records", headers=headers).json()["items"][0]["record_id"]

    move = client.post(
        f"/v1/admin/records/{record_id}/special-case",
        headers=headers,
        json={"special_case_reason": "质量问题协商后完成"},
    )
    assert move.status_code == 200
    assert move.json()["lifecycle_state"] == "special_case"
    assert move.json()["version_status"] == "special_case_retained"

    with SessionLocal() as db:
        record = db.get(NormalizedRecord, record_id)
        uploaded_file = db.get(UploadedFile, file_id)
        assert record is not None
        assert uploaded_file is not None
        assert record.version_status == "special_case_retained"
        assert record.is_current_effective is False
        assert record.lifecycle_state == "special_case"
        assert uploaded_file.lifecycle_state == "special_case"

    current_overview = client.get(f"/v1/admin/jobs/{job_id}/overview?lifecycle_view=current", headers=headers)
    assert current_overview.status_code == 200
    assert current_overview.json()["normalized_record_count"] == 0
    assert current_overview.json()["open_alert_count"] == 0

    special_items = client.get("/v1/admin/special-case?object_type=record", headers=headers)
    assert special_items.status_code == 200
    special_ids = {item["object_id"] for item in special_items.json()["items"]}
    assert record_id in special_ids


def test_legacy_update_enters_review_queue_and_does_not_change_current_effective_chain():
    headers = {"X-Role": "admin"}
    order_no = f"DOC-REVIEW-LEGACY-UPD-{uuid4().hex[:8]}"

    strict_job_id = _create_job(headers)
    strict_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-REVIEW-LEGACY-UPD,{order_no},1,ITEM-REVIEW-LEGACY-UPD,产品REVIEW-LEGACY-UPD,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    strict_file_id = _upload_order_csv(strict_job_id, strict_csv, headers, filename="review-legacy-update-base.csv")
    strict_record_id = client.get(f"/v1/admin/files/{strict_file_id}/records", headers=headers).json()["items"][0]["record_id"]

    legacy_job_id = _create_job(headers)
    legacy_update_csv = (
        "客户,合同号,单据编号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-REVIEW-LEGACY-UPD,{order_no},ITEM-REVIEW-LEGACY-UPD,产品REVIEW-LEGACY-UPD,100,1000,2026-03-18,2026-03-20,2026-03-19,100,0,100\n"
    )
    legacy_file_id = _upload_order_csv(legacy_job_id, legacy_update_csv, headers, filename="review-legacy-update.csv")
    legacy_record_id = client.get(f"/v1/admin/files/{legacy_file_id}/records", headers=headers).json()["items"][0]["record_id"]

    review_queue = client.get("/v1/admin/review-queue?object_type=record", headers=headers)
    assert review_queue.status_code == 200
    review_item = next(item for item in review_queue.json()["items"] if item["object_id"] == legacy_record_id)
    assert review_item["version_status"] == "review_pending"
    assert review_item["review_status"] == "pending_review"
    assert review_item["effective_status"] == "retained_not_effective"

    strict_overview = client.get(f"/v1/admin/jobs/{strict_job_id}/overview?lifecycle_view=current", headers=headers)
    assert strict_overview.status_code == 200
    assert strict_overview.json()["normalized_record_count"] == 1
    assert strict_overview.json()["open_alert_count"] == 1

    legacy_overview = client.get(f"/v1/admin/jobs/{legacy_job_id}/overview?lifecycle_view=current", headers=headers)
    assert legacy_overview.status_code == 200
    assert legacy_overview.json()["normalized_record_count"] == 1
    assert legacy_overview.json()["open_alert_count"] == 0

    strict_records = client.get(f"/v1/admin/files/{strict_file_id}/records", headers=headers)
    assert strict_records.status_code == 200
    strict_item = next(item for item in strict_records.json()["items"] if item["record_id"] == strict_record_id)
    assert strict_item["effective_status"] == "current_effective"

    legacy_records = client.get(f"/v1/admin/files/{legacy_file_id}/records", headers=headers)
    assert legacy_records.status_code == 200
    legacy_item = next(item for item in legacy_records.json()["items"] if item["record_id"] == legacy_record_id)
    assert legacy_item["version_status"] == "review_pending"
    assert legacy_item["review_status"] == "pending_review"
    assert legacy_item["effective_status"] == "retained_not_effective"
    assert legacy_item["change_type"] == "update"

    admin_summary = client.get("/v1/admin/jobs?page=1&size=100", headers=headers)
    assert admin_summary.status_code == 200
    admin_by_job = {item["job_id"]: item for item in admin_summary.json()["items"]}
    assert admin_by_job[strict_job_id]["current_effective_record_count"] == 1
    assert admin_by_job[legacy_job_id]["current_effective_record_count"] == 0
    assert admin_by_job[legacy_job_id]["review_queue_record_count"] == 1

    with SessionLocal() as db:
        strict_record = db.get(NormalizedRecord, strict_record_id)
        legacy_record = db.get(NormalizedRecord, legacy_record_id)
        assert strict_record is not None
        assert legacy_record is not None
        assert strict_record.version_status == "current"
        assert strict_record.is_current_effective is True
        assert legacy_record.version_status == "review_pending"
        assert legacy_record.is_current_effective is False


def test_review_queue_return_to_current_endpoints_are_removed():
    headers = {"X-Role": "admin"}
    order_no = f"DOC-REVIEW-NO-RETURN-{uuid4().hex[:8]}"

    strict_job_id = _create_job(headers)
    strict_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-REVIEW-NO-RETURN,{order_no},1,ITEM-REVIEW-NO-RETURN,产品REVIEW-NO-RETURN,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    _upload_order_csv(strict_job_id, strict_csv, headers, filename="review-no-return-base.csv")

    legacy_job_id = _create_job(headers)
    legacy_csv = (
        "客户,合同号,单据编号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-REVIEW-NO-RETURN,{order_no},ITEM-REVIEW-NO-RETURN,产品REVIEW-NO-RETURN,100,1000,2026-03-18,2026-03-20,2026-03-19,100,0,100\n"
    )
    legacy_file_id = _upload_order_csv(legacy_job_id, legacy_csv, headers, filename="review-no-return.csv")
    legacy_record_id = client.get(f"/v1/admin/files/{legacy_file_id}/records", headers=headers).json()["items"][0]["record_id"]

    keep_res = client.post(
        f"/v1/admin/review-queue/records/{legacy_record_id}/confirm-not-duplicate",
        headers=headers,
        json={"reason": "待复核区不再提供放回当前数据入口"},
    )
    assert keep_res.status_code == 404

    make_current_res = client.post(
        f"/v1/admin/review-queue/records/{legacy_record_id}/confirm-not-duplicate-make-current",
        headers=headers,
        json={"reason": "待复核区不再提供放回当前数据入口"},
    )
    assert make_current_res.status_code == 404

    confirm_duplicate_res = client.post(
        f"/v1/admin/review-queue/records/{legacy_record_id}/confirm-duplicate",
        headers=headers,
        json={"reason": "待复核区不再提供人工判重删除入口"},
    )
    assert confirm_duplicate_res.status_code == 404

    review_queue = client.get("/v1/admin/review-queue?object_type=record", headers=headers)
    assert review_queue.status_code == 200
    review_item = next(item for item in review_queue.json()["items"] if item["object_id"] == legacy_record_id)
    assert review_item["version_status"] == "review_pending"
    assert review_item["review_status"] == "pending_review"
    assert review_item["effective_status"] == "retained_not_effective"


def test_review_queue_record_can_move_to_recycle_bin_and_restore_back_to_review_queue_without_reentering_current_top_view():
    headers = {"X-Role": "admin"}
    order_no = f"DOC-REVIEW-RESTORE-{uuid4().hex[:8]}"

    strict_job_id = _create_job(headers)
    strict_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-REVIEW-RESTORE,{order_no},1,ITEM-REVIEW-RESTORE,产品REVIEW-RESTORE,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    _upload_order_csv(strict_job_id, strict_csv, headers, filename="review-restore-base.csv")

    legacy_job_id = _create_job(headers)
    legacy_csv = (
        "客户,合同号,单据编号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-REVIEW-RESTORE,{order_no},ITEM-REVIEW-RESTORE,产品REVIEW-RESTORE,100,1000,2026-03-18,2026-03-20,2026-03-19,100,0,100\n"
    )
    legacy_file_id = _upload_order_csv(legacy_job_id, legacy_csv, headers, filename="review-restore.csv")
    legacy_record_id = client.get(f"/v1/admin/files/{legacy_file_id}/records", headers=headers).json()["items"][0]["record_id"]

    soft_delete = client.post(
        f"/v1/admin/records/{legacy_record_id}/soft-delete",
        headers=headers,
        json={"reason": "待复核进回收站"},
    )
    assert soft_delete.status_code == 200
    assert soft_delete.json()["lifecycle_state"] == "recycle_bin"

    restore = client.post(
        f"/v1/admin/records/{legacy_record_id}/restore",
        headers=headers,
        json={"reason": "回原待复核区"},
    )
    assert restore.status_code == 200
    assert restore.json()["lifecycle_state"] == "active"
    assert restore.json()["version_status"] == "review_pending"
    assert restore.json()["is_current_effective"] is False

    review_queue = client.get("/v1/admin/review-queue?object_type=record", headers=headers)
    assert review_queue.status_code == 200
    assert legacy_record_id in {item["object_id"] for item in review_queue.json()["items"]}

    current_view = client.get("/v1/upload-jobs/summary?batch_view=all&lifecycle_view=current", headers=headers)
    assert current_view.status_code == 200
    assert legacy_job_id not in {item["job_id"] for item in current_view.json()["items"]}

    with SessionLocal() as db:
        restored_record = db.get(NormalizedRecord, legacy_record_id)
        restored_file = db.get(UploadedFile, legacy_file_id)
        assert restored_record is not None
        assert restored_file is not None
        assert restored_record.deleted_at is None
        assert restored_record.deleted_by is None
        assert restored_record.delete_reason is None
        assert restored_record.delete_origin is None
        assert restored_file.deleted_at is None
        assert restored_file.deleted_by is None
        assert restored_file.delete_reason is None


def test_special_case_record_can_return_to_review_queue_when_originated_from_review_queue():
    headers = {"X-Role": "admin"}
    order_no = f"DOC-SPECIAL-BACK-REVIEW-{uuid4().hex[:8]}"

    strict_job_id = _create_job(headers)
    strict_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-SPECIAL-BACK-REVIEW,{order_no},1,ITEM-SPECIAL-BACK-REVIEW,产品SPECIAL-BACK-REVIEW,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    _upload_order_csv(strict_job_id, strict_csv, headers, filename="special-back-review-base.csv")

    legacy_job_id = _create_job(headers)
    legacy_csv = (
        "客户,合同号,单据编号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-SPECIAL-BACK-REVIEW,{order_no},ITEM-SPECIAL-BACK-REVIEW,产品SPECIAL-BACK-REVIEW,100,1000,2026-03-18,2026-03-20,2026-03-19,100,0,100\n"
    )
    legacy_file_id = _upload_order_csv(legacy_job_id, legacy_csv, headers, filename="special-back-review.csv")
    legacy_record_id = client.get(f"/v1/admin/files/{legacy_file_id}/records", headers=headers).json()["items"][0]["record_id"]

    move = client.post(
        f"/v1/admin/records/{legacy_record_id}/special-case",
        headers=headers,
        json={"special_case_reason": "其他特殊完成", "special_case_note": "先人工收起"},
    )
    assert move.status_code == 200

    special_items = client.get("/v1/admin/special-case?object_type=record", headers=headers)
    assert special_items.status_code == 200
    special_item = next(item for item in special_items.json()["items"] if item["object_id"] == legacy_record_id)
    assert special_item["special_case_source"] == "review_queue"

    back = client.post(
        f"/v1/admin/records/{legacy_record_id}/return-to-review-queue",
        headers=headers,
        json={"reason": "放回待复核区"},
    )
    assert back.status_code == 200
    assert back.json()["lifecycle_state"] == "active"
    assert back.json()["version_status"] == "review_pending"
    assert back.json()["is_current_effective"] is False
    assert back.json()["recompute_task_ids"]

    review_queue = client.get("/v1/admin/review-queue?object_type=record", headers=headers)
    assert review_queue.status_code == 200
    assert legacy_record_id in {item["object_id"] for item in review_queue.json()["items"]}

    current_view = client.get("/v1/upload-jobs/summary?batch_view=all&lifecycle_view=current", headers=headers)
    assert current_view.status_code == 200
    assert legacy_job_id not in {item["job_id"] for item in current_view.json()["items"]}


def test_special_case_state_survives_startup_backfill_after_review_queue_restore_flow():
    headers = {"X-Role": "admin"}
    order_no = f"DOC-SPECIAL-BACKFILL-{uuid4().hex[:8]}"

    strict_job_id = _create_job(headers)
    strict_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-SPECIAL-BACKFILL,{order_no},1,ITEM-SPECIAL-BACKFILL,产品SPECIAL-BACKFILL,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    _upload_order_csv(strict_job_id, strict_csv, headers, filename="special-backfill-base.csv")

    legacy_job_id = _create_job(headers)
    legacy_csv = (
        "客户,合同号,单据编号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-SPECIAL-BACKFILL,{order_no},ITEM-SPECIAL-BACKFILL,产品SPECIAL-BACKFILL,100,1000,2026-03-18,2026-03-20,2026-03-19,100,0,100\n"
    )
    legacy_file_id = _upload_order_csv(legacy_job_id, legacy_csv, headers, filename="special-backfill-review.csv")
    legacy_record_id = client.get(f"/v1/admin/files/{legacy_file_id}/records", headers=headers).json()["items"][0]["record_id"]

    soft_delete = client.post(
        f"/v1/admin/files/{legacy_file_id}/soft-delete",
        headers=headers,
        json={"reason": "先误删整份文件"},
    )
    assert soft_delete.status_code == 200

    restore = client.post(
        f"/v1/admin/files/{legacy_file_id}/restore",
        headers=headers,
        json={"reason": "恢复回待复核"},
    )
    assert restore.status_code == 200

    move = client.post(
        f"/v1/admin/records/{legacy_record_id}/special-case",
        headers=headers,
        json={"special_case_reason": "其他特殊完成", "special_case_note": "回收站后再进特殊情况"},
    )
    assert move.status_code == 200
    assert move.json()["lifecycle_state"] == "special_case"

    with SessionLocal() as db:
        _backfill_lifecycle_defaults(db)
        record = db.get(NormalizedRecord, legacy_record_id)
        uploaded_file = db.get(UploadedFile, legacy_file_id)
        assert record is not None
        assert uploaded_file is not None
        assert record.lifecycle_state == "special_case"
        assert record.version_status == "special_case_retained"
        assert uploaded_file.lifecycle_state == "special_case"


def test_special_case_file_can_return_to_review_queue_when_originated_from_review_queue():
    headers = {"X-Role": "admin"}
    order_no = f"DOC-SPECIAL-FILE-REVIEW-{uuid4().hex[:8]}"

    strict_job_id = _create_job(headers)
    strict_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-SPECIAL-FILE-REVIEW,{order_no},1,ITEM-SPECIAL-FILE-REVIEW,产品SPECIAL-FILE-REVIEW,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    _upload_order_csv(strict_job_id, strict_csv, headers, filename="special-file-review-base.csv")

    legacy_job_id = _create_job(headers)
    legacy_csv = (
        "客户,合同号,单据编号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-SPECIAL-FILE-REVIEW,{order_no},ITEM-SPECIAL-FILE-REVIEW,产品SPECIAL-FILE-REVIEW,100,1000,2026-03-18,2026-03-20,2026-03-19,100,0,100\n"
    )
    legacy_file_id = _upload_order_csv(legacy_job_id, legacy_csv, headers, filename="special-file-review.csv")
    legacy_record_id = client.get(f"/v1/admin/files/{legacy_file_id}/records", headers=headers).json()["items"][0]["record_id"]

    move = client.post(
        f"/v1/admin/records/{legacy_record_id}/special-case",
        headers=headers,
        json={"special_case_reason": "其他特殊完成", "special_case_note": "文件级放回待复核"},
    )
    assert move.status_code == 200

    special_files = client.get("/v1/admin/special-case?object_type=file", headers=headers)
    assert special_files.status_code == 200
    special_file = next(item for item in special_files.json()["items"] if item["object_id"] == legacy_file_id)
    assert special_file["special_case_source"] == "review_queue"

    back = client.post(
        f"/v1/admin/files/{legacy_file_id}/return-to-review-queue",
        headers=headers,
        json={"reason": "整份文件放回待复核区"},
    )
    assert back.status_code == 200
    assert back.json()["lifecycle_state"] == "active"
    assert back.json()["recompute_task_ids"]

    review_queue = client.get("/v1/admin/review-queue?object_type=file", headers=headers)
    assert review_queue.status_code == 200
    assert legacy_file_id in {item["object_id"] for item in review_queue.json()["items"]}

    current_view = client.get("/v1/upload-jobs/summary?batch_view=all&lifecycle_view=current", headers=headers)
    assert current_view.status_code == 200
    assert legacy_job_id not in {item["job_id"] for item in current_view.json()["items"]}


def test_special_case_list_keeps_file_above_record_within_same_job():
    headers = {"X-Role": "admin"}
    order_no = f"DOC-SPECIAL-ORDER-{uuid4().hex[:8]}"

    job_id = _create_job(headers)
    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-SPECIAL-ORDER,{order_no},1,ITEM-SPECIAL-ORDER,产品SPECIAL-ORDER,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers, filename="special-order.csv")
    record_id = client.get(f"/v1/admin/files/{file_id}/records", headers=headers).json()["items"][0]["record_id"]

    move = client.post(
        f"/v1/admin/records/{record_id}/special-case",
        headers=headers,
        json={"special_case_reason": "其他特殊完成", "special_case_note": "排序测试"},
    )
    assert move.status_code == 200

    special_case = client.get("/v1/admin/special-case?object_type=all", headers=headers)
    assert special_case.status_code == 200
    job_items = [item for item in special_case.json()["items"] if item["job_id"] == job_id]
    assert [item["object_type"] for item in job_items] == ["file", "record"]
    assert job_items[0]["object_id"] == file_id
    assert job_items[1]["object_id"] == record_id


def test_special_case_allows_empty_reason_with_optional_note():
    headers = {"X-Role": "admin"}
    order_no = f"DOC-SPECIAL-NO-REASON-{uuid4().hex[:8]}"
    job_id = _create_job(headers)
    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-SPECIAL-NO-REASON,{order_no},1,ITEM-SPECIAL-NO-REASON,产品SPECIAL-NO-REASON,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers, filename="special-no-reason.csv")
    record_id = client.get(f"/v1/admin/files/{file_id}/records", headers=headers).json()["items"][0]["record_id"]

    move = client.post(
        f"/v1/admin/records/{record_id}/special-case",
        headers=headers,
        json={"special_case_note": "仅备注，不选固定原因"},
    )
    assert move.status_code == 200
    assert move.json()["special_case_reason"] == "其他特殊完成"
    assert move.json()["special_case_note"] == "仅备注，不选固定原因"


def test_admin_record_soft_delete_recycle_restore_archive_and_hard_delete():
    headers = {"X-Role": "admin"}
    hard_delete_order_no = "DOC-RB-REC-001"
    hard_delete_job_id = _create_job(headers)
    hard_delete_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-RB,{hard_delete_order_no},1,ITEM-RB,产品RB,100,1000,2026-03-18,2026-03-20,2026-03-19,80,50,30\n"
    )
    file_id = _upload_order_csv(hard_delete_job_id, hard_delete_csv, headers, filename="order-rb.csv")

    records = client.get(f"/v1/admin/files/{file_id}/records", headers=headers)
    assert records.status_code == 200
    record_id = records.json()["items"][0]["record_id"]

    impact = client.post(f"/v1/admin/records/{record_id}/delete-impact", headers=headers, json={})
    assert impact.status_code == 200
    assert "confirm_phrase" not in impact.json()

    active_preview = client.post(f"/v1/admin/records/{record_id}/hard-delete-preview", headers=headers, json={})
    assert active_preview.status_code == 200
    assert active_preview.json()["eligible"] is False
    active_purge = client.post(
        f"/v1/admin/records/{record_id}/hard-delete",
        headers=headers,
        json={},
    )
    assert active_purge.status_code == 409

    soft_delete = client.post(
        f"/v1/admin/records/{record_id}/soft-delete",
        headers=headers,
        json={"reason": "误删演练"},
    )
    assert soft_delete.status_code == 200
    assert soft_delete.json()["lifecycle_state"] == "recycle_bin"

    recycle = client.get("/v1/admin/recycle-bin?object_type=record", headers=headers)
    assert recycle.status_code == 200
    recycle_ids = {item["object_id"] for item in recycle.json()["items"]}
    assert record_id in recycle_ids

    preview = client.post(f"/v1/admin/records/{record_id}/hard-delete-preview", headers=headers, json={})
    assert preview.status_code == 200
    assert preview.json()["eligible"] is True
    assert preview.json()["preview_token"]

    no_confirm = client.post(f"/v1/admin/records/{record_id}/hard-delete", headers=headers, json={})
    assert no_confirm.status_code == 409
    assert no_confirm.json()["detail"] == "Hard delete requires a fresh hard-delete preview."

    confirmed = client.post(
        f"/v1/admin/records/{record_id}/hard-delete",
        headers=headers,
        json={"preview_token": preview.json()["preview_token"]},
    )
    assert confirmed.status_code == 200

    with SessionLocal() as db:
        assert db.get(NormalizedRecord, record_id) is None

    archive_order_no = "DOC-RB-REC-ARCHIVE-001"
    archive_job_id = _create_job(headers)
    archive_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-RB-ARCHIVE,{archive_order_no},1,ITEM-RB-ARCHIVE,产品RB-ARCHIVE,100,1000,2026-03-18,2026-03-20,2026-03-19,80,50,30\n"
    )
    archive_file_id = _upload_order_csv(archive_job_id, archive_csv, headers, filename="order-rb-archive.csv")

    archive_records = client.get(f"/v1/admin/files/{archive_file_id}/records", headers=headers)
    assert archive_records.status_code == 200
    archive_record_id = archive_records.json()["items"][0]["record_id"]

    archive_soft_delete = client.post(
        f"/v1/admin/records/{archive_record_id}/soft-delete",
        headers=headers,
        json={"reason": "归档演练"},
    )
    assert archive_soft_delete.status_code == 200
    assert archive_soft_delete.json()["lifecycle_state"] == "recycle_bin"

    archive = client.post(f"/v1/admin/records/{archive_record_id}/archive", headers=headers, json={"reason": "归档"})
    assert archive.status_code == 200
    assert archive.json()["lifecycle_state"] == "archived"

    archived = client.get("/v1/admin/archived?object_type=record", headers=headers)
    assert archived.status_code == 200
    archived_ids = {item["object_id"] for item in archived.json()["items"]}
    assert archive_record_id in archived_ids

    unarchive = client.post(f"/v1/admin/records/{archive_record_id}/unarchive", headers=headers, json={"reason": "退回"})
    assert unarchive.status_code == 404
    assert unarchive.json()["detail"] == "Not Found"

    archived_again = client.get("/v1/admin/archived?object_type=record", headers=headers)
    assert archived_again.status_code == 200
    archived_again_ids = {item["object_id"] for item in archived_again.json()["items"]}
    assert archive_record_id in archived_again_ids


def test_current_data_soft_delete_allows_direct_recycle_without_confirm_phrase():
    headers = {"X-Role": "admin"}
    job_id = _create_job(headers)
    order_no = f"DOC-CONFIRM-{uuid4().hex[:8]}"
    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-CONFIRM,{order_no},1,ITEM-CONFIRM,产品CONFIRM,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers, filename="confirm-required.csv")

    missing_confirm = client.post(
        f"/v1/admin/files/{file_id}/soft-delete",
        headers=headers,
        json={"reason": "当前数据可直接回收"},
    )
    assert missing_confirm.status_code == 200
    assert missing_confirm.json()["lifecycle_state"] == "recycle_bin"


def test_archived_file_can_move_to_recycle_bin_without_confirm_and_restore_to_archived():
    headers = {"X-Role": "admin"}
    job_id = _create_job(headers)
    order_no = f"DOC-ARCHIVE-RESTORE-{uuid4().hex[:8]}"
    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-ARCHIVE-RESTORE,{order_no},1,ITEM-ARCHIVE-RESTORE,产品ARCHIVE-RESTORE,100,1000,2026-03-18,2026-03-20,2026-03-19,100,100,0\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers, filename="archived-restore.csv")

    archived = client.get("/v1/admin/archived?object_type=file", headers=headers)
    assert archived.status_code == 200
    assert file_id in {item["object_id"] for item in archived.json()["items"]}

    soft_delete = client.post(
        f"/v1/admin/files/{file_id}/soft-delete",
        headers=headers,
        json={"reason": "归档件回收测试"},
    )
    assert soft_delete.status_code == 200
    assert soft_delete.json()["lifecycle_state"] == "recycle_bin"

    recycle_files = client.get("/v1/admin/recycle-bin?object_type=file", headers=headers)
    assert recycle_files.status_code == 200
    assert file_id in {item["object_id"] for item in recycle_files.json()["items"]}

    restore = client.post(
        f"/v1/admin/files/{file_id}/restore",
        headers=headers,
        json={"reason": "回原归档区"},
    )
    assert restore.status_code == 200
    assert restore.json()["lifecycle_state"] == "archived"
    assert restore.json()["recompute_task_ids"] == []

    archived_again = client.get("/v1/admin/archived?object_type=file", headers=headers)
    assert archived_again.status_code == 200
    assert file_id in {item["object_id"] for item in archived_again.json()["items"]}

    current_overview = client.get(f"/v1/admin/jobs/{job_id}/overview?lifecycle_view=current", headers=headers)
    assert current_overview.status_code == 200
    assert current_overview.json()["normalized_record_count"] == 0
    assert current_overview.json()["open_alert_count"] == 0

    with SessionLocal() as db:
        uploaded_file = db.get(UploadedFile, file_id)
        record = (
            db.query(NormalizedRecord)
            .filter(NormalizedRecord.file_id == file_id)
            .order_by(NormalizedRecord.created_at.asc())
            .first()
        )
        assert uploaded_file is not None
        assert record is not None
        assert uploaded_file.lifecycle_state == "archived"
        assert record.lifecycle_state == "archived"
        assert record.is_current_effective is False


def test_special_case_record_can_move_to_recycle_bin_without_confirm_and_restore_to_special_case():
    headers = {"X-Role": "admin"}
    job_id = _create_job(headers)
    order_no = f"DOC-SPECIAL-RESTORE-{uuid4().hex[:8]}"
    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-SPECIAL-RESTORE,{order_no},1,ITEM-SPECIAL-RESTORE,产品SPECIAL-RESTORE,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers, filename="special-restore.csv")
    record_id = client.get(f"/v1/admin/files/{file_id}/records", headers=headers).json()["items"][0]["record_id"]

    move = client.post(
        f"/v1/admin/records/{record_id}/special-case",
        headers=headers,
        json={"special_case_reason": "其他特殊完成", "special_case_note": "特殊情况回收测试"},
    )
    assert move.status_code == 200
    assert move.json()["lifecycle_state"] == "special_case"

    soft_delete = client.post(
        f"/v1/admin/records/{record_id}/soft-delete",
        headers=headers,
        json={"reason": "特殊情况件回收测试"},
    )
    assert soft_delete.status_code == 200
    assert soft_delete.json()["lifecycle_state"] == "recycle_bin"
    assert soft_delete.json()["delete_origin"] == "manual_special_case"

    recycle_records = client.get("/v1/admin/recycle-bin?object_type=record", headers=headers)
    assert recycle_records.status_code == 200
    assert record_id in {item["object_id"] for item in recycle_records.json()["items"]}

    restore = client.post(
        f"/v1/admin/records/{record_id}/restore",
        headers=headers,
        json={"reason": "回原特殊区"},
    )
    assert restore.status_code == 200
    assert restore.json()["lifecycle_state"] == "special_case"
    assert restore.json()["version_status"] == "special_case_retained"
    assert restore.json()["is_current_effective"] is False
    assert restore.json()["recompute_task_ids"] == []

    special_items = client.get("/v1/admin/special-case?object_type=record", headers=headers)
    assert special_items.status_code == 200
    assert record_id in {item["object_id"] for item in special_items.json()["items"]}

    current_overview = client.get(f"/v1/admin/jobs/{job_id}/overview?lifecycle_view=current", headers=headers)
    assert current_overview.status_code == 200
    assert current_overview.json()["normalized_record_count"] == 0
    assert current_overview.json()["open_alert_count"] == 0

    with SessionLocal() as db:
        uploaded_file = db.get(UploadedFile, file_id)
        record = db.get(NormalizedRecord, record_id)
        assert uploaded_file is not None
        assert record is not None
        assert uploaded_file.lifecycle_state == "special_case"
        assert record.lifecycle_state == "special_case"
        assert record.version_status == "special_case_retained"
        assert record.is_current_effective is False


def test_special_case_record_can_return_to_job_list_and_reenter_scan():
    headers = {"X-Role": "admin"}
    job_id = _create_job(headers)
    order_no = f"DOC-SPECIAL-BACK-{uuid4().hex[:8]}"
    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-SPECIAL-BACK,{order_no},1,ITEM-SPECIAL-BACK,产品SPECIAL-BACK,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers, filename="special-back.csv")
    record_id = client.get(f"/v1/admin/files/{file_id}/records", headers=headers).json()["items"][0]["record_id"]

    move = client.post(
        f"/v1/admin/records/{record_id}/special-case",
        headers=headers,
        json={"special_case_reason": "其他特殊完成", "special_case_note": "先人工收起"},
    )
    assert move.status_code == 200
    assert move.json()["lifecycle_state"] == "special_case"

    back = client.post(
        f"/v1/admin/records/{record_id}/return-to-job-list",
        headers=headers,
        json={"reason": "悔步回job列表"},
    )
    assert back.status_code == 200
    assert back.json()["lifecycle_state"] == "active"
    assert back.json()["version_status"] == "current"
    assert back.json()["is_current_effective"] is True
    assert back.json()["recompute_task_ids"]

    current_view = client.get("/v1/upload-jobs/summary?batch_view=all&lifecycle_view=current", headers=headers)
    assert current_view.status_code == 200
    assert job_id in {item["job_id"] for item in current_view.json()["items"]}

    with SessionLocal() as db:
        record = db.get(NormalizedRecord, record_id)
        uploaded_file = db.get(UploadedFile, file_id)
        assert record is not None
        assert uploaded_file is not None
        assert record.lifecycle_state == "active"
        assert record.version_status == "current"
        assert record.is_current_effective is True
        assert uploaded_file.lifecycle_state == "active"


def test_special_case_record_return_to_job_list_keeps_original_time_precedence():
    headers = {"X-Role": "admin"}
    order_no = f"DOC-SPECIAL-TIME-{uuid4().hex[:8]}"

    old_job_id = _create_job(headers)
    old_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-SPECIAL-TIME,{order_no},1,ITEM-SPECIAL-TIME,产品SPECIAL-TIME,100,1000,2026-03-18,2026-03-20,2026-03-19,80,50,30\n"
    )
    old_file_id = _upload_order_csv(old_job_id, old_csv, headers, filename="special-time-old.csv")
    old_record_id = client.get(f"/v1/admin/files/{old_file_id}/records", headers=headers).json()["items"][0]["record_id"]

    move = client.post(
        f"/v1/admin/records/{old_record_id}/special-case",
        headers=headers,
        json={"special_case_reason": "其他特殊完成", "special_case_note": "先人工挪开"},
    )
    assert move.status_code == 200

    new_job_id = _create_job(headers)
    new_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-SPECIAL-TIME,{order_no},1,ITEM-SPECIAL-TIME,产品SPECIAL-TIME,100,1000,2026-03-18,2026-03-20,2026-03-21,90,60,30\n"
    )
    _upload_order_csv(new_job_id, new_csv, headers, filename="special-time-new.csv")

    back = client.post(
        f"/v1/admin/records/{old_record_id}/return-to-job-list",
        headers=headers,
        json={"reason": "按原上传时间重新参与判断"},
    )
    assert back.status_code == 200
    assert back.json()["version_status"] == "restored_history"
    assert back.json()["is_current_effective"] is False

    rows = _order_records(order_no)
    assert len(rows) == 2
    current_rows = [row for row in rows if row.is_current_effective]
    assert len(current_rows) == 1
    restored_row = next(row for row in rows if row.id == old_record_id)
    assert restored_row.lifecycle_state == "active"
    assert restored_row.version_status == "restored_history"
    assert restored_row.is_current_effective is False


def test_special_case_file_can_return_to_job_list():
    headers = {"X-Role": "admin"}
    job_id = _create_job(headers)
    order_no = f"DOC-SPECIAL-FILE-BACK-{uuid4().hex[:8]}"
    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-SPECIAL-FILE-BACK,{order_no},1,ITEM-SPECIAL-FILE-BACK,产品SPECIAL-FILE-BACK,100,1000,2026-03-18,2026-03-20,,0,0,100\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers, filename="special-file-back.csv")
    record_id = client.get(f"/v1/admin/files/{file_id}/records", headers=headers).json()["items"][0]["record_id"]

    move = client.post(
        f"/v1/admin/records/{record_id}/special-case",
        headers=headers,
        json={"special_case_reason": "其他特殊完成", "special_case_note": "文件级悔步测试"},
    )
    assert move.status_code == 200

    special_files = client.get("/v1/admin/special-case?object_type=file", headers=headers)
    assert special_files.status_code == 200
    assert file_id in {item["object_id"] for item in special_files.json()["items"]}

    special_all = client.get("/v1/admin/special-case?object_type=all", headers=headers)
    assert special_all.status_code == 200
    special_items = special_all.json()["items"]
    assert any(item["object_type"] == "file" and item["object_id"] == file_id for item in special_items)
    assert any(item["object_type"] == "record" and item["object_id"] == record_id for item in special_items)

    back = client.post(
        f"/v1/admin/files/{file_id}/return-to-job-list",
        headers=headers,
        json={"reason": "整份文件悔步回job列表"},
    )
    assert back.status_code == 200
    assert back.json()["lifecycle_state"] == "active"
    assert back.json()["recompute_task_ids"]

    current_view = client.get("/v1/upload-jobs/summary?batch_view=all&lifecycle_view=current", headers=headers)
    assert current_view.status_code == 200
    assert job_id in {item["job_id"] for item in current_view.json()["items"]}


def test_completed_current_order_is_auto_archived_after_orchestration():
    headers = {"X-Role": "admin"}
    client.put(
        "/v1/config/operations_monitoring_policy",
        headers=headers,
        json={"value": {"archive_mode": "auto"}},
    )
    order_no = "DOC-AUTO-ARCHIVE-001"
    job_id = _create_job(headers)
    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-AUTO-ARCHIVE,{order_no},1,ITEM-AUTO-ARCHIVE,产品AUTO,100,1000,2026-03-18,2026-03-20,2026-03-19,100,100,0\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers, filename="auto-archive.csv")

    jobs = client.get("/v1/admin/jobs?page=1&size=50", headers=headers)
    assert jobs.status_code == 200
    row = next(item for item in jobs.json()["items"] if item["job_id"] == job_id)
    assert row["archived_file_count"] == 1
    assert row["current_effective_record_count"] == 0
    assert row["open_alert_count"] == 0

    archived = client.get("/v1/admin/archived?object_type=file", headers=headers)
    assert archived.status_code == 200
    archived_file_ids = {item["object_id"] for item in archived.json()["items"]}
    assert file_id in archived_file_ids

    runtime = client.get("/v1/config/operations_runtime_status", headers=headers)
    assert runtime.status_code == 200
    archive_run = runtime.json()["value"]["archive_run"]
    assert archive_run["last_status"] == "succeeded"
    assert archive_run["last_error"] == ""
    assert archive_run["last_archived_file_count"] == 1
    assert archive_run["last_archived_record_count"] == 1
    assert archive_run["last_run_at"]

    operations = client.get("/v1/admin/operations/summary", headers=headers)
    assert operations.status_code == 200
    health_block = operations.json()["health"]
    archive_block = operations.json()["archive"]["archive_run"]
    assert health_block["latest_upload_job_id"] == job_id
    assert health_block["latest_upload_at"]
    assert health_block["latest_task_job_id"] == job_id
    assert health_block["latest_task_status"] == "succeeded"
    assert health_block["latest_task_at"]
    assert archive_block["last_status"] == "succeeded"
    assert archive_block["last_archived_file_count"] == 1
    assert archive_block["last_archived_record_count"] == 1

    with SessionLocal() as db:
        uploaded_file = db.get(UploadedFile, file_id)
        assert uploaded_file is not None
        assert uploaded_file.lifecycle_state == "archived"
        record = (
            db.query(NormalizedRecord)
            .filter(NormalizedRecord.file_id == file_id)
            .order_by(NormalizedRecord.created_at.asc())
            .first()
        )
        assert record is not None
        assert record.lifecycle_state == "archived"
        assert record.is_current_effective is False


def test_manual_archive_mode_requires_preview_then_execute():
    headers = {"X-Role": "admin"}
    client.put(
        "/v1/config/operations_monitoring_policy",
        headers=headers,
        json={
            "value": {
                "archive_mode": "manual",
                "review_queue_warn_threshold": 10,
                "parse_failed_warn_threshold": 1,
                "failed_task_warn_threshold": 1,
                "backup_overdue_hours": 24,
                "archive_run_overdue_hours": 24,
                "db_backup_enabled": True,
                "db_backup_target_path": "/tmp/manual-archive/db",
                "file_backup_enabled": True,
                "file_backup_target_path": "/tmp/manual-archive/uploads",
            }
        },
    )

    job_id = _create_job(headers)
    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        "A客户,HT-MANUAL-MODE,DOC-MANUAL-MODE,1,ITEM-MANUAL-MODE,产品MANUAL-MODE,100,1000,2026-03-18,2026-03-20,2026-03-19,100,100,0\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers, filename="manual-mode-archive.csv")

    jobs = client.get("/v1/admin/jobs?page=1&size=50", headers=headers)
    assert jobs.status_code == 200
    row = next(item for item in jobs.json()["items"] if item["job_id"] == job_id)
    assert row["archived_file_count"] == 0
    assert row["archived_record_count"] == 0
    assert row["current_effective_record_count"] == 1

    execute_without_preview = client.post("/v1/admin/operations/archive/run", headers=headers, json={})
    assert execute_without_preview.status_code == 409
    assert "fresh archive preview" in execute_without_preview.json()["detail"]

    preview = client.post("/v1/admin/operations/archive/preview", headers=headers, json={})
    assert preview.status_code == 200
    preview_payload = preview.json()
    assert preview_payload["candidate_file_count"] == 1
    assert preview_payload["candidate_record_count"] == 1
    assert len(preview_payload["items"]) == 1
    assert preview_payload["preview_token"]

    runtime = client.get("/v1/config/operations_runtime_status", headers=headers)
    assert runtime.status_code == 200
    preview_block = runtime.json()["value"]["archive_preview"]
    assert preview_block["last_status"] == "succeeded"
    assert preview_block["last_candidate_file_count"] == 1
    assert preview_block["last_candidate_record_count"] == 1
    assert preview_block["last_preview_items"]

    execute = client.post(
        "/v1/admin/operations/archive/run",
        headers=headers,
        json={"preview_token": preview_payload["preview_token"]},
    )
    assert execute.status_code == 200
    execute_payload = execute.json()
    assert execute_payload["status"] == "succeeded"
    assert execute_payload["archived_file_count"] == 1
    assert execute_payload["archived_record_count"] == 1

    operations = client.get("/v1/admin/operations/summary", headers=headers)
    assert operations.status_code == 200
    archive_block = operations.json()["archive"]
    assert archive_block["mode"] == "manual"
    assert archive_block["archive_run"]["last_status"] == "succeeded"
    assert archive_block["archive_run"]["last_trigger"] == "manual"

    with SessionLocal() as db:
        uploaded_file = db.get(UploadedFile, file_id)
        assert uploaded_file is not None
        assert uploaded_file.lifecycle_state == "archived"
        record = (
            db.query(NormalizedRecord)
            .filter(NormalizedRecord.file_id == file_id)
            .order_by(NormalizedRecord.created_at.asc())
            .first()
        )
        assert record is not None
        assert record.lifecycle_state == "archived"
        assert record.is_current_effective is False


def test_admin_rejects_incomplete_current_record_and_file_archive_in_current_data():
    headers = {"X-Role": "admin"}
    incomplete_job_id = _create_job(headers)
    incomplete_due = (date.today() + timedelta(days=2)).isoformat()
    incomplete_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-MANUAL-ARCHIVE,DOC-MANUAL-ARCHIVE-INCOMPLETE-001,1,ITEM-MANUAL-ARCHIVE-INCOMPLETE,产品MANUAL-INCOMPLETE,100,1000,2026-03-18,{incomplete_due},,80,50,50\n"
    )
    incomplete_file_id = _upload_order_csv(
        incomplete_job_id,
        incomplete_csv,
        headers,
        filename="manual-archive-incomplete.csv",
    )
    incomplete_record_id = client.get(f"/v1/admin/files/{incomplete_file_id}/records", headers=headers).json()["items"][0]["record_id"]

    incomplete_archive = client.post(
        f"/v1/admin/records/{incomplete_record_id}/archive",
        headers=headers,
        json={"reason": "manual_completed_confirmed"},
    )
    assert incomplete_archive.status_code == 409
    assert "completed current-effective order records" in incomplete_archive.json()["detail"]

    file_archive = client.post(
        f"/v1/admin/files/{incomplete_file_id}/archive",
        headers=headers,
        json={"reason": "manual_completed_confirmed"},
    )
    assert file_archive.status_code == 409
    assert "record-based" in file_archive.json()["detail"]


def test_auto_archive_mixed_file_archives_only_completed_rows():
    headers = {"X-Role": "admin"}
    client.put(
        "/v1/config/operations_monitoring_policy",
        headers=headers,
        json={"value": {"archive_mode": "auto"}},
    )
    job_id = _create_job(headers)
    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        "A客户,HT-MIXED-ARCHIVE,3531-2603300065,1,ITEM-MIXED-A,产品A,8363,1000,2026-04-01,2026-05-30,,0,0,8363\n"
        "A客户,HT-MIXED-ARCHIVE,3541-2504100056,1,ITEM-MIXED-B,产品B,4000,1000,2025-04-10,2025-04-19,2025-05-05,4000,4000,0\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers, filename="mixed-auto-archive.csv")

    jobs = client.get("/v1/admin/jobs?page=1&size=50", headers=headers)
    assert jobs.status_code == 200
    row = next(item for item in jobs.json()["items"] if item["job_id"] == job_id)
    assert row["archived_file_count"] == 0
    assert row["archived_record_count"] == 1
    assert row["current_effective_record_count"] == 1

    file_detail = client.get(f"/v1/admin/files/{file_id}", headers=headers)
    assert file_detail.status_code == 200
    assert file_detail.json()["lifecycle_state"] == "active"
    assert file_detail.json()["is_archived"] is False

    records = client.get(f"/v1/admin/files/{file_id}/records", headers=headers)
    assert records.status_code == 200
    items = {item["customer_order_no"]: item for item in records.json()["items"]}
    assert items["3531-2603300065"]["lifecycle_state"] == "active"
    assert items["3541-2504100056"]["lifecycle_state"] == "archived"

    archived_records = client.get("/v1/admin/archived?object_type=record", headers=headers)
    assert archived_records.status_code == 200
    archived_record_ids = {item["object_id"] for item in archived_records.json()["items"]}
    assert items["3541-2504100056"]["record_id"] in archived_record_ids

    archived_files = client.get("/v1/admin/archived?object_type=file", headers=headers)
    assert archived_files.status_code == 200
    archived_file_ids = {item["object_id"] for item in archived_files.json()["items"]}
    assert file_id not in archived_file_ids


def test_admin_restore_current_conflict_returns_restored_history_without_double_current():
    headers = {"X-Role": "admin"}
    order_no = "DOC-RESTORE-HISTORY-001"
    old_job_id = _create_job(headers)
    old_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-RESTORE,{order_no},1,ITEM-RESTORE,产品RESTORE,100,1000,2026-03-18,2026-03-20,2026-03-19,80,50,30\n"
    )
    old_file_id = _upload_order_csv(old_job_id, old_csv, headers, filename="order-old.csv")
    old_record_id = client.get(f"/v1/admin/files/{old_file_id}/records", headers=headers).json()["items"][0]["record_id"]

    soft_delete = client.post(
        f"/v1/admin/records/{old_record_id}/soft-delete",
        headers=headers,
        json={"reason": "先删后恢复"},
    )
    assert soft_delete.status_code == 200

    new_job_id = _create_job(headers)
    new_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-RESTORE,{order_no},1,ITEM-RESTORE,产品RESTORE,100,1000,2026-03-18,2026-03-20,2026-03-21,90,60,30\n"
    )
    _upload_order_csv(new_job_id, new_csv, headers, filename="order-new.csv")

    restore = client.post(
        f"/v1/admin/records/{old_record_id}/restore",
        headers=headers,
        json={"reason": "恢复测试"},
    )
    assert restore.status_code == 200
    assert restore.json()["version_status"] == "restored_history"
    assert restore.json()["is_current_effective"] is False

    rows = _order_records(order_no)
    assert len(rows) == 2
    current_rows = [row for row in rows if row.is_current_effective]
    assert len(current_rows) == 1
    restored_row = next(row for row in rows if row.id == old_record_id)
    assert restored_row.version_status == "restored_history"
    assert restored_row.lifecycle_state == "active"
    assert restored_row.is_current_effective is False


def test_admin_file_soft_delete_cascades_records_and_can_restore():
    headers = {"X-Role": "admin"}
    order_no = "DOC-FILE-RB-001"
    job_id = _create_job(headers)
    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-FILE-RB,{order_no},1,ITEM-FILE-RB,产品FILE-RB,100,1000,2026-03-18,2026-03-20,2026-03-19,80,50,30\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers, filename="order-file-rb.csv")

    delete_impact = client.post(f"/v1/admin/files/{file_id}/delete-impact", headers=headers, json={})
    assert delete_impact.status_code == 200
    assert "confirm_phrase" not in delete_impact.json()

    soft_delete = client.post(
        f"/v1/admin/files/{file_id}/soft-delete",
        headers=headers,
        json={"reason": "整文件误传"},
    )
    assert soft_delete.status_code == 200
    assert soft_delete.json()["lifecycle_state"] == "recycle_bin"

    overview_after_delete = client.get(
        f"/v1/admin/jobs/{job_id}/overview?lifecycle_view=current",
        headers=headers,
    )
    assert overview_after_delete.status_code == 200
    assert overview_after_delete.json()["normalized_record_count"] == 0

    recycle = client.get("/v1/admin/recycle-bin?object_type=all", headers=headers)
    assert recycle.status_code == 200
    recycle_items = recycle.json()["items"]
    assert any(item["object_type"] == "file" and item["object_id"] == file_id for item in recycle_items)
    assert any(item["object_type"] == "record" and item["delete_origin"] == "manual_file" for item in recycle_items)

    with SessionLocal() as db:
        file_row = db.get(UploadedFile, file_id)
        assert file_row is not None
        assert file_row.lifecycle_state == "recycle_bin"
        record_rows = db.query(NormalizedRecord).filter(NormalizedRecord.file_id == file_id).all()
        assert record_rows
        assert all(row.lifecycle_state == "recycle_bin" for row in record_rows)

    restore = client.post(f"/v1/admin/files/{file_id}/restore", headers=headers, json={"reason": "恢复文件"})
    assert restore.status_code == 200
    assert restore.json()["lifecycle_state"] == "active"

    overview_after_restore = client.get(
        f"/v1/admin/jobs/{job_id}/overview?lifecycle_view=current",
        headers=headers,
    )
    assert overview_after_restore.status_code == 200
    assert overview_after_restore.json()["normalized_record_count"] == 1

    with SessionLocal() as db:
        file_row = db.get(UploadedFile, file_id)
        assert file_row is not None
        assert file_row.lifecycle_state == "active"
        record_rows = db.query(NormalizedRecord).filter(NormalizedRecord.file_id == file_id).all()
        assert all(row.lifecycle_state == "active" for row in record_rows)


def test_restoring_record_from_recycle_bin_also_recovers_file_from_recycle_bin():
    headers = {"X-Role": "admin"}
    job_id = _create_job(headers)
    order_no = f"DOC-RESTORE-RECORD-FILE-{uuid4().hex[:8]}"
    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-RESTORE-RECORD-FILE,{order_no},1,ITEM-RESTORE-RECORD-FILE,产品RESTORE-RECORD-FILE,100,1000,2026-03-18,2026-03-20,2026-03-19,80,50,30\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers, filename="restore-record-file.csv")
    records = client.get(f"/v1/admin/files/{file_id}/records", headers=headers)
    assert records.status_code == 200
    record_id = records.json()["items"][0]["record_id"]

    soft_delete_file = client.post(
        f"/v1/admin/files/{file_id}/soft-delete",
        headers=headers,
        json={"reason": "整文件误传"},
    )
    assert soft_delete_file.status_code == 200
    assert soft_delete_file.json()["lifecycle_state"] == "recycle_bin"

    restore_record = client.post(
        f"/v1/admin/records/{record_id}/restore",
        headers=headers,
        json={"reason": "恢复单条记录"},
    )
    assert restore_record.status_code == 200
    assert restore_record.json()["lifecycle_state"] == "active"

    with SessionLocal() as db:
        file_row = db.get(UploadedFile, file_id)
        record_row = db.get(NormalizedRecord, record_id)
        assert file_row is not None
        assert record_row is not None
        assert file_row.lifecycle_state == "active"
        assert file_row.deleted_at is None
        assert record_row.lifecycle_state == "active"

    current_files = client.get(
        f"/v1/admin/jobs/{job_id}/files?lifecycle_view=current",
        headers=headers,
    )
    assert current_files.status_code == 200
    assert file_id in {item["file_id"] for item in current_files.json()["items"]}

    recycle_files = client.get(
        f"/v1/admin/jobs/{job_id}/files?lifecycle_view=recycle_bin",
        headers=headers,
    )
    assert recycle_files.status_code == 200
    assert file_id not in {item["file_id"] for item in recycle_files.json()["items"]}


def test_admin_file_hard_delete_requires_preview_token():
    headers = {"X-Role": "admin"}
    job_id = _create_job(headers)
    order_no = f"DOC-FILE-HARD-DELETE-{uuid4().hex[:8]}"
    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-FILE-HARD-DELETE,{order_no},1,ITEM-FILE-HARD-DELETE,产品FILE-HARD-DELETE,100,1000,2026-03-18,2026-03-20,2026-03-19,80,50,30\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers, filename="file-hard-delete.csv")

    soft_delete = client.post(
        f"/v1/admin/files/{file_id}/soft-delete",
        headers=headers,
        json={"reason": "文件硬删除预览拦截"},
    )
    assert soft_delete.status_code == 200
    assert soft_delete.json()["lifecycle_state"] == "recycle_bin"

    without_preview = client.post(f"/v1/admin/files/{file_id}/hard-delete", headers=headers, json={})
    assert without_preview.status_code == 409
    assert without_preview.json()["detail"] == "Hard delete requires a fresh hard-delete preview."

    preview = client.post(f"/v1/admin/files/{file_id}/hard-delete-preview", headers=headers, json={})
    assert preview.status_code == 200
    assert preview.json()["eligible"] is True
    assert preview.json()["preview_token"]

    confirmed = client.post(
        f"/v1/admin/files/{file_id}/hard-delete",
        headers=headers,
        json={"preview_token": preview.json()["preview_token"]},
    )
    assert confirmed.status_code == 200

    with SessionLocal() as db:
        assert db.get(UploadedFile, file_id) is None


def test_admin_recycle_bin_does_not_mix_inactive_old_version():
    headers = {"X-Role": "admin"}
    order_no = "DOC-NO-MIX-001"

    job_a = _create_job(headers)
    old_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-NO-MIX,{order_no},1,ITEM-NO-MIX,产品A,100,1000,2026-03-18,2026-03-20,2026-03-19,80,50,30\n"
    )
    _upload_order_csv(job_a, old_csv, headers, filename="order-no-mix-old.csv")

    job_b = _create_job(headers)
    new_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-NO-MIX,{order_no},1,ITEM-NO-MIX,产品A,100,1000,2026-03-18,2026-03-20,2026-03-22,90,55,35\n"
    )
    _upload_order_csv(job_b, new_csv, headers, filename="order-no-mix-new.csv")

    rows = _order_records(order_no)
    inactive_row = next(row for row in rows if row.version_status == "inactive_old_version")

    recycle = client.get("/v1/admin/recycle-bin?object_type=record", headers=headers)
    assert recycle.status_code == 200
    recycle_ids = {item["object_id"] for item in recycle.json()["items"]}
    assert inactive_row.id not in recycle_ids


def test_data_retention_policy_config_exists_and_is_updatable():
    headers = {"X-Role": "admin"}
    current = client.get("/v1/config/data_retention_policy", headers=headers)
    assert current.status_code == 200
    value = current.json()["value"]
    assert value["hard_delete_allowed_from"] == "recycle_bin_only"
    assert "archive_recommended_after_days" not in value
    updated = dict(value)
    updated["recycle_bin_retention_days"] = 45
    updated["archive_recommended_after_days"] = 120

    saved = client.put("/v1/config/data_retention_policy", headers=headers, json={"value": updated})
    assert saved.status_code == 200
    assert saved.json()["value"]["recycle_bin_retention_days"] == 45
    assert "archive_recommended_after_days" not in saved.json()["value"]
