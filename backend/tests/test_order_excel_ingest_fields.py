from __future__ import annotations

import io
from uuid import uuid4

from fastapi.testclient import TestClient

from app.db.init_db import init_db
from app.db.models import ConfigEntry, NormalizedRecord, UploadedFile
from app.db.session import SessionLocal
from app.main import app
from app.services.order_governance import current_effective_only_in_job


client = TestClient(app)


def _create_job(headers: dict[str, str]) -> str:
    resp = client.post("/v1/upload-jobs", headers=headers)
    assert resp.status_code == 200
    return resp.json()["id"]


def _upload_order_csv(job_id: str, csv_content: str, headers: dict[str, str]) -> str:
    resp = client.post(
        f"/v1/upload-jobs/{job_id}/files",
        headers=headers,
        data={"document_type": "order"},
        files={"upload": ("order.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
    )
    assert resp.status_code == 200
    return resp.json()["id"]


def _first_payload(file_id: str) -> dict:
    with SessionLocal() as db:
        row = (
            db.query(NormalizedRecord)
            .filter(NormalizedRecord.file_id == file_id)
            .order_by(NormalizedRecord.source_row.asc())
            .first()
        )
        assert row is not None
        return row.payload_json or {}


def _first_record(file_id: str) -> NormalizedRecord:
    with SessionLocal() as db:
        row = (
            db.query(NormalizedRecord)
            .filter(NormalizedRecord.file_id == file_id)
            .order_by(NormalizedRecord.source_row.asc())
            .first()
        )
        assert row is not None
        db.expunge(row)
        return row


def _file_rows(file_id: str) -> list[NormalizedRecord]:
    with SessionLocal() as db:
        return (
            db.query(NormalizedRecord)
            .filter(NormalizedRecord.file_id == file_id)
            .order_by(NormalizedRecord.source_row.asc())
            .all()
        )


def _payload_by_source_row(file_id: str, source_row: int) -> dict:
    with SessionLocal() as db:
        row = (
            db.query(NormalizedRecord)
            .filter(NormalizedRecord.file_id == file_id, NormalizedRecord.source_row == source_row)
            .first()
        )
        assert row is not None
        return row.payload_json or {}


def _records_by_customer_order_no(customer_order_no: str) -> list[NormalizedRecord]:
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
            if (((row.payload_json or {}).get("core") or {}).get("customer_order_no")) == customer_order_no
        ]


def test_order_excel_with_close_columns_normalizes_and_keeps_raw():
    headers = {"X-Role": "admin"}
    job_id = _create_job(headers)

    csv_content = (
        "客户,合同号,单据编号,料号,品名,数量,金额,订单日期,预计交货日期,关闭状态,行关闭状态,最近出库日期,"
        "行已执行已出库数量,行已开票数量,行未开票数量,行开票状态\n"
        "A客户,HT-001,DOC-001,ITEM-1,产品A,100,1000,2026-03-10,2026-03-20,已关闭,未关闭,"
        "2026-03-15,80,50,30,部分开票\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers)
    payload = _first_payload(file_id)
    core = payload.get("core") or {}
    ext = payload.get("ext") or {}

    assert core.get("customer_order_no") == "DOC-001"
    assert core.get("due_date") == "2026-03-20"
    assert core.get("order_closed") is True
    assert core.get("line_closed") is False
    assert core.get("latest_outbound_date") == "2026-03-15"
    assert core.get("executed_shipped_qty") == 80.0
    assert core.get("invoiced_qty") == 50.0
    assert core.get("uninvoiced_qty") == 30.0
    assert core.get("line_invoice_status") == "partial"

    assert ext.get("order_closed_raw") == "已关闭"
    assert ext.get("line_closed_raw") == "未关闭"
    assert ext.get("line_invoice_status_raw") == "部分开票"
    assert ext.get("due_date_raw") == "2026-03-20"
    assert ext.get("document_no_raw") == "DOC-001"
    assert ext.get("order_closed_column_present") is True
    assert ext.get("line_closed_column_present") is True


def test_order_excel_outbound_status_columns_normalize_and_keep_raw():
    headers = {"X-Role": "admin"}
    job_id = _create_job(headers)
    suffix = uuid4().hex[:8]
    order_no_1 = f"DOC-OUT-1-{suffix}"
    order_no_2 = f"DOC-OUT-2-{suffix}"

    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,出库状态,行出库状态,最近出库日期,"
        "行已执行已出库数量,行已开票数量,行未开票数量,行开票状态\n"
        f"A客户,HT-OUT-1,{order_no_1},1,ITEM-OUT-1,产品出库1,100,1000,2026-03-10,2026-03-20,全部出库,全部出库,"
        "2026-03-15,100,50,50,部分开票\n"
        f"A客户,HT-OUT-2,{order_no_2},2,ITEM-OUT-2,产品出库2,80,800,2026-03-11,2026-03-21,部分出库,未出库,"
        "2026-03-16,20,0,20,未开票\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers)

    payload1 = _payload_by_source_row(file_id, 1)
    core1 = payload1.get("core") or {}
    ext1 = payload1.get("ext") or {}
    assert core1.get("order_outbound_status") == "fully_outbound"
    assert core1.get("line_outbound_status") == "fully_outbound"
    assert ext1.get("order_outbound_status_raw") == "全部出库"
    assert ext1.get("line_outbound_status_raw") == "全部出库"
    assert ext1.get("order_outbound_status_column_present") is True
    assert ext1.get("line_outbound_status_column_present") is True

    payload2 = _payload_by_source_row(file_id, 2)
    core2 = payload2.get("core") or {}
    ext2 = payload2.get("ext") or {}
    assert core2.get("order_outbound_status") == "partially_outbound"
    assert core2.get("line_outbound_status") == "not_outbound"
    assert ext2.get("order_outbound_status_raw") == "部分出库"
    assert ext2.get("line_outbound_status_raw") == "未出库"


def test_order_excel_entry_line_no_normalizes_and_keeps_raw():
    headers = {"X-Role": "admin"}
    job_id = _create_job(headers)

    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期\n"
        "A客户,HT-001,DOC-001,1.0,ITEM-1,产品A,100,1000,2026-03-10,2026-03-20\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers)
    payload = _first_payload(file_id)
    core = payload.get("core") or {}
    ext = payload.get("ext") or {}

    assert core.get("entry_line_no") == "1"
    assert ext.get("entry_line_no_raw") == 1.0


def test_order_excel_amount_aliases_capture_amount():
    headers = {"X-Role": "admin"}
    job_id = _create_job(headers)

    csv_content = (
        "客户,单据编号,分录行号,商品名称,商品编码,数量,单据日期,预计交货日期,价税合计,成交金额,含税单价\n"
        "安吉热威,DOC-AMOUNT-001,1,压板,0101-HIT-8.5S3338-1,150,2025-04-21,2025-04-21,103.5,9999,0.69\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers)
    payload = _first_payload(file_id)
    core = payload.get("core") or {}

    assert core.get("amount") == 103.5
    assert core.get("order_total_amount") == 9999
    assert core.get("tax_inclusive_unit_price") == 0.69


def test_init_db_backfills_order_amount_aliases_for_existing_field_mappings():
    with SessionLocal() as db:
        item = db.get(ConfigEntry, "field_mappings")
        assert item is not None
        current = dict(item.value_json or {})
        order_map = dict(current.get("order") or {})
        order_map["amount"] = ["成交金额", "金额", "amount"]
        order_map.pop("order_total_amount", None)
        current["order"] = order_map
        item.value_json = current
        db.commit()

    init_db()

    with SessionLocal() as db:
        item = db.get(ConfigEntry, "field_mappings")
        assert item is not None
        order_map = dict((item.value_json or {}).get("order") or {})
        aliases = order_map.get("amount") or []
        assert aliases[:3] == ["价税合计", "金额", "amount"]
        assert "价税合计" in aliases
        assert "成交金额" not in aliases
        order_total_aliases = order_map.get("order_total_amount") or []
        assert order_total_aliases[0] == "成交金额"
        assert "成交金额" in order_total_aliases
        unit_price_aliases = order_map.get("tax_inclusive_unit_price") or []
        assert "含税单价" in unit_price_aliases


def test_init_db_backfills_order_outbound_status_aliases_for_existing_field_mappings():
    with SessionLocal() as db:
        item = db.get(ConfigEntry, "field_mappings")
        assert item is not None
        current = dict(item.value_json or {})
        order_map = dict(current.get("order") or {})
        order_map.pop("order_outbound_status", None)
        order_map.pop("line_outbound_status", None)
        current["order"] = order_map
        item.value_json = current
        db.commit()

    init_db()

    with SessionLocal() as db:
        item = db.get(ConfigEntry, "field_mappings")
        assert item is not None
        order_map = dict((item.value_json or {}).get("order") or {})
        assert "出库状态" in (order_map.get("order_outbound_status") or [])
        assert "行出库状态" in (order_map.get("line_outbound_status") or [])


def test_init_db_backfills_ship_after_no_finance_order_outbound_switch_default():
    with SessionLocal() as db:
        item = db.get(ConfigEntry, "rule_parameters")
        assert item is not None
        current = dict(item.value_json or {})
        current.pop("ship_after_no_finance_require_order_fully_outbound", None)
        item.value_json = current
        db.commit()

    init_db()

    with SessionLocal() as db:
        item = db.get(ConfigEntry, "rule_parameters")
        assert item is not None
        value = dict(item.value_json or {})
        assert value["ship_after_no_finance_require_order_fully_outbound"] is False
        assert value["ship_after_no_finance_days"] == 60


def test_order_excel_duplicate_import_auto_deletes_new_strict_duplicate_and_keeps_single_current():
    headers = {"X-Role": "admin"}
    order_no = "DOC-GOV-DUP-001"

    job_a = _create_job(headers)
    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-GOV-DUP,{order_no},1,ITEM-GOV-DUP,产品DUP,100,1000,2026-03-10,2026-03-20,2026-03-18,80,50,30\n"
    )
    _upload_order_csv(job_a, csv_content, headers)

    job_b = _create_job(headers)
    _upload_order_csv(job_b, csv_content, headers)

    rows = _records_by_customer_order_no(order_no)
    assert len(rows) == 1

    current_governance = rows[0].payload_json["governance"]
    assert current_governance["version_status"] == "current"
    assert current_governance["is_current_effective"] is True

    with SessionLocal() as db:
        duplicate_file = db.get(UploadedFile, db.query(UploadedFile.id).filter(UploadedFile.job_id == job_b).scalar())
        assert duplicate_file is not None
        assert duplicate_file.parsed_count == 0


def test_order_excel_update_marks_old_version_inactive_and_new_current():
    headers = {"X-Role": "admin"}
    order_no = "DOC-GOV-UPD-001"

    job_a = _create_job(headers)
    old_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-GOV-UPD,{order_no},1,ITEM-GOV-UPD,产品UPD,100,1000,2026-03-10,2026-03-20,2026-03-15,70,40,30\n"
    )
    _upload_order_csv(job_a, old_csv, headers)

    job_b = _create_job(headers)
    new_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-GOV-UPD,{order_no},1,ITEM-GOV-UPD,产品UPD,100,1000,2026-03-10,2026-03-20,2026-03-18,80,40,20\n"
    )
    _upload_order_csv(job_b, new_csv, headers)

    rows = _records_by_customer_order_no(order_no)
    assert len(rows) == 2

    current_row = next(row for row in rows if (row.payload_json or {}).get("governance", {}).get("is_current_effective") is True)
    old_row = next(row for row in rows if row.id != current_row.id)
    current_governance = current_row.payload_json["governance"]
    old_governance = old_row.payload_json["governance"]

    assert current_governance["change_type"] == "update"
    assert current_governance["version_status"] == "current"
    assert current_governance["supersedes_record_id"] == old_row.id
    assert old_governance["version_status"] == "inactive_old_version"
    assert old_governance["is_current_effective"] is False
    assert old_governance["superseded_by_record_id"] == current_row.id


