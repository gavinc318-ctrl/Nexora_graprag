import os
import time
from typing import Optional, List, Dict, Any

import numpy as np
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse

from paddleocr import PaddleOCR, PPStructureV3
from bs4 import BeautifulSoup

app = FastAPI(title="OCR Service")

# OCR Engine cache
_OCR = None
_OCR_LANG = None

# Table Engine cache
_TABLE = None
_TABLE_LANG = None


def get_device() -> str:
    use_gpu = os.getenv("USE_GPU", "0") == "1"
    return "gpu" if use_gpu else "cpu"


def get_ocr(lang: str) -> PaddleOCR:
    global _OCR, _OCR_LANG
    device = get_device()
    if _OCR is None or _OCR_LANG != lang:
        _OCR_LANG = lang
        _OCR = PaddleOCR(
            use_angle_cls=True,
            lang=lang,          # "ch" / "en" / "ar"
            device=device
        )
    return _OCR


def get_table_engine(lang: str) -> PPStructureV3:
    """
    PPStructureV3 输出结构化结果，其中 table 通常含 html。
    """
    global _TABLE, _TABLE_LANG
    device = get_device()
    if _TABLE is None or _TABLE_LANG != lang:
        _TABLE_LANG = lang
        #_TABLE = PPStructureV3(lang=lang, device=device)
         # ✅ 明确禁用高性能推理插件（避免 require_hpip 依赖检查）
        try:
            _TABLE = PPStructureV3(lang=lang, device=device, enable_hpi=False)
        except TypeError:
            try:
                _TABLE = PPStructureV3(lang=lang, device=device, use_hpip=False)
            except TypeError:
                _TABLE = PPStructureV3(lang=lang, device=device)
    return _TABLE


@app.get("/health")
def health():
    return {"ok": True}


def pick_text_score(rec):
    txt = ""
    score = None

    if rec is None:
        return txt, score

    if isinstance(rec, (tuple, list)):
        if len(rec) >= 2:
            txt = rec[0]
            score = rec[1]
        elif len(rec) == 1:
            txt = rec[0]
        else:
            txt = ""
    elif isinstance(rec, dict):
        txt = rec.get("text") or rec.get("label") or rec.get("value") or ""
        score = rec.get("score") or rec.get("prob") or rec.get("confidence")
    else:
        txt = str(rec)

    txt = "" if txt is None else str(txt)
    try:
        score = float(score) if score is not None else None
    except Exception:
        score = None

    return txt, score


def html_table_to_markdown(html: str) -> str:
    """
    把单个 <table> HTML 转成 Markdown 表格。
    这是“简单可靠版”：不完美处理 colspan/rowspan，但足够作为 VLM 的结构证据。
    """
    soup = BeautifulSoup(html, "lxml" if _has_lxml() else "html.parser")
    table = soup.find("table")
    if not table:
        return ""

    rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        row = []
        for c in cells:
            # 取纯文本，压缩空白
            text = " ".join(c.get_text(separator=" ", strip=True).split())
            row.append(text)
        if row:
            rows.append(row)

    if not rows:
        return ""

    # 统一列数（按最大列数补齐）
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]

    header = rows[0]
    body = rows[1:] if len(rows) > 1 else []

    md = []
    md.append("| " + " | ".join(header) + " |")
    md.append("| " + " | ".join(["---"] * ncol) + " |")
    for r in body:
        md.append("| " + " | ".join(r) + " |")
    return "\n".join(md)


def _has_lxml() -> bool:
    try:
        import lxml  # noqa
        return True
    except Exception:
        return False


def extract_tables_from_ppstructure(res: Any, max_tables: int = 3) -> List[dict]:
    """
    返回:
      tables = [
        { "html": str, "markdown": str, "neighbor_texts": [str, ...] },
        ...
      ]
    """
    tables: List[dict] = []

    if not res:
        return tables

    # res 通常是 [doc_dict]
    doc = None
    if isinstance(res, list) and res and isinstance(res[0], dict):
        doc = res[0]
    elif isinstance(res, dict):
        doc = res
    if not isinstance(doc, dict):
        return tables

    table_list = doc.get("table_res_list") or []
    if not isinstance(table_list, list):
        return tables

    for it in table_list:
        if len(tables) >= max_tables:
            break
        if not isinstance(it, dict):
            continue

        # 你当前版本：HTML 在 pred_html
        html = it.get("pred_html") or it.get("html")
        if not html and isinstance(it.get("res"), dict):
            html = it["res"].get("html")
        if not html or not isinstance(html, str):
            continue

        md = html_table_to_markdown(html)

        neighbor = it.get("neighbor_texts")
        if neighbor is None:
            neighbor_list: List[str] = []
        elif isinstance(neighbor, list):
            neighbor_list = [str(x).strip() for x in neighbor if str(x).strip()]
        else:
            neighbor_list = [str(neighbor).strip()] if str(neighbor).strip() else []

        tables.append({
            "html": html,
            "markdown": md,
            "neighbor_texts": neighbor_list,
        })

    return tables




