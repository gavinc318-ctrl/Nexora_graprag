from pathlib import Path
from datetime import datetime

from typing import Any, Dict, List, Optional, Tuple
import json
import re
import requests

# =========================
# 配置
# =========================
import config


def call_vllm_chat(
        messages: List[Dict[str, Any]],
        temperature:float = config.TEMPERATURE,
        top_p:float=config.TOP_P,
        max_tokens:int = config.MAX_TOKENS
        ) -> str:
    payload = {
        "model": config.MODEL_PATH,
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": max_tokens,
        "stream": False,
    }
    resp = requests.post(config.CHAT_COMPLETIONS_URL, json=payload, timeout=config.TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]

def call_openai_responses_json(
    page_png_data_url: str,
    lang: str,
    text_hint: Optional[str] = None,
) -> Dict[str, Any]:
    LANG_MAP = {
        "ar": "Arabic",
        "ch": "Chinese",
        "en": "English"
    }
    lang = LANG_MAP.get(lang, lang)
    prompt = (
        f"You are reading a screenshot of a PDF page, The page content language is primarily {lang}.\n"
        "Return a single JSON object that matches the schema.\n"
        "Rules:\n"
        "- Use the same language as the page content. Do not translate.\n"
        "- Exclude page numbers, watermarks, and copyrights.\n"
        "- If you see a two-column list of 'Category + Value', output it as a Markdown table.\n"
        "- If a section does not exist, use an empty string or empty array.\n"
    )
    if text_hint:
        prompt += (
            "\n\nThe following text is extracted from the PDF text layer and ocr, is only for correction and proper nouns."
            "Do not copy its line breaks or order; follow the reading order on the screenshot:\n"
            f"{text_hint}\n"
        )

    payload = {
        "model": config.OPENAI_MODEL,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": page_png_data_url},
                ],
            }
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "rag_page",
                "schema": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "tables": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "caption": {"type": "string"},
                                    "markdown": {"type": "string"},
                                },
                                "required": ["caption", "markdown"],
                                "additionalProperties": False,
                            },
                        },
                        "figures": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "caption": {"type": "string"},
                                    "description": {"type": "string"},
                                    "data_table_markdown": {"type": "string"},
                                },
                                "required": ["caption", "description", "data_table_markdown"],
                                "additionalProperties": False,
                            },
                        },
                        "notes": {"type": "string"},
                    },
                    "required": ["text", "tables", "figures", "notes"],
                    "additionalProperties": False,
                },
            },
        },
    }
    headers = {"Authorization": f"Bearer {config.OPENAI_API_KEY}"}
    resp = requests.post(config.OPENAI_RESPONSES_URL, json=payload, headers=headers, timeout=config.TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    output = data.get("output") or []
    for item in output:
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                return json.loads(content.get("text") or "{}")
    raise ValueError("OpenAI Responses returned no output_text")

def vlm_page_to_rag_text_structured(
    page_png_data_url: str,
    page_no: int,
    lang: str = "ar",
    text_hint: Optional[str] = None,
) -> str:
    LANG_MAP = {
        "ar": "Arabic",
        "ch": "Chinese",
        "en": "English"
    }
    lang = LANG_MAP.get(lang, lang)
    prompt = (
        f"You are reading a screenshot of a PDF page. The page content language is primarily {lang}.\n"
        "Please fully transcribe the page content in the following structure:\n"
        "\n"
        "## Text (Nexora)\n"
        "- Main body text in natural reading order\n"
        "\n"
        "## Tables (Nexora)\n"
        "- For each table, include its caption/title line (if any) immediately before the Markdown table\n"
        "- Each table using Markdown table syntax\n"
        "\n"    
        "## Figures (Nexora)\n"
        "- Start each figure block with its caption/title line (if any), then a short description\n"
        "\n"
        "Rules:\n"
        "- Keep the same language as the page content. Do not translate.\n"
        "- Exclude page numbers, watermarks, and copyrights.\n"
        "- If you see a two-column list of 'Category + Value', output it as a Markdown table.\n"
        "- Do not omit table captions, table titles, figure captions, or figure titles when visible.\n"
        "- If a section does not exist, write an empty line under it.\n"
        "\n"
        "Priority and fallback rules:\n"
        "- Use the screenshot (visual content) as the PRIMARY source of truth.\n"
        "- If some content is unclear, partially visible, or low confidence in the screenshot, "
        "you MAY use the PDF text layer to help correct wording, numbers, or proper nouns.\n"
        "- If the screenshot contains LITTLE OR NO readable content (e.g., blank, very blurry, "
        "or recognition fails), you SHOULD reconstruct the page content based on the PDF text layer.\n"
        "- When reconstructing from the PDF text layer, reorganize the content into natural reading order "
        "and fit it into the required sections (Text / Tables / Figures).\n"
        "- Do NOT hallucinate content that is not supported by either the screenshot or the PDF text layer.\n"
    )
    if text_hint:
        prompt += (
            "\n\nThe following text is extracted from the PDF text layer.\n"
            "It may be unordered, fragmented, or lack layout information.\n"
            "Use it ONLY as a secondary reference or as a fallback when visual recognition fails.\n"
            "Do NOT copy its line breaks or original order; reorganize it according to the page structure:\n"
            f"{text_hint}\n"
        )
    messages = [
        {"role": "system", "content": "You are a multimodal assistant that converts PDF page screenshots into retrievable text."},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": page_png_data_url}},
            ],
        },
    ]
    try:
        raw = call_vllm_chat(messages=messages, temperature=0, top_p=1)
        return (raw or "").strip()
    except Exception as e:
        return f"(VLM Page {page_no} failed: {type(e).__name__}: {e})"