def test_order_excel_blank_and_zero_business_qty_are_treated_as_same_for_duplicate():
    headers = {"X-Role": "admin"}
    order_no = f"DOC-GOV-BLANK-ZERO-{uuid4().hex[:8]}"

    job_a = _create_job(headers)
    blank_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-GOV-BLANK-ZERO,{order_no},1,ITEM-GOV-BLANK-ZERO,产品BLANK-ZERO,100,1000,2026-03-10,2026-03-20,2026-03-18,,,\n"
    )
    _upload_order_csv(job_a, blank_csv, headers)

    job_b = _create_job(headers)
    zero_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-GOV-BLANK-ZERO,{order_no},1,ITEM-GOV-BLANK-ZERO,产品BLANK-ZERO,100,1000,2026-03-10,2026-03-20,2026-03-18,0,0,0\n"
    )
    _upload_order_csv(job_b, zero_csv, headers)

    rows = _records_by_customer_order_no(order_no)
    assert len(rows) == 1
    assert rows[0].payload_json["governance"]["version_status"] == "current"


def test_order_excel_missing_any_of_review_required_columns_enters_review_queue():
    headers = {"X-Role": "admin"}
    order_no = f"DOC-GOV-DRIFT-COL-{uuid4().hex[:8]}"
    job_id = _create_job(headers)

    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量\n"
        f"A客户,HT-GOV-DRIFT-COL,{order_no},1,ITEM-GOV-DRIFT-COL,产品DRIFT-COL,100,1000,2026-03-10,2026-03-20,2026-03-18,80,50\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers)
    record = _first_record(file_id)
    governance = (record.payload_json or {}).get("governance") or {}

    assert governance["version_status"] == "review_pending"
    assert governance["is_current_effective"] is False
    assert governance["governance_reason"] == "pending_review_missing_required_columns:uninvoiced_qty"


