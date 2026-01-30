from pathlib import Path
import fitz  # PyMuPDF
import base64
import re
import uuid
import statistics
import os
from collections import defaultdict
from typing import List, Tuple, Optional, Dict, Any
import config
from concurrent.futures import ThreadPoolExecutor, as_completed
from functions.object_store import ObjectStore
from functions.ocrfunc import paddle_ocr_from_png_bytes, tables_to_block
from functions.vlmfunc import (
    vlm_page_to_rag_jason,
    vlm_tables_to_markdown,
    vlm_figures_to_markdown
)

# =========================
# 工具函数
# =========================

def _pixmap_to_png_data_url(pix: fitz.Pixmap) -> str:
    png_bytes = pix.tobytes("png")
    b64 = base64.b64encode(png_bytes).decode("utf-8")
    return f"data:image/png;base64,{b64}"

def render_page_png_data_url(page: fitz.Page, target_long_edge: int = 1280) -> str:
    rect = page.rect  # PDF points
    # 72 points = 1 inch, 近似按像素换算，先粗略估
    base_w, base_h = rect.width, rect.height
    # zoom 后的像素大致与 points 成比例
    zoom = target_long_edge / max(base_w, base_h)
    zoom = max(0.8, min(zoom, 2.0))  # 给个合理范围
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    return _pixmap_to_png_data_url(pix)

def render_page_png_bytes(page: fitz.Page, zoom: float = 2.0) -> bytes:
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    return pix.tobytes("png")

