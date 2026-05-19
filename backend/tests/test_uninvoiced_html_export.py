from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Alert, MatchGroup, NormalizedRecord, UploadJob, UploadedFile, ViewerCustomerAlertSetting
from app.services.export_service import export_uninvoiced_html


def _session():
    engine = create_engine(
        "sqlite+pysqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)()


def _core(
    *,
    customer: str,
    order_no: str,
    line_no: str,
    item_code: str,
    item_name: str,
    quantity: float,
    amount: float | None,
    order_outbound_status: str,
    line_outbound_status: str,
    latest_outbound_date: str | None,
    executed_shipped_qty: float,
    invoiced_qty: float,
    uninvoiced_qty: float,
) -> dict:
    return {
        "customer": customer,
        "customer_order_no": order_no,
        "entry_line_no": line_no,
        "item_code": item_code,
        "item_name": item_name,
        "quantity": quantity,
        "amount": amount,
        "order_outbound_status": order_outbound_status,
        "line_outbound_status": line_outbound_status,
        "latest_outbound_date": latest_outbound_date,
        "executed_shipped_qty": executed_shipped_qty,
        "invoiced_qty": invoiced_qty,
        "uninvoiced_qty": uninvoiced_qty,
        "order_unshipped_qty": max(quantity - executed_shipped_qty, 0),
    }


def _add_order_record(db, *, record_id: str, job_id: str, file_id: str, source_row: int, core: dict):
    db.add(
        NormalizedRecord(
            id=record_id,
            job_id=job_id,
            file_id=file_id,
            document_type="order",
            source_row=source_row,
            lifecycle_state="active",
            is_current_effective=True,
            payload_json={
                "core": core,
                "ext": {
                    "order_outbound_status_raw": "全部出库"
                    if core["order_outbound_status"] == "fully_outbound"
                    else "部分出库",
                    "line_outbound_status_raw": "全部出库"
                    if core["line_outbound_status"] == "fully_outbound"
                    else "部分出库",
                },
            },
        )
    )


def _add_alert(db, *, alert_id: str, job_id: str, group_id: str, record_id: str, core: dict, days: int):
    db.add(
        Alert(
            id=alert_id,
            job_id=job_id,
            group_id=group_id,
            alert_type="ship_after_no_finance",
            status="open",
            severity="medium",
            message=f"订单〔{core['customer_order_no']}〕距最近出库已〔{days}〕天，金额〔{core['amount']}〕，请尽快开票。",
            payload_json={
                "record_id": record_id,
                "source_row": 1,
                "customer_order_no": core["customer_order_no"],
                "entry_line_no": core["entry_line_no"],
                "item_code": core["item_code"],
                "item_name": core["item_name"],
                "quantity": core["quantity"],
                "amount": core["amount"],
                "order_outbound_status": core["order_outbound_status"],
                "line_outbound_status": core["line_outbound_status"],
                "latest_outbound_date": core["latest_outbound_date"],
                "days_after_outbound": days,
                "executed_shipped_qty": core["executed_shipped_qty"],
                "invoiced_qty": core["invoiced_qty"],
                "uninvoiced_qty": core["uninvoiced_qty"],
                "order_unshipped_qty": core["order_unshipped_qty"],
            },
        )
    )


def test_uninvoiced_html_groups_customer_order_and_product_context():
    db = _session()
    job = UploadJob(id="job-html", status="succeeded", created_by="test")
    file = UploadedFile(
        id="file-html",
        job_id=job.id,
        document_type="order",
        filename="order.csv",
        storage_path="/tmp/order.csv",
    )
    group = MatchGroup(
        id="group-html",
        job_id=job.id,
        group_key="group-html",
        summary_json={"aggregate": {"customer": "A客户"}},
    )
    db.add_all([job, file, group])

    should_core = _core(
        customer="A客户",
        order_no="SO-HTML-1",
        line_no="1",
        item_code="ITEM-A",
        item_name="产品A",
        quantity=100,
        amount=52000,
        order_outbound_status="partially_outbound",
        line_outbound_status="fully_outbound",
        latest_outbound_date="2026-03-01",
        executed_shipped_qty=100,
        invoiced_qty=20,
        uninvoiced_qty=80,
    )
    should_uninvoiced_core = _core(
        customer="A客户",
        order_no="SO-HTML-1",
        line_no="4",
        item_code="ITEM-D",
        item_name="产品D",
        quantity=5,
        amount=500,
        order_outbound_status="partially_outbound",
        line_outbound_status="fully_outbound",
        latest_outbound_date="2026-03-01",
        executed_shipped_qty=5,
        invoiced_qty=0,
        uninvoiced_qty=5,
    )
    done_core = _core(
        customer="A客户",
        order_no="SO-HTML-1",
        line_no="2",
        item_code="ITEM-B",
        item_name="产品B",
        quantity=10,
        amount=1000,
        order_outbound_status="partially_outbound",
        line_outbound_status="fully_outbound",
        latest_outbound_date="2026-03-01",
        executed_shipped_qty=10,
        invoiced_qty=10,
        uninvoiced_qty=0,
    )
    hold_core = _core(
        customer="A客户",
        order_no="SO-HTML-1",
        line_no="3",
        item_code="ITEM-C",
        item_name="产品C",
        quantity=20,
        amount=8000,
        order_outbound_status="partially_outbound",
        line_outbound_status="partially_outbound",
        latest_outbound_date="2026-03-01",
        executed_shipped_qty=5,
        invoiced_qty=0,
        uninvoiced_qty=20,
    )
    no_alert_core = _core(
        customer="A客户",
        order_no="SO-NO-ALERT",
        line_no="1",
        item_code="ITEM-X",
        item_name="不应出现产品",
        quantity=8,
        amount=6000,
        order_outbound_status="partially_outbound",
        line_outbound_status="partially_outbound",
        latest_outbound_date="2026-03-01",
        executed_shipped_qty=2,
        invoiced_qty=0,
        uninvoiced_qty=8,
    )
    _add_order_record(db, record_id="rec-should", job_id=job.id, file_id=file.id, source_row=1, core=should_core)
    _add_order_record(db, record_id="rec-done", job_id=job.id, file_id=file.id, source_row=2, core=done_core)
    _add_order_record(db, record_id="rec-hold", job_id=job.id, file_id=file.id, source_row=3, core=hold_core)
    _add_order_record(db, record_id="rec-no-alert", job_id=job.id, file_id=file.id, source_row=4, core=no_alert_core)
    _add_order_record(
        db,
        record_id="rec-should-uninvoiced",
        job_id=job.id,
        file_id=file.id,
        source_row=5,
        core=should_uninvoiced_core,
    )
    _add_alert(db, alert_id="alert-html", job_id=job.id, group_id=group.id, record_id="rec-should", core=should_core, days=79)
    _add_alert(
        db,
        alert_id="alert-html-uninvoiced",
        job_id=job.id,
        group_id=group.id,
        record_id="rec-should-uninvoiced",
        core=should_uninvoiced_core,
        days=79,
    )
    db.commit()

    exported = export_uninvoiced_html(
        db,
        generated_at=datetime(2026, 5, 19, 13, 0, tzinfo=timezone.utc),
    )

    assert exported is not None
    html = exported.decode("utf-8")
    assert "超60天没开票" in html
    assert "A客户" in html
    assert "SO-HTML-1" in html
    assert "产品A（ITEM-A）" in html
    assert "产品D（ITEM-D）" in html
    assert "产品B（ITEM-B）" not in html
    assert "产品C（ITEM-C）" not in html
    assert "已发完，应该催票" not in html
    assert "应该催票" not in html
    assert "产品已部分开票" in html
    assert "产品未开票" in html
    assert "已开完，不用催" not in html
    assert "还没发完，先不催" not in html
    assert "未发完暂不催金额" not in html
    assert "未发完暂不催金额合计" not in html
    assert "订单总金额" in html
    assert "¥61,500.00" in html
    assert "已开票金额" in html
    assert "¥11,400.00" in html
    assert "还差开票金额" in html
    assert "¥41,600.00" in html
    assert "¥42,100.00" in html
    assert "¥52,500.00" not in html
    assert "已开票金额</b>暂无" not in html
    assert "整单已发完 / 应催" not in html
    assert "部分发货 / 有产品应催" not in html
    assert "整单未发完" in html
    assert "客户 1/1" in html
    assert "订单 1/1" in html
    order_summary = html.split('<div class="order-summary">', 1)[1].split("</div>", 1)[0]
    assert order_summary.index("订单总金额") < order_summary.index("已开票金额")
    assert order_summary.index("已开票金额") < order_summary.index("还差开票金额")
    assert order_summary.index("还差开票金额") < order_summary.index("订单总量")
    assert order_summary.index("订单总量") < order_summary.index("已开票数量")
    assert order_summary.index("已开票数量") < order_summary.index("还差开票数量")
    assert ".order-summary { grid-template-columns: repeat(3, minmax(0, 1fr)); }" in html
    assert "出库天数" in html
    assert "距最近出库" not in html
    assert "已超过60天" not in html
    assert "最长已超" not in html
    assert "SO-NO-ALERT" not in html
    assert "未解除" not in html
    assert "@media print" in html
    assert ".order-grid { display: grid; grid-template-columns: 1fr; gap: 12px; }" in html
    assert ".order-grid { grid-template-columns: 1fr; gap: 6mm; }" in html
    assert ".order-grid { grid-template-columns: repeat(2, minmax(0, 1fr));" not in html


def test_uninvoiced_html_skips_disabled_customer():
    db = _session()
    job = UploadJob(id="job-disabled", status="succeeded", created_by="test")
    file = UploadedFile(
        id="file-disabled",
        job_id=job.id,
        document_type="order",
        filename="order.csv",
        storage_path="/tmp/order.csv",
    )
    group = MatchGroup(
        id="group-disabled",
        job_id=job.id,
        group_key="group-disabled",
        summary_json={"aggregate": {"customer": "B客户"}},
    )
    db.add_all([job, file, group])
    core = _core(
        customer="B客户",
        order_no="SO-DISABLED",
        line_no="1",
        item_code="ITEM-D",
        item_name="产品D",
        quantity=10,
        amount=12000,
        order_outbound_status="fully_outbound",
        line_outbound_status="fully_outbound",
        latest_outbound_date="2026-03-01",
        executed_shipped_qty=10,
        invoiced_qty=0,
        uninvoiced_qty=10,
    )
    _add_order_record(db, record_id="rec-disabled", job_id=job.id, file_id=file.id, source_row=1, core=core)
    _add_alert(db, alert_id="alert-disabled", job_id=job.id, group_id=group.id, record_id="rec-disabled", core=core, days=79)
    db.add(
        ViewerCustomerAlertSetting(
            id="setting-disabled",
            customer_name="B客户",
            customer_key="b客户",
            alert_type="ship_after_no_finance",
            is_enabled=False,
            last_reason="允许延期",
            last_operator_name="月总",
        )
    )
    db.commit()

    assert export_uninvoiced_html(db) is None