@app.post("/ocr/image")
async def ocr_image(
    file: UploadFile = File(...),
    lang: str = Form("en"),
    return_boxes: int = Form(1),
    return_debug: int = Form(1),
    return_tables: int = Form(1),    # ✅ 新增：是否跑表格识别
    max_tables: int = Form(3),       # ✅ 新增：最多返回几个表
) -> JSONResponse:
    t0 = time.time()
    content = await file.read()

    import cv2

    img_arr = np.frombuffer(content, np.uint8)
    img = cv2.imdecode(img_arr, cv2.IMREAD_COLOR)

    debug: Dict[str, Any] = {"lang": lang, "filename": file.filename}

    if img is None:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": "Invalid image", "debug": debug},
        )

    h, w = img.shape[:2]
    debug["image_size"] = [w, h]

    # -------------------------
    # OCR
    # -------------------------
    ocr = get_ocr(lang)
    try:
        if hasattr(ocr, "ocr"):
            try:
                result = ocr.ocr(img, cls=True)
            except TypeError as e:
                if "cls" in str(e):
                    result = ocr.ocr(img)
                else:
                    raise
        elif hasattr(ocr, "predict"):
            result = ocr.predict(img)
        else:
            result = ocr(img)
    except Exception as e:
        debug["exception"] = repr(e)
        return JSONResponse(status_code=500, content={"ok": False, "error": "OCR failed", "debug": debug})

    lines: List[str] = []
    boxes: List[Any] = []
    scores: List[float] = []

    d = None
    if isinstance(result, dict):
        d = result
    elif isinstance(result, list) and len(result) == 1 and isinstance(result[0], dict):
        d = result[0]

    if d is not None:
        rec_texts = d.get("rec_texts") or []
        rec_scores = d.get("rec_scores") or []
        rec_boxes = d.get("rec_polys") or d.get("rec_boxes") or []

        for i, t in enumerate(rec_texts):
            t = "" if t is None else str(t).strip()
            if not t:
                continue
            lines.append(t)
            if return_boxes:
                s = rec_scores[i] if i < len(rec_scores) else None
                try:
                    scores.append(float(s) if s is not None else 0.0)
                except Exception:
                    scores.append(0.0)

                b = rec_boxes[i] if i < len(rec_boxes) else None
                if b is not None:
                    if isinstance(b, np.ndarray):
                        b = b.tolist()
                    boxes.append(b)
    else:
        # 老结构解析（你当前主要走 dict/[dict]，这里保留兜底）
        try:
            dets = result[0] if isinstance(result, list) and len(result) > 0 else result
            for item in dets or []:
                box = None
                rec = None
                if isinstance(item, (list, tuple)):
                    if len(item) >= 2:
                        box = item[0]
                        rec = item[1]
                    elif len(item) == 1:
                        rec = item[0]
                    else:
                        continue
                elif isinstance(item, dict):
                    box = item.get("box") or item.get("bbox")
                    rec = item.get("rec") or item.get("text") or item
                else:
                    rec = item

                txt, score = pick_text_score(rec)
                if txt.strip():
                    lines.append(txt.strip())
                    if return_boxes and box is not None:
                        if isinstance(box, np.ndarray):
                            box = box.tolist()
                        boxes.append(box)
                    if return_boxes:
                        scores.append(score if score is not None else 0.0)
        except Exception as e:
            debug["parse_exception"] = repr(e)

    text = "\n".join(lines).strip()

    # -------------------------
    # Tables (PPStructureV3)
    # -------------------------
    tables: List[dict] = []


    if int(return_tables) == 1:
        try:
            t_table0 = time.time()
            table_engine = get_table_engine(lang)
            table_res = table_engine.predict(img)

            tables = extract_tables_from_ppstructure(table_res, max_tables=max_tables)

            debug["tables_num"] = len(tables)
            debug["tables_elapsed_ms"] = int((time.time() - t_table0) * 1000)
        except Exception as e:
            debug["tables_exception"] = repr(e)
            debug["tables_num"] = len(tables)
            debug["tables_elapsed_ms"] = int((time.time() - t_table0) * 1000)
        except Exception as e:
            debug["tables_exception"] = repr(e)

    debug["num_lines"] = len(lines)
    debug["elapsed_ms"] = int((time.time() - t0) * 1000)
    if lines:
        debug["avg_score"] = sum(scores) / max(len(scores), 1) if scores else 0.0
    else:
        debug["avg_score"] = 0.0
        debug["empty_reason_hint"] = "No text detected (check image quality/rotation/lang/threshold)."

    payload: Dict[str, Any] = {
        "ok": True,
        "text": text,
        "lines": lines,
        "tables": tables,
    }

    if return_boxes:
        payload["boxes"] = boxes
        payload["scores"] = scores
    if return_debug:
        payload["debug"] = debug

    return JSONResponse(content=payload)