def parse_vlm_text_to_payload(raw_text: str, page_no: int) -> Dict[str, Any]:
    text_blocks, tables_blocks, figures_blocks = detect_tables_and_figures(raw_text)
    tables = _tables_from_block(tables_blocks.get("text"))
    figures = _figures_from_block(figures_blocks.get("text"))
    text = (text_blocks.get("text") or "").strip()
    return {
        "page": page_no,
        "text": text,
        "tables": tables,
        "figures": figures,
        "notes": "",
        "raw": raw_text,
        "error": None if text or tables or figures else "EMPTY_VLM_TEXT",
    }

def vlm_page_to_rag_jason(
    page_png_data_url: str,
    page_no: int,
    lang:str = "ar",
    text_hint: Optional[str] = None,
) -> Dict[str, Any]:

    """
    强制：只看页面截图，用 VLM 生成结构化 JSON（文本/表格/图表分离）。
    不用 OCR，不用 PDF 文本层。
    """
    LANG_MAP = {
        "ar": "Arabic",
        "ch": "Chinese",
        "en": "English"
    }

    lang = LANG_MAP.get(lang, lang)


    try:
        provider = (getattr(config, "VLM_PROVIDER", "vllm") or "vllm").lower()
        if provider == "openai":
            payload = call_openai_responses_json(
                page_png_data_url=page_png_data_url,
                lang=lang,
                text_hint=text_hint,
            )
            return _normalize_vlm_payload(payload, page_no=page_no)
        raw_text = vlm_page_to_rag_text_structured(
            page_png_data_url=page_png_data_url,
            page_no=page_no,
            lang=lang,
            text_hint=text_hint,
        )
        return parse_vlm_text_to_payload(raw_text, page_no=page_no)
    except Exception as e:
        return {
            "page": page_no,
            "text": "",
            "tables": [],
            "figures": [],
            "notes": "",
            "error": f"(VLM Page {page_no} failed: {type(e).__name__}: {e})",
        }

def vlm_page_to_rag_text(
    page_png_data_url: str,
    page_no: int,
    lang:str = "ar",
) -> str:
    """
    强制：只看页面截图，生成文本。

    """
    LANG_MAP = {
        "ar": "Arabic",
        "ch": "Chinese",
        "en": "English"
    }
    lang = LANG_MAP.get(lang, lang)
    prompt = (
        f"You are reading a screenshot of a PDF page, The page content language is primarily {lang}.\n"
        "- Use the same language as the page content. Do not translate.\n"
         "- If you see a two-column list of 'Category + Value', output it as a Markdown table.\n"
        "- Transcribe ALL visible text on the page in reading order."
        "- If the page looks like a cover/title page, include the main title and subtitles."
        "- If you can read any text, output it (do not return empty)."
    )
   
    messages = [
        {"role": "system", "content": "You are a multimodal assistant that converts PDF page screenshots into retrievable text."},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": page_png_data_url}},
            ],
        },
    ]
    try:
        raw = call_vllm_chat(messages)
        return (raw or "").strip()
    except Exception as e:
        return  f"(VLM Page {page_no} failed: {type(e).__name__}: {e})"
        

def _extract_json_object(raw: str) -> Optional[str]:
    if not raw:
        return None

    s = raw.strip()

    # 快速路径：整段就是 JSON
    if s.startswith("{") and s.endswith("}"):
        try:
            json.loads(s)
            return s
        except Exception:
            pass

    # 1) 优先从 ```json 代码块提取
    m = re.search(r"```json\s*(\{[\s\S]*?\})\s*```", s, flags=re.I)
    if m:
        candidate = m.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except Exception:
            pass

    in_str = False
    esc = False
    depth = 0
    start = None
    
    # 2) 括号平衡扫描：找第一个完整 JSON object
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        else:
            if ch == '"':
                in_str = True
                continue

        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    candidate = s[start:i+1]
                    # 只返回“能被解析成 JSON”的候选
                    try:
                        json.loads(candidate)
                        return candidate
                    except Exception:
                        # 如果这段不是合法 JSON，继续找下一段
                        start = None
                        continue

    return None