def test_order_excel_blank_identity_value_enters_review_queue():
    headers = {"X-Role": "admin"}
    order_no = f"DOC-GOV-DRIFT-ID-{uuid4().hex[:8]}"
    job_id = _create_job(headers)

    csv_content = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-GOV-DRIFT-ID,{order_no},1,ITEM-GOV-DRIFT-ID,,100,1000,2026-03-10,2026-03-20,2026-03-18,80,50,30\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers)
    record = _first_record(file_id)
    governance = (record.payload_json or {}).get("governance") or {}

    assert governance["version_status"] == "review_pending"
    assert governance["is_current_effective"] is False
    assert governance["governance_reason"] == "pending_review_missing_identity_values:item_name"


def test_order_excel_legacy_fallback_without_entry_line_no_enters_review_queue_without_affecting_current():
    headers = {"X-Role": "admin"}
    order_no = "DOC-GOV-LEGACY-001"

    job_a = _create_job(headers)
    csv_content = (
        "客户,合同号,单据编号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-GOV-LEGACY,{order_no},ITEM-GOV-LEGACY,产品LEGACY,100,1000,2026-03-10,2026-03-20,2026-03-18,80,50,30\n"
    )
    _upload_order_csv(job_a, csv_content, headers)

    job_b = _create_job(headers)
    _upload_order_csv(job_b, csv_content, headers)

    rows = _records_by_customer_order_no(order_no)
    assert len(rows) == 2
    assert sum(1 for row in rows if (row.payload_json or {}).get("governance", {}).get("is_current_effective") is True) == 0
    review_rows = [
        row for row in rows if (row.payload_json or {}).get("governance", {}).get("version_status") == "review_pending"
    ]
    assert len(review_rows) == 2
    first_review = review_rows[0].payload_json["governance"]
    second_review = review_rows[1].payload_json["governance"]
    assert first_review["identity_mode"] == "legacy_fallback"
    assert first_review["change_type"] == "new"
    assert first_review["governance_reason"] == "pending_review_missing_required_columns:entry_line_no"
    assert second_review["identity_mode"] == "legacy_fallback"
    assert second_review["change_type"] == "new"
    assert second_review["governance_reason"] == "pending_review_missing_required_columns:entry_line_no"
    assert second_review["is_current_effective"] is False


