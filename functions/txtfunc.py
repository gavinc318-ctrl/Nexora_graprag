# code/functions/txtfunc.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _read_text_with_fallback(path: Path) -> str:
    """
    读取 txt，尽量稳：
    1) utf-8-sig（兼容 BOM）
    2) utf-8
    3) latin-1（兜底不报错）
    """
    raw = path.read_bytes()

    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass

    # 兜底：不抛错，保证能读出来
    return raw.decode("latin-1", errors="replace")


def _split_into_pages(text: str, max_chars_per_page: int) -> List[str]:
    """
    将长文本切成多个“伪页”。
    - 尽量在换行处切开
    - max_chars_per_page<=0 则不切
    """
    text = (text or "").strip()
    if not text:
        return [""]

    if max_chars_per_page <= 0 or len(text) <= max_chars_per_page:
        return [text]

    pages: List[str] = []
    start = 0
    n = len(text)

    while start < n:
        end = min(start + max_chars_per_page, n)
        # 尽量往后找一个换行作为切点（最多找 3000 字符范围）
        if end < n:
            window_end = min(end + 3000, n)
            nl = text.rfind("\n", end, window_end)
            if nl != -1 and nl > start:
                end = nl

        chunk = text[start:end].strip()
        pages.append(chunk)
        start = end

    return pages


def parse_txt_to_pages(
    file_path: str,
    max_chars_per_page: int = 5000,
) -> List[Dict[str, Any]]:
    """
    TXT -> pages（统一结构）
    返回：
      [
        {"page_no": 1, "text": "...", "tables": "", "png_bytes": None},
        ...
      ]
    """
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"txt file not found: {file_path}")

    text = _read_text_with_fallback(p)

    # 轻度清理：避免极端空行爆炸
    # 不做过度 normalize，保留原始格式以便校对
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    page_texts = _split_into_pages(text, max_chars_per_page=max_chars_per_page)

    pages: List[Dict[str, Any]] = []
    for i, page_text in enumerate(page_texts, start=1):
        pages.append(
            {
                "page_no": i,
                "text": page_text,
                "tables": "",
                "png_bytes": None,  # txt 暂时不生成预览图（第二阶段可做“文本快照图”）
            }
        )
    return pages
