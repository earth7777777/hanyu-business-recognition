from __future__ import annotations

from pathlib import Path

from app.services.parsers import parse_document_fallback



def test_parse_document_fallback_extracts_fields_from_ocr_text(monkeypatch, tmp_path: Path):
    sample = tmp_path / "invoice.png"
    sample.write_bytes(b"fake")

    monkeypatch.setattr(
        "app.services.parsers.ocr_any",
        lambda p: "客户: A公司\n合同号: HT-001\n订单号: SO-01\n金额: 1234.50\n日期: 2026-03-19\n",
    )

    result = parse_document_fallback(sample, meta_json={})

    assert result.error is None
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row["customer"] == "A公司"
    assert row["contract_no"] == "HT-001"
    assert row["customer_order_no"] == "SO-01"
    assert row["amount"] == "1234.50"
    assert row["biz_date"] == "2026-03-19"
    assert row["ocr_engine"] == "rapidocr_onnxruntime"
