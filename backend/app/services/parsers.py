from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from app.services.ocr_service import IMAGE_EXTS, PDF_EXTS, ocr_any


TABULAR_EXTS = {".csv", ".xls", ".xlsx"}


class ParseResult:
    def __init__(
        self,
        rows: list[dict[str, Any]],
        error: str | None = None,
        source_columns: dict[str, str | None] | None = None,
    ):
        self.rows = rows
        self.error = error
        self.source_columns = source_columns or {}



def _normalize_col(col: str) -> str:
    return str(col).strip().lower().replace(" ", "")



def _find_col(df: pd.DataFrame, aliases: list[str]) -> str | None:
    normalized_map = {_normalize_col(c): c for c in df.columns}
    for alias in aliases:
        key = _normalize_col(alias)
        if key in normalized_map:
            return normalized_map[key]
    return None



def _read_tabular(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    return pd.read_excel(path)



def _coerce_scalar(value: Any) -> Any:
    if pd.isna(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    return text



def parse_with_mapping(path: Path, mapping: dict[str, list[str]]) -> ParseResult:
    try:
        df = _read_tabular(path)
    except Exception as exc:
        return ParseResult([], f"tabular parse failed: {exc}")

    if df.empty:
        source_columns: dict[str, str | None] = {}
        for target_field, aliases in mapping.items():
            source_columns[target_field] = _find_col(df, aliases) if isinstance(aliases, list) else None
        return ParseResult([], None, source_columns=source_columns)

    source_columns: dict[str, str | None] = {}
    for target_field, aliases in mapping.items():
        source_columns[target_field] = _find_col(df, aliases) if isinstance(aliases, list) else None

    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        payload = {}
        for target_field, source_col in source_columns.items():
            payload[target_field] = _coerce_scalar(row[source_col]) if source_col else None
        if any(v is not None for v in payload.values()):
            rows.append(payload)

    return ParseResult(rows, None, source_columns=source_columns)



def _regex_extract(text: str, patterns: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field, pattern in patterns.items():
        match = re.search(pattern, text, flags=re.IGNORECASE)
        result[field] = match.group(1).strip() if match else None
    return result



def parse_document_fallback(path: Path, meta_json: dict[str, Any] | None = None) -> ParseResult:
    """
    Real OCR chain for PDF/image in V1:
    - PDF -> render pages -> OCR
    - Image -> OCR
    """
    meta_json = meta_json or {}
    suffix = path.suffix.lower()

    if suffix in PDF_EXTS or suffix in IMAGE_EXTS:
        try:
            text = ocr_any(path)
        except Exception as exc:
            return ParseResult([], f"ocr failed: {exc}")

        guessed = _regex_extract(
            text,
            {
                "customer": r"(?:客户|customer)[:：\s]*([^\n\r]+)",
                "contract_no": r"(?:合同号|contract)[:：\s]*([A-Za-z0-9\-_/]+)",
                "customer_order_no": r"(?:客户订单号|订单号|order)[:：\s]*([A-Za-z0-9\-_/]+)",
                "item_code": r"(?:料号|物料编码|item\s*code)[:：\s]*([A-Za-z0-9\-_/]+)",
                "item_name": r"(?:品名|物料名称|item\s*name)[:：\s]*([^\n\r]+)",
                "quantity": r"(?:数量|qty)[:：\s]*([0-9]+(?:\.[0-9]+)?)",
                "amount": r"(?:金额|amount)[:：\s]*([0-9]+(?:\.[0-9]+)?)",
                "biz_date": r"(?:日期|date)[:：\s]*([0-9]{4}[\-/][0-9]{1,2}[\-/][0-9]{1,2})",
                "due_date": r"(?:交期|交货日期|due\s*date)[:：\s]*([0-9]{4}[\-/][0-9]{1,2}[\-/][0-9]{1,2})",
                "ship_date": r"(?:发货日期|ship\s*date)[:：\s]*([0-9]{4}[\-/][0-9]{1,2}[\-/][0-9]{1,2})",
                "notice_date": r"(?:通知日期|notice\s*date)[:：\s]*([0-9]{4}[\-/][0-9]{1,2}[\-/][0-9]{1,2})",
                "invoice_date": r"(?:开票日期|invoice\s*date)[:：\s]*([0-9]{4}[\-/][0-9]{1,2}[\-/][0-9]{1,2})",
            },
        )
        merged = {**guessed, **meta_json}
        merged["raw_text"] = text[:8000] if text else ""
        merged["ocr_engine"] = "rapidocr_onnxruntime"
        return ParseResult([merged], None)

    return ParseResult([], f"Unsupported document type for extension {suffix}")



def parse_meta_json(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}
