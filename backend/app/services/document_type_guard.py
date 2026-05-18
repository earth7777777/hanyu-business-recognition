from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from app.services.parsers import IMAGE_EXTS, PDF_EXTS, TABULAR_EXTS, ParseResult, parse_document_fallback


_DOC_TYPES = ("order", "shipment", "payment_notice", "invoice")
_ANCHOR_FIELDS: dict[str, set[str]] = {
    "order": {"due_date"},
    "shipment": {"ship_date"},
    "payment_notice": {"notice_date"},
    "invoice": {"invoice_date", "invoice_formal"},
}
_OCR_HINTS: dict[str, tuple[str, ...]] = {
    "order": ("交期", "交货日期", "due date"),
    "shipment": ("发货日期", "ship date", "发货"),
    "payment_notice": ("付款通知", "付款通知单", "通知日期", "notice date"),
    "invoice": ("发票", "正式发票", "开票日期", "invoice"),
}


def _norm_col(name: Any) -> str:
    return str(name).strip().lower().replace(" ", "").replace("_", "")


def _norm_text(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "").replace("_", "")


def _read_tabular_header(path: Path) -> list[str]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        df = pd.read_csv(path, nrows=0)
    else:
        df = pd.read_excel(path, nrows=0)
    return [str(c) for c in df.columns]


@dataclass
class DetectResult:
    detected_type: str | None
    confidence: str
    weighted_scores: dict[str, float]
    field_hits: dict[str, int]
    anchor_hits: dict[str, int]
    reason: str


class TypeGuardMismatch(Exception):
    def __init__(self, detail: dict[str, Any]):
        super().__init__("document type mismatch")
        self.detail = detail


def _score_confidence(best_hits: int, best_anchor: int, gap: float, best_score: float) -> str:
    if best_score <= 0:
        return "unknown"
    if (best_anchor >= 1 and best_hits >= 2 and gap >= 1.5) or (best_hits >= 4 and gap >= 1.0):
        return "high"
    if best_hits >= 2 and gap >= 0.5:
        return "medium"
    return "low"


def detect_tabular_type(path: Path, field_mappings: dict[str, Any]) -> DetectResult:
    try:
        columns = _read_tabular_header(path)
    except Exception as exc:
        return DetectResult(
            detected_type=None,
            confidence="unknown",
            weighted_scores={},
            field_hits={},
            anchor_hits={},
            reason=f"read_failed: {exc}",
        )

    normalized_cols = {_norm_col(c) for c in columns}
    weighted: dict[str, float] = {}
    field_hits: dict[str, int] = {}
    anchor_hits: dict[str, int] = {}

    for doc_type in _DOC_TYPES:
        mapping = field_mappings.get(doc_type, {})
        if not isinstance(mapping, dict):
            weighted[doc_type] = 0.0
            field_hits[doc_type] = 0
            anchor_hits[doc_type] = 0
            continue

        hit_fields: set[str] = set()
        anchor_count = 0
        for field, aliases in mapping.items():
            if not isinstance(aliases, list):
                continue
            matched = any(_norm_col(alias) in normalized_cols for alias in aliases)
            if not matched:
                continue
            hit_fields.add(field)
            if field in _ANCHOR_FIELDS.get(doc_type, set()):
                anchor_count += 1

        hit_count = len(hit_fields)
        field_hits[doc_type] = hit_count
        anchor_hits[doc_type] = anchor_count
        weighted[doc_type] = float(hit_count) + (anchor_count * 2.0)

    ranked = sorted(weighted.items(), key=lambda x: x[1], reverse=True)
    if not ranked:
        return DetectResult(
            detected_type=None,
            confidence="unknown",
            weighted_scores=weighted,
            field_hits=field_hits,
            anchor_hits=anchor_hits,
            reason="no_doc_type_candidates",
        )

    best_type, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    gap = best_score - second_score

    if best_score <= 0:
        confidence = "unknown"
        detected_type = None
        reason = "no_header_match"
    else:
        detected_type = best_type
        best_hits = field_hits.get(best_type, 0)
        best_anchor = anchor_hits.get(best_type, 0)
        confidence = _score_confidence(best_hits, best_anchor, gap, best_score)
        reason = f"best={best_type},hits={best_hits},anchors={best_anchor},gap={round(gap,3)}"

    return DetectResult(
        detected_type=detected_type,
        confidence=confidence,
        weighted_scores=weighted,
        field_hits=field_hits,
        anchor_hits=anchor_hits,
        reason=reason,
    )


