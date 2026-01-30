
from __future__ import annotations
import json, os, requests
from typing import Any, Dict, List, Tuple
import config

# =========================
# OCR处理Table表头
# =========================
def tables_to_block(tables: list[dict]) -> str:
    blocks = []
    for i, t in enumerate(tables or [], start=1):
        if not isinstance(t, dict):
            continue
        neigh = t.get("neighbor_texts") or []
        md = (t.get("markdown") or "").strip()
        blk = [f"## Table (Markdown) {i}"]
        if isinstance(neigh, list) and neigh:
            blk.append("Neighbor Texts:\n" + "\n".join(str(x).strip() for x in neigh if str(x).strip()))
        if md:
            blk.append(md)
        blocks.append("\n\n".join(blk).strip())
    return "\n\n".join(blocks).strip()

def paddle_ocr_from_png_bytes(
    png_bytes: bytes,
    lang: str = "ar",
    max_tables: int = 3,
    timeout_sec: int = 180,
) -> Tuple[str, List[Dict[str, Any]], str]:
    logs: List[str] = []
    text = ""
    tables: List[Dict[str, Any]] = []

    def _normalize_lang(lang: str) -> str:
        lang2 = (lang or "en").strip().lower()
        if lang2 in ("cn", "zh", "zh-cn", "zh_cn", "chinese"):
            return "ch"
        if lang2 in ("arabic", "ar-sa", "ar_sa"):
            return "ar"
        return lang2

    endpoint = config.OCR_ENDPOINT

    try:
        files = {"file": ("page.png", png_bytes, "image/png")}
        data = {
            "lang": _normalize_lang(lang),
            "return_boxes": "0",
            "return_debug": "1",
            "return_tables": "1",
            "max_tables": str(int(max_tables)),
        }

        logs.append(f"[OCR] POST {endpoint}/ocr/image")
        resp = requests.post(f"{endpoint}/ocr/image", files=files, data=data, timeout=timeout_sec)
        resp.raise_for_status()

        payload = resp.json()
        if not payload.get("ok", False):
            logs.append(f"[OCR] remote error: {payload.get('error')}")
            return "", [], "\n".join(logs)

        text = (payload.get("text") or "").strip()

        # ✅ tables 原样保留结构，不合并
        raw_tables = payload.get("tables") or []
        if isinstance(raw_tables, list):
            for t in raw_tables:
                if not isinstance(t, dict):
                    continue
                neighbor = t.get("neighbor_texts") or []
                if isinstance(neighbor, list):
                    neighbor = [str(x).strip() for x in neighbor if str(x).strip()]
                else:
                    neighbor = [str(neighbor).strip()] if str(neighbor).strip() else []

                tables.append({
                    "neighbor_texts": neighbor,
                    "markdown": (t.get("markdown") or "").strip(),
                    # 可选：保留 html 证据，VLM 需要更强证据时很有用
                    "html": (t.get("html") or "").strip(),
                })

        if payload.get("debug") is not None:
            logs.append("[OCR DEBUG]")
            logs.append(json.dumps(payload["debug"], ensure_ascii=False, indent=2))

    except Exception as e:
        logs.append(f"[OCR FATAL] {type(e).__name__}: {e}")

    return text, tables, "\n".join(logs)