def _extract_textlayer_tables_and_exclusions(
    page: fitz.Page,
    y_tol: float = 3.0,
) -> Tuple[List[str], List[str]]:
    """
    共享表格识别逻辑：返回 table markdown 列表、以及非表格纯文本行。
    """
    words = page.get_text("words")
    if not words:
        return [], []

    lines = _cluster_lines_by_y(words, y_tol=y_tol)

    tables_md: List[str] = []
    plain_lines: List[str] = []
    table_line_texts: List[str] = []

    table_buf: List[List[str]] = []
    table_buf_words: List[List[Tuple[float, float, float, float, str, int, int, int]]] = []

    def flush_table():
        nonlocal table_buf, table_buf_words
        if not table_buf:
            return
        if _looks_like_table_by_cols(table_buf):
            md = _rows_to_markdown_table(table_buf)
            if md:
                tables_md.append(md)
            table_line_texts.extend([_line_words_to_text(lw) for lw in table_buf_words])
        table_buf = []
        table_buf_words = []

    for line_words in lines:
        cols = _line_words_to_columns(line_words)
        if len(cols) >= 2:
            table_buf.append(cols)
            table_buf_words.append(line_words)
        else:
            flush_table()
            t = _line_words_to_text(line_words)
            if t:
                plain_lines.append(t)

    flush_table()

    # === 兜底：交替行（文本 -> 数字）转两列表格 ===
    if not tables_md:
        def is_num(s: str) -> bool:
            s = s.strip().replace(",", "")
            return bool(re.fullmatch(r"[-+]?\d+(\.\d+)?", s))

        seq = [s.strip() for s in plain_lines if s.strip()]
        if len(seq) >= 6:
            pair_cnt = 0
            for i in range(0, len(seq) - 1):
                if (not is_num(seq[i])) and is_num(seq[i + 1]):
                    pair_cnt += 1

            if pair_cnt >= max(3, len(seq) // 3):
                rows = [["名称", "数值"]]
                i = 0
                while i < len(seq) - 1:
                    if (not is_num(seq[i])) and is_num(seq[i + 1]):
                        rows.append([seq[i], seq[i + 1]])
                        table_line_texts.append(seq[i])
                        table_line_texts.append(seq[i + 1])
                        i += 2
                    else:
                        i += 1
                md = _rows_to_markdown_table(rows)
                if md:
                    tables_md = [md]

    if table_line_texts:
        def _norm_line(s: str) -> str:
            return re.sub(r"\s+", " ", s or "").strip()

        table_line_texts_norm = {_norm_line(s) for s in table_line_texts if s and s.strip()}
        plain_lines = [s for s in plain_lines if _norm_line(s) not in table_line_texts_norm]

    return tables_md, plain_lines


def extract_textlayer_md_and_plain(page: fitz.Page) -> Tuple[str, str]:
    """
    合并版：一次计算，返回 (textlayer_md, textlayer_plain)。
    - textlayer_md: 仅表格 Markdown
    - textlayer_plain: 排除已进入表格的文本行
    """
    tables_md, plain_lines = _extract_textlayer_tables_and_exclusions(
        page, y_tol=3.0
    )
    textlayer_md = "\n\n".join(t.strip() for t in tables_md if t and t.strip()).strip()
    textlayer_plain = "\n".join(plain_lines).strip()
    return textlayer_md, textlayer_plain


def _looks_like_table(lines: List[str]) -> bool:
    """
    轻量启发式：多行、包含多列分隔特征（多空格对齐/重复分隔符/数字列等）。
    你后续可替换为更强的表格检测（camelot/tabula/pdfplumber）。
    """
    if len(lines) < 4:
        return False
    # 多行含有“明显分列”迹象：连续空格、制表符、或重复出现的分隔符
    col_like = 0
    for s in lines[:20]:
        if "\t" in s or re.search(r"\s{2,}", s) or ("|" in s and s.count("|") >= 2):
            col_like += 1
    return col_like >= max(3, len(lines) // 3)

def _table_lines_to_markdown(lines: List[str]) -> Optional[str]:
    """
    将疑似表格行转为 markdown。
    规则：先用 (tab / 多空格 / |) 切列，取最大列数对齐。
    """
    if not lines:
        return None

    rows = []
    for s in lines:
        s = s.strip()
        if not s:
            continue
        if "|" in s and s.count("|") >= 2:
            cols = [c.strip() for c in s.strip("|").split("|")]
        elif "\t" in s:
            cols = [c.strip() for c in s.split("\t")]
        else:
            cols = [c.strip() for c in re.split(r"\s{2,}", s)]
        cols = [c for c in cols if c != ""]
        if len(cols) >= 2:
            rows.append(cols)

    if len(rows) < 2:
        return None

    ncol = max(len(r) for r in rows)
    # pad
    rows = [r + [""] * (ncol - len(r)) for r in rows]

    header = rows[0]
    body = rows[1:]

    md = []
    md.append("| " + " | ".join(header) + " |")
    md.append("| " + " | ".join(["---"] * ncol) + " |")
    for r in body:
        md.append("| " + " | ".join(r) + " |")

    return "\n".join(md)


def _cluster_lines_by_y(words: List[Tuple[float,float,float,float,str,int,int,int]], y_tol: float = 3.0):
    """words: (x0,y0,x1,y1,text,block_no,line_no,word_no)"""
    # 按 y0 排序
    words_sorted = sorted(words, key=lambda w: (w[1], w[0]))
    lines = []
    cur = []
    cur_y = None
    for w in words_sorted:
        y = w[1]
        if cur_y is None or abs(y - cur_y) <= y_tol:
            cur.append(w)
            cur_y = y if cur_y is None else (cur_y*0.7 + y*0.3)
        else:
            lines.append(cur)
            cur = [w]
            cur_y = y
    if cur:
        lines.append(cur)
    return lines

def _line_words_to_text(line_words):
    # 按 x 排序拼成一行文本
    ws = sorted(line_words, key=lambda w: w[0])
    return " ".join(w[4] for w in ws).strip()

def _line_words_to_columns(
    line_words,
    gap_factor: float = 1.6,
    min_abs_gap: float = 12.0,
):
    """
    用 x 间隙切列：若相邻词 x gap 明显大，就认为换列。
    这是最小可用启发式，比靠空格靠谱很多。
    """
    ws = sorted(line_words, key=lambda w: w[0])
    if not ws:
        return []

    # 估计“正常词间距”
    gaps = []
    for i in range(1, len(ws)):
        prev = ws[i-1]
        cur = ws[i]
        gap = cur[0] - prev[2]
        if gap > 0:
            gaps.append(gap)
    if not gaps:
        return [" ".join(w[4] for w in ws).strip()]

    # 单一间隙时，用词宽估计“正常间距”，避免 gap_factor 失效
    if len(gaps) == 1:
        gap = gaps[0]
        word_widths = [max(0.0, w[2] - w[0]) for w in ws]
        avg_word_w = statistics.median(word_widths) if word_widths else 0.0
        if gap >= max(min_abs_gap, avg_word_w * 0.8):
            return [ws[0][4], ws[1][4]]
        return [" ".join(w[4] for w in ws).strip()]

    base_gap = statistics.median(gaps)
    cut = max(base_gap * gap_factor, min_abs_gap)

    cols = []
    cur_col = [ws[0][4]]
    for i in range(1, len(ws)):
        prev = ws[i-1]
        cur = ws[i]
        gap = cur[0] - prev[2]
        if gap > cut:
            cols.append(" ".join(cur_col).strip())
            cur_col = [cur[4]]
        else:
            cur_col.append(cur[4])
    cols.append(" ".join(cur_col).strip())
    return cols

def _looks_like_table_by_cols(rows: List[List[str]]) -> bool:
    if len(rows) < 3:
        return False
    ncols = [len(r) for r in rows]
    # 至少有多列，并且列数相对稳定
    if max(ncols) < 2:
        return False
    common = max(set(ncols), key=ncols.count)
    stable = sum(1 for x in ncols if x == common) >= max(2, len(rows)//2)
    return stable and common >= 2

def _rows_to_markdown_table(rows: List[List[str]]) -> Optional[str]:
    if not rows:
        return None
    ncol = max(len(r) for r in rows)
    if ncol < 2:
        return None
    rows = [r + [""]*(ncol-len(r)) for r in rows]
    header = rows[0]
    body = rows[1:]
    md = []
    md.append("| " + " | ".join(header) + " |")
    md.append("| " + " | ".join(["---"]*ncol) + " |")
    for r in body:
        md.append("| " + " | ".join(r) + " |")
    return "\n".join(md)

def page_textlayer_to_markdown(page: fitz.Page) -> str:
    """
    只返回表格的 Markdown（不返回普通文本）
    """
    textlayer_md, _ = extract_textlayer_md_and_plain(page)
    return textlayer_md.strip() if textlayer_md else ""


# =========================
# PDF ocr并行处理
# =========================
def ocr_pdf_pages_parallel(
    pdf_path: str,
    ocr_lang: str,
    max_workers: int = 6,
    max_tables: int = 3,

):
    """
    并行跑 OCR（不跑 VLM）
    返回：results_by_page_no: dict[int, dict]
      每页 dict: { "ocr_text": str, "table_block": str, "logs": str }
    """
    p=Path(pdf_path)
    doc = fitz.open(p)
    stem = Path(p).stem
    def _job(page_no: int):
        page = doc[page_no - 1]

        # 1) render png
        png_bytes = render_page_png_bytes(page, zoom=2.0)

        # 2) remote ocr
        ocr_text, ocr_tables, ocr_logs = paddle_ocr_from_png_bytes(
            png_bytes=png_bytes,
            lang=ocr_lang,
            max_tables=max_tables,
        )
        ocr_text = (ocr_text or "").strip()
        table_block = tables_to_block(ocr_tables)

        return page_no, {
            "ocr_text": ocr_text,
            "table_block": table_block,
            "logs": ocr_logs or "",
            "png": png_bytes
        }
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs = [ex.submit(_job, pno) for pno in range(1, doc.page_count + 1)]
        for fut in as_completed(futs):
            page_no, payload = fut.result()
            results[page_no] = payload

    doc.close()
    return results

# =========================
# PDF 多模态解析（单ocr走并行cpu计算，vlm混合计算走串行）
# =========================
def extract_pdf_multimodal_rag(
    pdf_path: str,
    use_ocr: bool = True,
    use_vlm: bool = True,
    ocr_lang: str = "ar",
) ->List[Dict[str, Any]]:
    p = Path(pdf_path)
    if not p.exists():
        raise FileNotFoundError(p)
    doc = fitz.open(p)

    if not use_vlm and use_ocr:
        ocr_results = ocr_pdf_pages_parallel(
            pdf_path=pdf_path,
            ocr_lang=ocr_lang,
            max_workers= config.OCR_MAX_WORKER,     # 你可以做成参数
            max_tables= config.OCR_MAX_TABLES,
        )
        doc = fitz.open(pdf_path)
        

        pages: List[Dict[str, Any]] = []
        for page_no in range(1, doc.page_count + 1):
            page = doc[page_no - 1]
            text_parts: List[str] = []
            table_parts: List[str] = []

            r = ocr_results.get(page_no, {}) or {}
            

            #✅ 追加：OCR 文本
            ocr_text = (r.get("ocr_text") or "").strip()
            if ocr_text:
                text_parts.append(ocr_text)

            #✅ 追加：OCR 表格
            ocr_table = (r.get("table_block") or "").strip()
            if ocr_table:
                table_parts.append(ocr_table)
            page_texts = "\n\n".join([x for x in text_parts if x.strip()]).strip()
            page_tables = "\n\n".join([x for x in table_parts if x.strip()]).strip()

            pages.append(
                {
                    "page_no": page_no,          # 用真实页号
                    "text": page_texts,        
                    "tables": page_tables,    
                    "figures": None,
                    "png_bytes": r.get("png"),   # 如需预览图，后面再加（或复用你已有的 OCR png）
                }
            )

        doc.close()

        return pages

    #===========================================================================================
    # 串行VLM识别，由于GPU不需要本地实现并行算力分解
    #===========================================================================================
    pages: List[Dict[str, Any]] = []
    
    for i in range(len(doc)):
        page = doc.load_page(i)
        page_no = i + 1
        combined_hint_parts: List[str] = []
        #生成当前页的快照，用于保存证据和OCR识别
        png_bytes = render_page_png_bytes(page, zoom=2.0)

        # ---- pdf 文本层直接抽取----
        # ✅ 不管是否 OCR，都给 VLM：PDF plain text + PDF markdown
        textlayer_md, textlayer_plain = extract_textlayer_md_and_plain(page)
        if textlayer_plain.strip():
            combined_hint_parts.append("PDF TextLayer (Plain Text):\n" + textlayer_plain.strip())
        if textlayer_md.strip():
            combined_hint_parts.append("PDF TextLayer (Markdown):\n" + textlayer_md.strip())

        # ---- pdf ocr抽取----
        if use_ocr:
            # ---- OCR 远程调用 ----
            ocr_text, ocr_tables, ocr_logs = paddle_ocr_from_png_bytes(
                png_bytes=png_bytes,
                lang=ocr_lang,
                max_tables=3,
            )
            table_blob=tables_to_block(ocr_tables)

            # ✅ 仅在 OCR 模式下追加：OCR 文本 + 表格
            if ocr_text := (ocr_text or "").strip():
                combined_hint_parts.append("OCR Text:\n" + ocr_text)

            if table_blob := (table_blob or "").strip():
                combined_hint_parts.append("OCR Tables:\n" + table_blob)

        text_hint = "\n\n".join(combined_hint_parts).strip()
        
        if len(text_hint) > 4000:
            text_hint = text_hint[:4000] + "\n...(text hint truncated)"


        # ✅ 生成PDF页面图像
        page_png_data_url = render_page_png_data_url(page=page, target_long_edge =1280)

        vlm_payload = vlm_page_to_rag_jason(
                        page_png_data_url=page_png_data_url,
                        page_no=page_no,
                        lang = ocr_lang,
                        text_hint=text_hint if text_hint else None,
                      )
        vlm_text = (vlm_payload.get("text") or "").strip()
        vlm_tables = vlm_tables_to_markdown(vlm_payload.get("tables") or [])
        vlm_figures = vlm_figures_to_markdown(vlm_payload.get("figures") or [])
        pages.append(
            {
                "page_no": page_no,       # 用真实页号
                "text": vlm_text,        # vlm 返回
                "tables": vlm_tables,    # vlm不单独返回表格
                "figures": vlm_figures,
                "png_bytes": png_bytes,        # 如需预览图，后面再加（或复用你已有的 OCR png）
            }
        )
    doc.close()
    
    return pages