def test_order_excel_update_still_works_after_middle_duplicate_was_auto_deleted():
    headers = {"X-Role": "admin"}
    order_no = f"DOC-GOV-DUP-UPD-{uuid4().hex[:8]}"

    job_a = _create_job(headers)
    base_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-GOV-DUP-UPD,{order_no},1,ITEM-GOV-DUP-UPD,产品DUP-UPD,100,1000,2026-03-10,2026-03-20,2026-03-18,80,50,30\n"
    )
    _upload_order_csv(job_a, base_csv, headers)

    job_b = _create_job(headers)
    _upload_order_csv(job_b, base_csv, headers)

    job_c = _create_job(headers)
    updated_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-GOV-DUP-UPD,{order_no},1,ITEM-GOV-DUP-UPD,产品DUP-UPD,100,1000,2026-03-10,2026-03-20,2026-03-19,100,50,0\n"
    )
    _upload_order_csv(job_c, updated_csv, headers)

    rows = _records_by_customer_order_no(order_no)
    assert len(rows) == 2

    current_row = next(row for row in rows if (row.payload_json or {}).get("governance", {}).get("is_current_effective") is True)
    old_row = next(row for row in rows if row.id != current_row.id)

    assert current_row.payload_json["governance"]["change_type"] == "update"
    assert current_row.payload_json["governance"]["version_status"] == "current"
    assert old_row.payload_json["governance"]["version_status"] == "inactive_old_version"

    with SessionLocal() as db:
        duplicate_file = db.get(UploadedFile, db.query(UploadedFile.id).filter(UploadedFile.job_id == job_b).scalar())
        assert duplicate_file is not None
        assert duplicate_file.parsed_count == 0


