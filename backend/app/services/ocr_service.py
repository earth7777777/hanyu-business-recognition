from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image
from rapidocr_onnxruntime import RapidOCR


PDF_EXTS = {".pdf"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}


@lru_cache(maxsize=1)
def _engine() -> RapidOCR:
    # Single OCR engine instance per process for predictable performance.
    return RapidOCR()



def _ocr_image_array(image: np.ndarray) -> str:
    result, _ = _engine()(image)
    if not result:
        return ""

    lines: list[str] = []
    for row in result:
        if not row or len(row) < 2:
            continue
        text = str(row[1]).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)



def ocr_image(path: Path) -> str:
    with Image.open(path) as img:
        arr = np.array(img.convert("RGB"))
    return _ocr_image_array(arr)



def ocr_pdf(path: Path) -> str:
    max_pages = int(os.getenv("OCR_MAX_PAGES", "10"))
    scale = float(os.getenv("OCR_PDF_SCALE", "2.0"))

    pdf = pdfium.PdfDocument(str(path))
    try:
        page_count = min(len(pdf), max_pages)
        pages_text: list[str] = []
        for i in range(page_count):
            page = pdf[i]
            try:
                bitmap = page.render(scale=scale)
                pil_image = bitmap.to_pil()
                arr = np.array(pil_image.convert("RGB"))
                text = _ocr_image_array(arr)
                if text:
                    pages_text.append(text)
            finally:
                page.close()
        return "\n".join(pages_text)
    finally:
        pdf.close()



def ocr_any(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in PDF_EXTS:
        return ocr_pdf(path)
    if suffix in IMAGE_EXTS:
        return ocr_image(path)
    raise ValueError(f"Unsupported OCR file extension: {suffix}")
