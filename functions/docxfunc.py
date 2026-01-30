"""code/functions/docxfunc.py

DOCX (Word) -> logical pages

This parser is designed to match the existing non-PDF ingestion contract used by
`core.ingest_file()` -> `ingest_pages_common()`.

Public API (do NOT change signature):
    parse_docx_to_pages(file_path: str) -> List[Dict[str, Any]]

Returned `pages` format matches `functions/txtfunc.py`:
    [
      {"page_no": 1, "text": "...", "tables": "...", "png_bytes": None},
      ...
    ]

Design goals:
- Stable, reproducible pagination (no dependency on Word rendering engine).
- Preserve chapter/section structure as much as possible:
  * Start a new page on headings up to a configurable level.
  * Keep tables as atomic blocks when possible.
- Keep output text close to original formatting for easier human review.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph


# -------------------------
# Helpers: iterate DOCX in order
# -------------------------

def _iter_block_items(doc: Document) -> Iterable[Union[Paragraph, Table]]:
    """Yield paragraphs and tables in document order."""
    body = doc.element.body
    for child in body.iterchildren():
        # Paragraph
        if child.tag.endswith("}p"):
            yield Paragraph(child, doc)
        # Table
        elif child.tag.endswith("}tbl"):
            yield Table(child, doc)


# -------------------------
# Helpers: headings / lists
# -------------------------

_HEADING_RE = re.compile(r"^(heading|标题)\s*([0-9]+)$", re.IGNORECASE)


def _get_heading_level(p: Paragraph) -> Optional[int]:
    """Return heading level if paragraph is a heading, else None."""
    try:
        style_name = (p.style.name or "").strip()
    except Exception:
        style_name = ""

    m = _HEADING_RE.match(style_name)
    if m:
        try:
            return int(m.group(2))
        except Exception:
            return None

    # Some templates use "Heading 1" etc without exact match; be tolerant.
    if style_name.lower().startswith("heading"):
        nums = re.findall(r"\d+", style_name)
        if nums:
            try:
                return int(nums[0])
            except Exception:
                return None

    if style_name.startswith("标题"):
        nums = re.findall(r"\d+", style_name)
        if nums:
            try:
                return int(nums[0])
            except Exception:
                return None

    return None


def _is_list_paragraph(p: Paragraph) -> bool:
    """Heuristic list detection: numbering/bullets in paragraph properties or style name."""
    # Fast heuristic by style name
    try:
        style_name = (p.style.name or "").lower()
    except Exception:
        style_name = ""
    if "list" in style_name or "项目符号" in style_name or "编号" in style_name:
        return True

    # numPr exists => numbering properties
    try:
        ppr = p._p.pPr  # type: ignore[attr-defined]
        if ppr is not None and ppr.numPr is not None:
            return True
    except Exception:
        pass

    return False


def _format_paragraph_text(p: Paragraph) -> str:
    """Extract paragraph text with lightweight formatting."""
    text = (p.text or "").replace("\r", "").strip()
    if not text:
        return ""

    level = _get_heading_level(p)
    if level is not None:
        # Markdown-ish heading for readability
        hashes = "#" * max(1, min(level, 6))
        return f"{hashes} {text}".strip()

    if _is_list_paragraph(p):
        # We don't try to perfectly reconstruct numbering; keep it readable.
        return f"- {text}".strip()

    return text


# -------------------------
# Helpers: tables
# -------------------------

def _table_to_markdown(tbl: Table, max_cell_chars: int = 2000) -> str:
    """Convert a Word table into a compact markdown-ish text block."""
    rows: List[List[str]] = []
    for r in tbl.rows:
        row_cells: List[str] = []
        for c in r.cells:
            # docx repeats cell.text for merged cells; acceptable for RAG
            t = (c.text or "").replace("\r", "").strip()
            t = re.sub(r"\s+", " ", t)
            if len(t) > max_cell_chars:
                t = t[:max_cell_chars] + "…"
            row_cells.append(t)
        rows.append(row_cells)

    # Remove fully-empty rows
    rows = [r for r in rows if any((x or "").strip() for x in r)]
    if not rows:
        return ""

    # Build a simple pipe table; avoid strict markdown alignment complexities.
    header = rows[0]
    ncols = max(len(r) for r in rows)
    header = header + [""] * (ncols - len(header))

    def fmt_row(r: List[str]) -> str:
        r = r + [""] * (ncols - len(r))
        return "| " + " | ".join(x.replace("|", "\\|") for x in r) + " |"

    lines: List[str] = [fmt_row(header), "| " + " | ".join(["---"] * ncols) + " |"]
    for r in rows[1:]:
        lines.append(fmt_row(r))
    return "\n".join(lines).strip()


# -------------------------
# Pagination
# -------------------------

def _compose_section_path(stack: List[Tuple[int, str]]) -> str:
    titles = [t.strip() for _, t in stack if t.strip()]
    return "/".join(titles).strip()


def parse_docx_to_pages(
    file_path: str,
    soft_limit_chars: int = 1200,
    hard_limit_chars: int = 2000,
    new_page_on_heading_leq: int = 3,
    max_heading_level_in_path: int = 6,
) -> List[Dict[str, Any]]:
    """DOCX -> pages (logical pagination)."""
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError(f"docx file not found: {file_path}")
    if p.suffix.lower() != ".docx":
        raise ValueError(f"not a .docx file: {file_path}")

    doc = Document(str(p))

    pages: List[Dict[str, Any]] = []
    page_no = 1

    section_stack: List[Tuple[int, str]] = []

    cur_blocks: List[str] = []
    cur_tables: List[str] = []
    cur_len = 0

    def flush() -> None:
        nonlocal page_no, cur_blocks, cur_tables, cur_len
        text_body = "\n\n".join(b for b in cur_blocks if (b or "").strip()).strip()
        tables_md = "\n\n".join(t for t in cur_tables if (t or "").strip()).strip()

        if not text_body and not tables_md:
            cur_blocks, cur_tables, cur_len = [], [], 0
            return

        sec_path = _compose_section_path(
            [(lvl, title) for (lvl, title) in section_stack if lvl <= max_heading_level_in_path]
        )
        header = f"[[DOCX_PATH {sec_path}]]" if sec_path else ""
        text = (header + "\n" + text_body).strip() if header else text_body

        pages.append(
            {
                "page_no": page_no,
                "text": text,
                "tables": tables_md,
                "png_bytes": None,
            }
        )
        page_no += 1
        cur_blocks, cur_tables, cur_len = [], [], 0

    def maybe_flush_before_adding(block_text: str) -> None:
        nonlocal cur_len
        add_len = len(block_text or "")
        if cur_len <= 0:
            return
        if hard_limit_chars > 0 and (cur_len + add_len) > hard_limit_chars:
            flush()

    for item in _iter_block_items(doc):
        if isinstance(item, Paragraph):
            raw_text = (item.text or "").replace("\r", "").strip()

            if not raw_text:
                if cur_len >= soft_limit_chars > 0:
                    flush()
                continue

            heading_level = _get_heading_level(item)
            formatted = _format_paragraph_text(item)
            if not formatted:
                continue

            if heading_level is not None and heading_level <= max(1, new_page_on_heading_leq):
                if cur_blocks or cur_tables:
                    flush()
                section_stack[:] = [(lvl, t) for (lvl, t) in section_stack if lvl < heading_level]
                section_stack.append((heading_level, raw_text))

                cur_blocks.append(formatted)
                cur_len += len(formatted)
                continue

            maybe_flush_before_adding(formatted)
            cur_blocks.append(formatted)
            cur_len += len(formatted)

        elif isinstance(item, Table):
            md = _table_to_markdown(item)
            if not md.strip():
                continue

            if cur_len > 0 and hard_limit_chars > 0 and (cur_len + len(md)) > hard_limit_chars:
                flush()

            cur_blocks.append("[Table]")
            cur_len += len("[Table]")
            cur_tables.append(md)

    flush()

    if not pages:
        pages = [{"page_no": 1, "text": "", "tables": "", "png_bytes": None}]

    return pages