def test_strict_duplicate_auto_delete_keeps_file_audit_count():
    headers = {"X-Role": "admin"}
    order_no = f"DOC-GOV-DUP-COUNT-{uuid4().hex[:8]}"

    job_a = _create_job(headers)
    base_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-GOV-DUP-COUNT,{order_no},1,ITEM-GOV-DUP-COUNT,产品DUP-COUNT,100,1000,2026-03-10,2026-03-20,2026-03-18,80,50,30\n"
    )
    _upload_order_csv(job_a, base_csv, headers)

    job_b = _create_job(headers)
    duplicate_file_id = _upload_order_csv(job_b, base_csv, headers)

    with SessionLocal() as db:
        duplicate_file = db.get(UploadedFile, duplicate_file_id)
        assert duplicate_file is not None
        assert duplicate_file.parsed_count == 0
        assert duplicate_file.auto_deleted_duplicate_count == 1


def test_order_excel_new_line_no_can_bridge_to_legacy_record_and_freezes_for_review():
    headers = {"X-Role": "admin"}
    order_no = "DOC-GOV-BRIDGE-001"

    job_a = _create_job(headers)
    old_csv = (
        "客户,合同号,单据编号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-GOV-BRIDGE,{order_no},ITEM-GOV-BRIDGE,产品BRIDGE,100,1000,2026-03-10,2026-03-20,2026-03-15,70,40,30\n"
    )
    _upload_order_csv(job_a, old_csv, headers)

    job_b = _create_job(headers)
    new_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-GOV-BRIDGE,{order_no},1,ITEM-GOV-BRIDGE,产品BRIDGE,100,1000,2026-03-10,2026-03-20,2026-03-18,80,40,20\n"
    )
    _upload_order_csv(job_b, new_csv, headers)

    rows = _records_by_customer_order_no(order_no)
    assert len(rows) == 2
    current_row = next(row for row in rows if (row.payload_json or {}).get("governance", {}).get("is_current_effective") is True)
    review_row = next(
        row for row in rows if (row.payload_json or {}).get("governance", {}).get("version_status") == "review_pending"
    )
    assert current_row.id != review_row.id
    assert current_row.payload_json["governance"]["identity_mode"] == "strict_line_no"
    assert current_row.payload_json["governance"]["change_type"] == "new"
    assert current_row.payload_json["governance"]["governance_reason"] == "strict_identity_no_match"
    assert review_row.payload_json["governance"]["identity_mode"] == "legacy_fallback"
    assert review_row.payload_json["governance"]["change_type"] == "new"
    assert review_row.payload_json["governance"]["governance_reason"] == "pending_review_missing_required_columns:entry_line_no"
    assert review_row.payload_json["governance"]["is_current_effective"] is False