def _normalize_vlm_payload(payload: Dict[str, Any], page_no: int) -> Dict[str, Any]:
    text = str(payload.get("text") or "").strip()
    notes = str(payload.get("notes") or "").strip()

    tables_raw = payload.get("tables") or []
    if isinstance(tables_raw, dict):
        tables_raw = [tables_raw]
    elif isinstance(tables_raw, str):
        tables_raw = [{"caption": "", "markdown": tables_raw}]

    tables: List[Dict[str, str]] = []
    if isinstance(tables_raw, list):
        for t in tables_raw:
            if isinstance(t, str):
                md = t.strip()
                if md:
                    tables.append({"caption": "", "markdown": md})
                continue
            if not isinstance(t, dict):
                continue
            caption = str(t.get("caption") or "").strip()
            markdown = str(t.get("markdown") or t.get("table") or "").strip()
            if caption or markdown:
                tables.append({"caption": caption, "markdown": markdown})

    figures_raw = payload.get("figures") or []
    if isinstance(figures_raw, dict):
        figures_raw = [figures_raw]
    elif isinstance(figures_raw, str):
        figures_raw = [{"caption": "", "description": "", "data_table_markdown": figures_raw}]

    figures: List[Dict[str, str]] = []
    if isinstance(figures_raw, list):
        for f in figures_raw:
            if isinstance(f, str):
                data = f.strip()
                if data:
                    figures.append(
                        {"caption": "", "description": "", "data_table_markdown": data}
                    )
                continue
            if not isinstance(f, dict):
                continue
            caption = str(f.get("caption") or "").strip()
            description = str(f.get("description") or "").strip()
            data_table = str(
                f.get("data_table_markdown") or f.get("data_table") or ""
            ).strip()
            if caption or description or data_table:
                figures.append(
                    {
                        "caption": caption,
                        "description": description,
                        "data_table_markdown": data_table,
                    }
                )

    page_val = payload.get("page", page_no)
    try:
        page_val = int(page_val)
    except Exception:
        page_val = page_no

    return {
        "page": page_val,
        "text": text,
        "tables": tables,
        "figures": figures,
        "notes": notes,
        "raw": payload,
    }


def _parse_vlm_json_response(raw: str, page_no: int) -> Dict[str, Any]:
    payload: Optional[Dict[str, Any]] = None
    try:
        payload = json.loads(raw)
    except Exception:
        json_str = _extract_json_object(raw)
        if json_str:
            try:
                payload = json.loads(json_str)
            except Exception:
                payload = None

    if isinstance(payload, dict):
        return _normalize_vlm_payload(payload, page_no=page_no)

    text_blocks, tables_blocks, figures_blocks = detect_tables_and_figures(raw)
    tables = _tables_from_block(tables_blocks.get("text"))
    figures = _figures_from_block(figures_blocks.get("text"))
    return {
        "page": page_no,
        "text": (text_blocks.get("text") or "").strip(),
        "tables": tables,
        "figures": figures,
        "notes": "",
        "raw": {"fallback_text": raw},
    }


