"""functions/imgfunc.py

JPG/PNG → OCR → pages

This module is used by `core.ingest_file()` for image ingestion.

Do NOT change the public function signature:
    parse_image_to_pages(file_path: str) -> List[Dict[str, Any]]

Returned `pages` format matches `functions/txtfunc.py`:
    [
      {
        "page_no": 1,
        "text": "...",        # OCR plain text
        "tables": "...",      # OCR tables in markdown (optional)
        "figures": "...",     # VLM figures in markdown (optional)
        "png_bytes": b"..."   # PNG bytes for preview / MinIO upload
      }
    ]

Notes:
- For JPG input we always convert to PNG bytes before OCR, because
  `paddle_ocr_from_png_bytes` expects PNG.
- For PNG input we still re-encode to a normalized PNG (RGBA/Palette edge cases)
  to keep downstream stable.
"""

from __future__ import annotations

import os, base64
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Tuple
import fitz
from PIL import Image, ImageOps
from functions.vlmfunc import (
    vlm_page_to_rag_jason,
    vlm_tables_to_markdown,
    vlm_figures_to_markdown,
)
import config
from functions.ocrfunc import paddle_ocr_from_png_bytes, tables_to_block

def _png_bytes_to_data_url(png_bytes: bytes) -> str:
    b64 = base64.b64encode(png_bytes).decode("utf-8")
    return f"data:image/png;base64,{b64}"


def _tables_to_markdown(tables: List[Dict[str, Any]]) -> str:
    """Convert OCR tables structure into a compact markdown block."""
    blocks: List[str] = []
    for i, t in enumerate(tables or [], start=1):
        if not isinstance(t, dict):
            continue
        neighbor = t.get("neighbor_texts") or []
        md = (t.get("markdown") or "").strip()
        html = (t.get("html") or "").strip()

        parts: List[str] = [f"### Table {i}"]
        if isinstance(neighbor, list) and neighbor:
            neigh_txt = "\n".join(str(x).strip() for x in neighbor if str(x).strip())
            if neigh_txt.strip():
                parts.append("Neighbor Texts:\n" + neigh_txt)

        if md:
            parts.append(md)
        elif html:
            # Fallback: keep html evidence if markdown is missing
            parts.append(html)

        block = "\n\n".join(p for p in parts if p).strip()
        if block:
            blocks.append(block)
    return "\n\n".join(blocks).strip()


def _to_png_bytes(file_path: str) -> bytes:
    """Load image file and return normalized PNG bytes."""
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"image file not found: {file_path}")

    # Pillow auto-detects format (jpg/png). Use exif_transpose to fix rotation.
    with Image.open(p) as im:
        im = ImageOps.exif_transpose(im)

        # Ensure image is decodable everywhere: convert unusual modes to RGB/RGBA.
        # - "P" (palette) and "LA" etc can cause weirdness in downstream.
        if im.mode in ("P", "L", "LA", "1"):
            im = im.convert("RGB")
        elif im.mode not in ("RGB", "RGBA"):
            # CMYK, YCbCr, etc
            im = im.convert("RGB")

        buf = BytesIO()
        # Optimize + deterministic-ish encoding
        im.save(buf, format="PNG", optimize=True)
        return buf.getvalue()


def parse_image_to_pages(
        file_path: str,
        use_ocr: bool = True,
        use_vlm: bool = True,
        ocr_lang: str = "ar",
        ) -> List[Dict[str, Any]]:
    """Parse a JPG/PNG image into unified pages:

    Returns a single-page list. (If later you want multi-image containers like
    TIFF/PDF, implement another parser without changing this interface.)
    """
    png_bytes = _to_png_bytes(file_path)
    max_tables = int(getattr(config, "OCR_MAX_TABLES", 3) or 3)
    combined_hint_parts: List[str] = []
    ocr_text = ""
    ocr_tables = []
    if use_ocr:
        # ---- OCR 远程调用 ----
        ocr_text, ocr_tables, ocr_logs = paddle_ocr_from_png_bytes(
            png_bytes=png_bytes,
            lang=ocr_lang,
            max_tables=max_tables
        )
        ocr_text_hint = ocr_text or ""
        table_blob=tables_to_block(ocr_tables)

        # ✅ 仅在 OCR 模式下追加：OCR 文本 
        if ocr_text_hint.strip():
            combined_hint_parts.append("OCR Text:\n" + ocr_text_hint.strip())

        
        # 仅在ocr模式下追加OCR表格
        if table_hint_md := (table_blob or "").strip():
            combined_hint_parts.append("Tables:\n" + table_hint_md.strip())


    text_hint = "\n\n".join(combined_hint_parts).strip()
    if len(text_hint) > 4000:
        text_hint = text_hint[:4000] + "\n...(text hint truncated)"

    if use_vlm or not use_ocr:
        vlm_text = ""
        vlm_tables = ""
        vlm_figures = ""

        page_png_data_url = _png_bytes_to_data_url(png_bytes)
        vlm_payload = vlm_page_to_rag_jason(
            page_png_data_url=page_png_data_url,
            page_no = 1,
            lang = ocr_lang,
            text_hint=text_hint if text_hint else None,
        )
        vlm_text = (vlm_payload.get("text") or "").strip()
        vlm_tables = vlm_tables_to_markdown(vlm_payload.get("tables") or [])
        vlm_figures = vlm_figures_to_markdown(vlm_payload.get("figures") or [])
        page = {
            "page_no": 1,
            "text": vlm_text,
            "tables": vlm_tables,
            "figures": vlm_figures,
            "png_bytes": png_bytes,
        }
    else:
        page = {
            "page_no": 1,       
            "text": (ocr_text or "").strip(),
            "tables": _tables_to_markdown(ocr_tables),
            "png_bytes": png_bytes,        
        } 
    return [page]