def test_order_excel_same_main_identity_with_aux_mismatch_stays_new():
    headers = {"X-Role": "admin"}
    order_no = "DOC-GOV-AUX-001"

    job_a = _create_job(headers)
    csv_a = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-GOV-AUX,{order_no},1,ITEM-GOV-A,产品A,100,1000,2026-03-10,2026-03-20,2026-03-18,80,50,30\n"
    )
    _upload_order_csv(job_a, csv_a, headers)

    job_b = _create_job(headers)
    csv_b = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-GOV-AUX,{order_no},1,ITEM-GOV-B,产品B,100,1000,2026-03-10,2026-03-20,2026-03-18,80,50,0\n"
    )
    _upload_order_csv(job_b, csv_b, headers)

    rows = _records_by_customer_order_no(order_no)
    assert len(rows) == 2
    assert sum(1 for row in rows if (row.payload_json or {}).get("governance", {}).get("is_current_effective") is True) == 2
    newest = rows[-1]
    assert newest.payload_json["governance"]["change_type"] == "new"
    assert newest.payload_json["governance"]["governance_reason"] == "main_hit_aux_mismatch"


def test_current_effective_only_in_job_excludes_inactive_old_versions():
    headers = {"X-Role": "admin"}
    order_no = "DOC-GOV-JOB-001"
    job_id = _create_job(headers)

    old_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-GOV-JOB,{order_no},1,ITEM-GOV-JOB,产品JOB,100,1000,2026-03-10,2026-03-20,2026-03-15,70,40,30\n"
    )
    _upload_order_csv(job_id, old_csv, headers)

    new_csv = (
        "客户,合同号,单据编号,分录行号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        f"A客户,HT-GOV-JOB,{order_no},1,ITEM-GOV-JOB,产品JOB,100,1000,2026-03-10,2026-03-20,2026-03-18,80,40,20\n"
    )
    _upload_order_csv(job_id, new_csv, headers)

    with SessionLocal() as db:
        rows = current_effective_only_in_job(db, job_id=job_id)
        order_rows = [
            row
            for row in rows
            if row.document_type == "order"
            and (((row.payload_json or {}).get("core") or {}).get("customer_order_no")) == order_no
        ]

    assert len(order_rows) == 1
    assert order_rows[0].payload_json["governance"]["version_status"] == "current"