def detect_tables_and_figures(page_texts: str) -> Tuple[Dict[str, Any], Dict[str, Any],Dict[str, Any]]:
    """返回 (text_blocks, special_blocks)。每个 block: {type,page,caption,text}"""
    text_blocks: Dict[str, Any]= {}
    tables_blocks: Dict[str, Any] = {}
    figures_blocks: Dict[str, Any] = {}

    #配置常量：函数级
    max_chars = int(getattr(config, "SPECIAL_BLOCK_MAX_CHARS", 12000))
    sep = getattr(config, "SPECIAL_PACK_SEPARATOR", "\n\n---\n\n")


    p = page_texts or ""

    # 0) 优先用明确标签切块（更稳定，避免误切）
    tag_text = "## Text (Nexora)"
    tag_tables = "## Tables (Nexora)"
    tag_figures = "## Figures (Nexora)"
    if tag_text in p or tag_tables in p or tag_figures in p:
        def _section(s: str, start_tag: str, end_tags: List[str]) -> str:
            if start_tag not in s:
                return ""
            after = s.split(start_tag, 1)[1]
            cut = len(after)
            for et in end_tags:
                idx = after.find(et)
                if idx != -1 and idx < cut:
                    cut = idx
            return after[:cut].strip()

        text_only = _section(p, tag_text, [tag_tables, tag_figures])
        tables_only = _section(p, tag_tables, [tag_figures])
        figures_only = _section(p, tag_figures, [])

        text_blocks = {"type": "text", "caption": "", "text": text_only}
        if tables_only:
            tables_blocks = {"type": "table", "caption": "", "text": tables_only}
        if figures_only:
            figures_blocks = {"type": "figure", "caption": "", "text": figures_only}
        return text_blocks, tables_blocks, figures_blocks

    # 1) 表格块（同页打包）
    tables = extract_table_blocks(p)
    if tables:
        caption = ""
        for ln in p.splitlines():
            if any(k.lower() in ln.lower() for k in config.TABLE_KEYWORDS):
                caption = ln.strip()
                break

        packed_tables = pack_special_items(tables, max_chars=max_chars, sep=sep)
        for idx, pt in enumerate(packed_tables):
            cap = caption
            if idx > 0 and cap:
                cap = f"{cap} (part {idx+1})"
            tables_blocks={"type": "table", "caption": cap, "text": pt}

    # 2) 图形/图表块（标记型）
    cap_lines = []
    for ln in p.splitlines():
        if any(k.lower() in ln.lower() for k in config.FIGURE_KEYWORDS):
            cap_lines.append(ln.strip())

    if cap_lines:
        caption = cap_lines[0]
        context = caption + "\n" + p
        if len(context) > max_chars:
            context = context[:max_chars] + "\n...(truncated)"
        figures_blocks= {"type": "figure", "caption": caption, "text": context}

    # 3) 文本块（无论是否有 table/figure，都必须保底入库）
    text_only = p
    if "## Tables (Nexora)" in text_only:
        text_only = text_only.split("## Tables (Nexora)", 1)[0]
    text_blocks = {"type": "text", "caption": "", "text": text_only}

    return text_blocks, tables_blocks, figures_blocks


def _tables_from_block(block_text: Optional[str]) -> List[Dict[str, str]]:
    if not block_text:
        return []
    return [{"caption": "", "markdown": block_text.strip()}]


def _figures_from_block(block_text: Optional[str]) -> List[Dict[str, str]]:
    if not block_text:
        return []
    return [{"caption": "", "description": "", "data_table_markdown": block_text.strip()}]


def vlm_tables_to_markdown(tables: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for t in tables or []:
        if not isinstance(t, dict):
            continue
        caption = str(t.get("caption") or "").strip()
        md = str(t.get("markdown") or "").strip()
        block = "\n".join([p for p in [caption, md] if p]).strip()
        if block:
            parts.append(block)
    return "\n\n".join(parts).strip()


def vlm_figures_to_markdown(figures: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for f in figures or []:
        if not isinstance(f, dict):
            continue
        caption = str(f.get("caption") or "").strip()
        desc = str(f.get("description") or "").strip()
        data = str(f.get("data_table_markdown") or "").strip()
        block = "\n".join([p for p in [caption, desc, data] if p]).strip()
        if block:
            parts.append(block)
    return "\n\n".join(parts).strip()

def extract_table_blocks(page_text: str) -> List[str]:
    """从页面文本中提取 markdown 表格块（基于 |---| 和连续 | 行）。"""
    lines = page_text.splitlines()
    blocks: List[str] = []
    buf: List[str] = []
    in_table = False

    def flush():
        nonlocal buf, in_table
        if buf:
            blocks.append("\n".join(buf).strip())
        buf = []
        in_table = False

    for ln in lines:
        s = ln.strip()
        is_md_row = s.startswith("|") and s.endswith("|")
        is_sep = ("|---" in s) or ("| ---" in s)
        if is_md_row or is_sep:
            buf.append(ln)
            in_table = True
        else:
            if in_table:
                flush()
    if in_table:
        flush()

    # 兜底：如果页面里包含你拼的 “【Tables (Markdown)】”段落，优先把其后内容当作表格候选
    if "## Table (Markdown)" in page_text and not blocks:
        tail = page_text.split("## Table (Markdown)", 1)[1].strip()
        if tail:
            blocks.append(tail[:8000])
    return [b for b in blocks if b and len(b) > 20]




def pack_special_items(items: List[str], max_chars: int, sep: str = "\n\n---\n\n") -> List[str]:
    """
    把多个表格/图片说明合并成更少的块，每块不超过 max_chars。
    items: 每个表格块（或每个 figure 块文本）
    """
    packed: List[str] = []
    buf = ""

    for it in items:
        it = (it or "").strip()
        if not it:
            continue

        cand = it if not buf else (buf + sep + it)
        if len(cand) <= max_chars:
            buf = cand
        else:
            if buf:
                packed.append(buf)
                buf = it
            else:
                # 单个就超长：截断避免无限循环
                packed.append(it[:max_chars] + "\n...(truncated)")
                buf = ""

    if buf:
        packed.append(buf)
    return packed