def enforce_tabular_type_guard(
    *,
    expected_type: str,
    path: Path,
    field_mappings: dict[str, Any],
) -> dict[str, Any] | None:
    if path.suffix.lower() not in TABULAR_EXTS:
        return None

    result = detect_tabular_type(path, field_mappings)
    detected = result.detected_type
    if not detected or detected == expected_type:
        return None

    payload = {
        "expected_type": expected_type,
        "detected_type": detected,
        "confidence": result.confidence,
        "reason": result.reason,
        "scores": result.weighted_scores,
        "field_hits": result.field_hits,
        "anchor_hits": result.anchor_hits,
    }

    if result.confidence == "high":
        raise TypeGuardMismatch(
            detail={
                "code": "DOCUMENT_TYPE_MISMATCH",
                "message": f"Expected '{expected_type}' but detected '{detected}' with high confidence.",
                "guard": payload,
            }
        )
    return payload


def detect_ocr_type(
    path: Path,
    field_mappings: dict[str, Any],
    *,
    meta_json: dict[str, Any] | None = None,
) -> tuple[DetectResult, ParseResult]:
    parsed = parse_document_fallback(path, meta_json or {})
    if parsed.error:
        return (
            DetectResult(
                detected_type=None,
                confidence="unknown",
                weighted_scores={},
                field_hits={},
                anchor_hits={},
                reason=f"ocr_parse_failed: {parsed.error}",
            ),
            parsed,
        )

    row = parsed.rows[0] if parsed.rows else {}
    raw_text = _norm_text(row.get("raw_text"))

    weighted: dict[str, float] = {}
    field_hits: dict[str, int] = {}
    anchor_hits: dict[str, int] = {}

    for doc_type in _DOC_TYPES:
        mapping = field_mappings.get(doc_type, {})
        if not isinstance(mapping, dict):
            weighted[doc_type] = 0.0
            field_hits[doc_type] = 0
            anchor_hits[doc_type] = 0
            continue

        hit_fields: set[str] = set()
        anchor_count = 0
        for field, aliases in mapping.items():
            field_has_value = row.get(field) not in (None, "")
            alias_hit = False
            if isinstance(aliases, list) and raw_text:
                alias_hit = any(_norm_text(alias) in raw_text for alias in aliases if str(alias).strip())
            matched = field_has_value or alias_hit
            if not matched:
                continue
            hit_fields.add(field)
            if field in _ANCHOR_FIELDS.get(doc_type, set()):
                anchor_count += 1

        hint_hits = 0
        if raw_text:
            for token in _OCR_HINTS.get(doc_type, ()):
                if _norm_text(token) and _norm_text(token) in raw_text:
                    hint_hits += 1

        hit_count = len(hit_fields)
        field_hits[doc_type] = hit_count
        anchor_hits[doc_type] = anchor_count
        weighted[doc_type] = float(hit_count) + (anchor_count * 2.0) + min(hint_hits, 2) * 0.5

    ranked = sorted(weighted.items(), key=lambda x: x[1], reverse=True)
    if not ranked:
        return (
            DetectResult(
                detected_type=None,
                confidence="unknown",
                weighted_scores=weighted,
                field_hits=field_hits,
                anchor_hits=anchor_hits,
                reason="no_doc_type_candidates",
            ),
            parsed,
        )

    best_type, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    gap = best_score - second_score
    if best_score <= 0:
        detected_type = None
        confidence = "unknown"
        reason = "no_ocr_signal_match"
    else:
        detected_type = best_type
        best_hits = field_hits.get(best_type, 0)
        best_anchor = anchor_hits.get(best_type, 0)
        confidence = _score_confidence(best_hits, best_anchor, gap, best_score)
        reason = f"best={best_type},hits={best_hits},anchors={best_anchor},gap={round(gap,3)}"

    return (
        DetectResult(
            detected_type=detected_type,
            confidence=confidence,
            weighted_scores=weighted,
            field_hits=field_hits,
            anchor_hits=anchor_hits,
            reason=reason,
        ),
        parsed,
    )


def enforce_document_type_guard(
    *,
    expected_type: str,
    path: Path,
    field_mappings: dict[str, Any],
    meta_json: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, ParseResult | None]:
    suffix = path.suffix.lower()
    if suffix in TABULAR_EXTS:
        return enforce_tabular_type_guard(
            expected_type=expected_type,
            path=path,
            field_mappings=field_mappings,
        ), None

    if suffix not in PDF_EXTS and suffix not in IMAGE_EXTS:
        return None, None

    result, parsed = detect_ocr_type(
        path=path,
        field_mappings=field_mappings,
        meta_json=meta_json,
    )
    detected = result.detected_type
    if not detected or detected == expected_type:
        return None, parsed

    payload = {
        "source": "ocr",
        "expected_type": expected_type,
        "detected_type": detected,
        "confidence": result.confidence,
        "reason": result.reason,
        "scores": result.weighted_scores,
        "field_hits": result.field_hits,
        "anchor_hits": result.anchor_hits,
    }
    if result.confidence == "high":
        raise TypeGuardMismatch(
            detail={
                "code": "DOCUMENT_TYPE_MISMATCH",
                "message": f"Expected '{expected_type}' but detected '{detected}' with high confidence.",
                "guard": payload,
            }
        )
    return payload, parsed