def test_order_excel_missing_close_columns_marks_null_and_unknown():
    headers = {"X-Role": "admin"}
    job_id = _create_job(headers)

    csv_content = (
        "客户,合同号,单据编号,料号,品名,数量,金额,订单日期,预计交货日期\n"
        "A客户,HT-002,DOC-002,ITEM-2,产品B,200,3000,2026-03-11,2026-03-21\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers)
    payload = _first_payload(file_id)
    core = payload.get("core") or {}
    ext = payload.get("ext") or {}

    assert core.get("customer_order_no") == "DOC-002"
    assert core.get("due_date") == "2026-03-21"
    assert core.get("order_closed") is None
    assert core.get("line_closed") is None
    assert core.get("order_outbound_status") is None
    assert core.get("line_outbound_status") is None
    assert ext.get("order_closed_column_present") is False
    assert ext.get("line_closed_column_present") is False
    assert ext.get("order_outbound_status_column_present") is False
    assert ext.get("line_outbound_status_column_present") is False


def test_order_excel_with_garbage_columns_is_ignored_safely():
    headers = {"X-Role": "admin"}
    job_id = _create_job(headers)

    csv_content = (
        "垃圾列A,客户,合同号,单据编号,料号,品名,数量,金额,订单日期,预计交货日期,关闭状态,行关闭状态,垃圾列B\n"
        "X,A客户,HT-003,DOC-003,ITEM-3,产品C,120,2600,2026-03-12,2026-03-22,未关闭,未关闭,Y\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers)
    payload = _first_payload(file_id)
    core = payload.get("core") or {}
    ext = payload.get("ext") or {}

    assert core.get("customer_order_no") == "DOC-003"
    assert core.get("due_date") == "2026-03-22"
    assert core.get("order_closed") is False
    assert core.get("line_closed") is False
    assert "垃圾列A" not in core
    assert "垃圾列A" not in ext
    assert "垃圾列B" not in core
    assert "垃圾列B" not in ext


def test_order_excel_half_empty_row_is_filtered_out():
    headers = {"X-Role": "admin"}
    job_id = _create_job(headers)

    csv_content = (
        "客户,合同号,单据编号,料号,品名,数量,金额,预计交货日期,关闭状态,行关闭状态,最近出库日期,"
        "行已执行已出库数量,行已开票数量,行未开票数量,行开票状态\n"
        "A客户,HT-004,DOC-004,ITEM-4,产品D,120,3600,2026-03-25,未关闭,未关闭,2026-03-18,60,30,30,部分开票\n"
        ",,,,,120,,,,,2026-03-18,120,120,,全部开票\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers)

    rows = _file_rows(file_id)
    assert len(rows) == 1
    assert rows[0].source_row == 1

    with SessionLocal() as db:
        f = db.get(UploadedFile, file_id)
        assert f is not None
        assert f.parsed_count == 1


def test_order_excel_line_invoice_status_full_and_uninvoiced_mapping():
    headers = {"X-Role": "admin"}
    job_id = _create_job(headers)

    csv_content = (
        "客户,合同号,单据编号,料号,品名,数量,金额,订单日期,预计交货日期,关闭状态,行关闭状态,行开票状态\n"
        "A客户,HT-005,DOC-005,ITEM-5,产品E,100,1000,2026-03-13,2026-03-23,未关闭,未关闭,全部开票\n"
        "B客户,HT-006,DOC-006,ITEM-6,产品F,80,800,2026-03-14,2026-03-24,未关闭,未关闭,未开票\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers)

    payload1 = _payload_by_source_row(file_id, 1)
    core1 = payload1.get("core") or {}
    ext1 = payload1.get("ext") or {}
    assert ext1.get("line_invoice_status_raw") == "全部开票"
    assert core1.get("line_invoice_status") == "invoiced"

    payload2 = _payload_by_source_row(file_id, 2)
    core2 = payload2.get("core") or {}
    ext2 = payload2.get("ext") or {}
    assert ext2.get("line_invoice_status_raw") == "未开票"
    assert core2.get("line_invoice_status") == "uninvoiced"


def test_order_excel_completed_skip_scan_state_is_written():
    headers = {"X-Role": "admin"}
    job_id = _create_job(headers)

    csv_content = (
        "客户,合同号,单据编号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量,行开票状态\n"
        "A客户,HT-007,DOC-007,ITEM-7,产品G,100,1200,2026-03-15,2026-03-25,2026-03-18,100,100,0,全部开票\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers)
    payload = _first_payload(file_id)
    core = payload.get("core") or {}
    record = _first_record(file_id)

    assert core.get("scan_state") == "completed_skip_scan"
    assert record.order_unshipped_qty == 0.0


def test_order_excel_blank_business_qty_fields_normalize_to_zero():
    headers = {"X-Role": "admin"}
    job_id = _create_job(headers)

    csv_content = (
        "客户,合同号,单据编号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量,行开票状态\n"
        "A客户,HT-007B,DOC-007B,ITEM-7B,产品G-空数量,100,1200,2026-03-15,2026-03-25,2026-03-18,,, ,全部开票\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers)
    payload = _first_payload(file_id)
    core = payload.get("core") or {}
    record = _first_record(file_id)

    assert core.get("executed_shipped_qty") == 0.0
    assert core.get("invoiced_qty") == 0.0
    assert core.get("uninvoiced_qty") == 0.0
    assert core.get("order_unshipped_qty") == 100.0
    assert record.order_unshipped_qty == 100.0
    assert core.get("scan_state") is None


def test_order_excel_order_unshipped_qty_uses_quantity_minus_executed_shipped_qty():
    headers = {"X-Role": "admin"}
    job_id = _create_job(headers)

    csv_content = (
        "客户,合同号,单据编号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        "A客户,HT-UNSHIPPED,DOC-UNSHIPPED,ITEM-UNSHIPPED,产品未发货,3000,1200,2026-03-15,2026-03-25,2026-03-18,700,0,3000\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers)
    payload = _first_payload(file_id)
    core = payload.get("core") or {}
    record = _first_record(file_id)

    assert core.get("order_unshipped_qty") == 2300.0
    assert record.order_unshipped_qty == 2300.0


def test_order_excel_order_unshipped_qty_is_clamped_to_zero_when_source_is_abnormal():
    headers = {"X-Role": "admin"}
    job_id = _create_job(headers)

    csv_content = (
        "客户,合同号,单据编号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量\n"
        "A客户,HT-UNSHIPPED-ERR,DOC-UNSHIPPED-ERR,ITEM-UNSHIPPED-ERR,产品异常,100,1200,2026-03-15,2026-03-25,2026-03-18,120,0,100\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers)
    payload = _first_payload(file_id)
    core = payload.get("core") or {}
    record = _first_record(file_id)

    assert core.get("order_unshipped_qty") == 0.0
    assert record.order_unshipped_qty == 0.0


def test_order_excel_dirty_rows_do_not_enter_completed_skip_scan():
    headers = {"X-Role": "admin"}
    job_id = _create_job(headers)

    csv_content = (
        "客户,合同号,单据编号,料号,品名,数量,金额,订单日期,预计交货日期,最近出库日期,行已执行已出库数量,行已开票数量,行未开票数量,行开票状态\n"
        "A客户,HT-008,DOC-008,,,100,1200,2026-03-15,2026-03-25,2026-03-18,100,100,0,全部开票\n"
        "B客户,HT-009,DOC-009,ITEM-9,产品I,100,1300,2026-03-16,2026-03-26,2026-03-19,120,120,-20,全部开票\n"
    )
    file_id = _upload_order_csv(job_id, csv_content, headers)

    payload1 = _payload_by_source_row(file_id, 1)
    payload2 = _payload_by_source_row(file_id, 2)

    assert (payload1.get("core") or {}).get("scan_state") is None
    assert (payload2.get("core") or {}).get("scan_state") is None
