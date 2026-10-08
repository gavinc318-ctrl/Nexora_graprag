"""通用「Odoo 部门问答」框架。

一个 DepartmentSkill 描述一个业务部门能读什么 Odoo 模型、有哪些专属查询工具、
以及给 LLM 的范围说明/业务规则；这个模块提供所有部门共用的部分：
- 附件列表/读取工具（list_attachments / read_attachment），按部门声明的 attachment_scopes 生效
- 通用只读逃生口（odoo_query），按部门声明的 allowed_models 生效
- 系统提示词模板（语言规则、人名不翻译、富文本输出格式、附件必须真正读取等）
- 工具调用循环（odoo_ask）

新增一个部门只需要：写一份 functions/odoo_skill/departments/<dept>.py，定义一个
DepartmentSkill 并 register() 它，然后在 departments/__init__.py 里 import 一下。
不需要碰这个文件，也不需要碰 api_server.py / 前端（前端从 /v1/odoo/departments 动态取列表）。
"""

from __future__ import annotations

import base64
import datetime as _dt
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

import requests

import config
from functions.odoo_client import OdooClient, OdooError, get_client
from functions.vlmfunc import call_vllm_chat

try:
    from core import _detect_query_language, _language_instruction
except Exception:  # pragma: no cover - fallback if core import chain changes
    def _detect_query_language(text: str) -> str:
        return "auto"

    def _language_instruction(lang: str):
        return None

MAX_STEPS = 6
_ATTACHMENT_MAX_BYTES = 15 * 1024 * 1024  # 15MB，超过就不读内容，只报元数据


# ============ 部门定义的数据结构 ============

@dataclass(frozen=True)
class AttachmentScope:
    """声明"这个部门的附件挂在哪个模型的记录下"。
    name_field 是那个模型里用来模糊搜索/展示的名称字段，绝大多数模型是 'name'。
    """
    res_model: str
    label: str
    name_field: str = "name"


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: Dict[str, Any]
    impl: Callable[..., Dict[str, Any]]

    def to_spec(self) -> Dict[str, Any]:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters,
        }}


@dataclass
class DepartmentSkill:
    key: str
    display_name: str
    # 给系统提示词里 "Scope:" 一句用，用自然语言描述这个部门能查什么
    scope_description: str
    # 允许这个部门的 OdooClient 读取的模型白名单（不含 ir.attachment，框架会自动加上）
    allowed_models: Set[str]
    # 部门专属的业务规则/术语说明（比如 HR 的 wage 是月薪、用公司本位币），追加进提示词
    domain_notes: str = ""
    # 附件挂在哪些模型下（框架据此生成 list_attachments/read_attachment 工具）
    attachment_scopes: List[AttachmentScope] = field(default_factory=list)
    # 部门专属查询工具
    tools: List[ToolDef] = field(default_factory=list)


_REGISTRY: Dict[str, DepartmentSkill] = {}


def register(dept: DepartmentSkill) -> DepartmentSkill:
    _REGISTRY[dept.key] = dept
    return dept


def get_department(key: str) -> DepartmentSkill:
    dept = _REGISTRY.get(key)
    if dept is None:
        raise KeyError(f"unknown department {key!r}; available: {sorted(_REGISTRY)}")
    return dept


def list_departments() -> List[Dict[str, str]]:
    return [{"key": d.key, "display_name": d.display_name} for d in _REGISTRY.values()]


# ============ 附件：内容提取（跟部门无关，纯格式判断） ============

def _read_attachment_content(client: OdooClient, attachment_id: int, meta: Dict[str, Any],
                             max_chars: int) -> Dict[str, Any]:
    if (meta.get("file_size") or 0) > _ATTACHMENT_MAX_BYTES:
        return {"name": meta.get("name"), "mimetype": meta.get("mimetype"),
                "error": "file too large to analyze (>15MB)"}

    data_rows = client.execute_kw("ir.attachment", "read", [[int(attachment_id)]], {"fields": ["datas"]})
    b64 = (data_rows[0].get("datas") if data_rows else None) or ""
    if not b64:
        return {"name": meta.get("name"), "error": "attachment has no content (maybe stored externally)"}

    raw = base64.b64decode(b64)
    mimetype = (meta.get("mimetype") or "").lower()
    name = meta.get("name") or ""
    lower_name = name.lower()

    if mimetype.startswith("text/") or lower_name.endswith((".txt", ".csv", ".md")):
        text = raw.decode("utf-8", errors="replace")
        return {"name": name, "mimetype": mimetype, "kind": "text", "content": text[:max_chars]}

    if mimetype == "application/pdf" or lower_name.endswith(".pdf"):
        try:
            import fitz  # PyMuPDF
        except ImportError:
            return {"name": name, "mimetype": mimetype, "error": "PDF support not available on the server"}
        try:
            doc = fitz.open(stream=raw, filetype="pdf")
            text = "\n\n".join(page.get_text() for page in doc).strip()
            doc.close()
        except Exception as e:
            return {"name": name, "mimetype": mimetype, "error": f"failed to parse PDF: {type(e).__name__}: {e}"}
        if not text:
            return {"name": name, "mimetype": mimetype, "kind": "pdf",
                    "note": "no extractable text layer (likely a scanned PDF); can't read its content with this tool"}
        return {"name": name, "mimetype": mimetype, "kind": "pdf", "content": text[:max_chars]}

    if (mimetype == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            or lower_name.endswith(".docx")):
        try:
            import io
            import docx
            from docx.oxml.table import CT_Tbl
            from docx.oxml.text.paragraph import CT_P
            from docx.table import Table as _DocxTable
            from docx.text.paragraph import Paragraph as _DocxParagraph
        except ImportError:
            return {"name": name, "mimetype": mimetype, "error": "DOCX support not available on the server"}
        try:
            d = docx.Document(io.BytesIO(raw))
            parts: List[str] = []
            for child in d.element.body.iterchildren():
                if isinstance(child, CT_P):
                    p = _DocxParagraph(child, d)
                    if p.text.strip():
                        parts.append(p.text)
                elif isinstance(child, CT_Tbl):
                    t = _DocxTable(child, d)
                    for row in t.rows:
                        parts.append(" | ".join(c.text.strip() for c in row.cells))
            text = "\n".join(parts).strip()
        except Exception as e:
            return {"name": name, "mimetype": mimetype, "error": f"failed to parse DOCX: {type(e).__name__}: {e}"}
        if not text:
            return {"name": name, "mimetype": mimetype, "kind": "docx", "note": "document appears to be empty"}
        return {"name": name, "mimetype": mimetype, "kind": "docx", "content": text[:max_chars]}

    if mimetype.startswith("image/"):
        data_url = f"data:{mimetype};base64,{b64}"
        try:
            desc = call_vllm_chat([
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Describe this image in detail, and transcribe any visible text exactly as written."},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                },
            ], max_tokens=800)
        except Exception as e:
            return {"name": name, "mimetype": mimetype, "error": f"image analysis failed: {type(e).__name__}: {e}"}
        return {"name": name, "mimetype": mimetype, "kind": "image", "content": desc}

    return {"name": name, "mimetype": mimetype, "kind": "unsupported",
            "note": "this file type isn't supported for content analysis by this tool "
                    "(only text/csv/md, text-layer PDF, DOCX, and images are)"}


# ============ 附件：列表/读取工具（按部门的 attachment_scopes 生成） ============

def _make_list_attachments_tool(scopes: List[AttachmentScope]) -> ToolDef:
    def _impl(client: OdooClient, record: str = "", limit: int = 20) -> Dict[str, Any]:
        limit = max(1, min(int(limit or 20), 100))
        rows_out: List[Dict[str, Any]] = []
        matched_any_scope = False

        for scope in scopes:
            names: Dict[int, str] = {}
            if record:
                found = client.search_read(scope.res_model, [[scope.name_field, "ilike", record]],
                                           fields=[scope.name_field], limit=50)
                if not found:
                    continue
                matched_any_scope = True
                names = {r["id"]: r.get(scope.name_field) or "" for r in found}
                domain = [["res_model", "=", scope.res_model], ["res_id", "in", list(names.keys())]]
            else:
                domain = [["res_model", "=", scope.res_model]]

            rows = client.search_read(
                "ir.attachment", domain,
                fields=["name", "mimetype", "file_size", "res_id", "create_date"],
                limit=limit, order="create_date desc",
            )
            missing_ids = {r["res_id"] for r in rows if r.get("res_id") and r["res_id"] not in names}
            if missing_ids:
                extra = client.search_read(scope.res_model, [["id", "in", list(missing_ids)]],
                                           fields=[scope.name_field])
                names.update({e["id"]: e.get(scope.name_field) or "" for e in extra})
            for r in rows:
                r["linked_record"] = names.get(r.pop("res_id", None), "unknown")
                r["linked_to"] = scope.label
                rows_out.append(r)

        if record and not matched_any_scope:
            return {"count": 0, "attachments": [], "note": f"no record matching {record!r}"}
        rows_out.sort(key=lambda r: r.get("create_date") or "", reverse=True)
        rows_out = rows_out[:limit]
        return {"count": len(rows_out), "attachments": rows_out}

    labels = "/".join(s.label for s in scopes) or "records"
    return ToolDef(
        name="list_attachments",
        description=f"List files attached to a {labels} record in Odoo (documents, scans, contracts, etc.). "
                     "Omit 'record' to list recent attachments across all records in scope.",
        parameters={
            "type": "object",
            "properties": {
                "record": {"type": "string", "description": f"{labels} name (fuzzy); omit to list across all"},
                "limit": {"type": "integer", "default": 20},
            },
        },
        impl=_impl,
    )


def _make_read_attachment_tool(scopes: List[AttachmentScope]) -> ToolDef:
    allowed_models = {s.res_model for s in scopes}

    def _impl(client: OdooClient, attachment_id: int, max_chars: int = 6000) -> Dict[str, Any]:
        rows = client.search_read(
            "ir.attachment", [["id", "=", int(attachment_id)]],
            fields=["name", "mimetype", "res_model", "file_size"], limit=1,
        )
        if not rows:
            return {"error": f"attachment {attachment_id} not found"}
        meta = rows[0]
        if meta.get("res_model") not in allowed_models:
            return {"error": "this attachment is not linked to a record in this department's scope; refusing to read it"}
        return _read_attachment_content(client, attachment_id, meta, max_chars)

    return ToolDef(
        name="read_attachment",
        description="Fetch and analyze one attachment's content by its id (from list_attachments). Returns "
                     "extracted text for text/CSV/Markdown, text-layer PDF, and DOCX files, or a visual "
                     "description for images. Call this on any attachment that might be relevant BEFORE "
                     "saying its content can't be read - only scanned (image-only) PDFs and formats other "
                     "than the ones listed above are actually unsupported.",
        parameters={
            "type": "object",
            "properties": {
                "attachment_id": {"type": "integer", "description": "attachment id from list_attachments"},
                "max_chars": {"type": "integer", "default": 6000, "description": "truncate extracted text to this many characters"},
            },
            "required": ["attachment_id"],
        },
        impl=_impl,
    )


def _make_odoo_query_tool(dept: DepartmentSkill) -> ToolDef:
    def _impl(client: OdooClient, model: str, domain: Optional[List] = None,
             fields: Optional[List[str]] = None, limit: int = 30, order: str = "") -> Dict[str, Any]:
        if model == "ir.attachment":
            return {"error": "use list_attachments / read_attachment for attachments, not odoo_query "
                              "(they scope the search to this department's records)"}
        rows = client.search_read(model, domain or [], fields=fields or [], limit=limit, order=order or None)
        return {"model": model, "count": len(rows), "rows": rows}

    models_hint = ", ".join(sorted(dept.allowed_models)) or "(none)"
    return ToolDef(
        name="odoo_query",
        description=f"Generic read-only escape hatch, scoped to this department's models ({models_hint}). "
                     "Only use it when the dedicated tools above can't answer the question.",
        parameters={
            "type": "object",
            "properties": {
                "model": {"type": "string"},
                "domain": {"type": "array", "items": {}, "description": 'Odoo domain, e.g. [["amount",">",5000]]'},
                "fields": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "default": 30},
                "order": {"type": "string"},
            },
            "required": ["model"],
        },
        impl=_impl,
    )


def _department_tools(dept: DepartmentSkill) -> List[ToolDef]:
    tools = list(dept.tools)
    if dept.attachment_scopes:
        tools.append(_make_list_attachments_tool(dept.attachment_scopes))
        tools.append(_make_read_attachment_tool(dept.attachment_scopes))
    tools.append(_make_odoo_query_tool(dept))
    return tools


def _department_allowed_models(dept: DepartmentSkill) -> Set[str]:
    models = set(dept.allowed_models) | {"ir.attachment"}
    models |= {s.res_model for s in dept.attachment_scopes}
    return models


# ============ 系统提示词（部门无关的部分是模板，部门相关的部分插值进去） ============

_PROMPT_TEMPLATE = """You are the Nexora enterprise assistant. You can call tools to query the company's
Odoo system, currently limited to **{display_name}** data.

Rules:
- Read-only: you can only query, never modify any data.
- Scope: {scope_description} The "out of scope" reply ("only {display_name} data is connected right now,
  that department can't be queried yet") is ONLY for a question about a genuinely different Odoo app with
  no specific in-scope record named - e.g. something that has nothing to do with {display_name} data at
  all. You are NOT equipped to reason about whether a topic "belongs" to some other module - a company can
  track almost anything as a plain file attached to a record you already have access to, and from your
  side that looks identical to any other in-scope question.
  RULE: if the question names or clearly refers to a specific record you can look up, NEVER give the
  out-of-scope reply. Instead first try the structured tools, and if the answer isn't in them, check that
  record's attachments with list_attachments / read_attachment regardless of what noun the question uses.
  Only say the information is missing after you've actually checked (or the file type can't be read) -
  never refuse or say "no information found" without having called list_attachments first.
- Never invent a download link/URL for an attachment (e.g. no "sandbox:" links) - this integration has no
  public URL for Odoo files. When mentioning an attachment, just give its file name as plain text, not a
  Markdown link.
{domain_notes}- LANGUAGE: always answer in the SAME language the user asked the question in. If the user writes in
  English, answer in English. If in Arabic, answer in Arabic. If in Chinese, answer in Chinese. Never
  switch language because tool results or this prompt are in another language.
- NAMES: people/company names (and other proper nouns that came from Odoo as Latin-script text) must be
  copied verbatim from the tool result, in their original script. Never transliterate or translate a name
  into Arabic/Chinese characters just because the rest of the answer is in that language - e.g. keep
  "Abigail Peterson" as "Abigail Peterson" in an Arabic answer, not "أبيجيل بيترسون".
- Give concrete numbers and note that the data comes from the company Odoo system (as of today).
- If data is missing, say so plainly; never make it up.
- Attachments: records in scope can have files attached in Odoo. Use list_attachments to see what's
  attached to a record, and read_attachment to fetch and analyze one - it returns extracted text for
  text-layer PDF, DOCX, and plain text/CSV/Markdown files, or a visual description for images. Only
  scanned (image-only) PDFs and other formats come back unreadable; tell the user plainly in that case
  rather than guessing at the content. Never skip reading an attachment just because its extension is
  .docx/.pdf - those ARE supported.
  CRITICAL - decide which of these two the question is BEFORE answering:
  (a) "does X have a document/file/attachment" or "what files are attached to X" -> list_attachments
      is enough, just list them.
  (b) literally any other phrasing that names or points at ONE specific attachment or asks what it
      says/shows/contains - including "what is the <thing>", "what's on/in the <thing>", "show me the
      <thing>", "details of the <thing>", "summarize the <thing>" - treat "<thing>" as a pointer to an
      attachment, not a request for its filename. You must call list_attachments AND THEN
      read_attachment on the matching one(s) in the SAME turn, and put the actual extracted content in
      your answer.
  When in doubt, treat it as (b) and read the file - answering with just a filename is only ever
  correct for case (a). NEVER reply with just the file name plus an offer like "let me know if you
  want the details" - you already have the tool to get those details right now, so get them and
  answer fully in one turn. Only stop short of reading it if the file type genuinely can't be read
  (see above).
  Worked example: user asks "what is the ID badge for Jane Doe" -> call list_attachments(record="Jane
  Doe"), find the badge image among the results, call read_attachment(attachment_id=<that id>), then
  answer with what the badge actually shows (name, ID number, department, etc.) - NOT "the badge is
  attached as badge.png".

Rich output formatting (the frontend renders these Markdown structures, use them instead of describing
data in plain prose):
- Table: when returning a list of records or any side-by-side comparison, use a standard Markdown table
  (| header | ... |).
- Chart: when the tool results contain real numeric data with a trend, breakdown or comparison, output a
  ```echarts code block whose content is strict, valid JSON (keys and string values must be double-quoted
  like real JSON, not a bare JavaScript object literal; no comments, no trailing commas) usable directly
  as an ECharts `option` object (xAxis/yAxis/series, etc.). Example:
  ```echarts
  {{"xAxis": {{"type": "category", "data": ["A", "B"]}}, "yAxis": {{"type": "value"}}, "series": [{{"type": "bar", "data": [10, 20]}}]}}
  ```
  Only chart numbers that actually came back from a tool call in this turn - never invent data. If
  unsure, just give a table or text instead of a chart.
- Only use a Markdown image (![caption](url)) or a Markdown file link ([name.ext](url)) when a real,
  accessible URL was actually returned by a tool or given by the user - never fabricate one.

Today's date: {today}
"""


def _build_system_prompt(dept: DepartmentSkill) -> str:
    notes = dept.domain_notes.strip()
    notes_block = (notes + "\n") if notes else ""
    return _PROMPT_TEMPLATE.format(
        display_name=dept.display_name,
        scope_description=dept.scope_description,
        domain_notes=notes_block,
        today=_dt.date.today().isoformat(),
    )


# ============ OpenAI 调用 + 主入口（部门无关） ============

def _openai_chat(messages: List[Dict[str, Any]], tools: Optional[List] = None) -> Dict[str, Any]:
    url = config.OPENAI_CHAT_URL
    headers = {"Authorization": f"Bearer {config.OPENAI_API_KEY}"}
    payload: Dict[str, Any] = {
        "model": config.OPENAI_MODEL,
        "messages": messages,
        "temperature": 0.1,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    resp = requests.post(url, json=payload, headers=headers, timeout=config.TIMEOUT)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]


def odoo_ask(question: str, history: Optional[List[Dict[str, str]]] = None,
            department: str = "hr") -> Dict[str, Any]:
    """跑一轮某部门的 Odoo 问答。history 是 [{role, content}] 形式的历史（可选）。"""
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "question is empty", "answer": ""}

    try:
        dept = get_department(department)
    except KeyError as e:
        return {"ok": False, "error": str(e), "answer": ""}

    try:
        client = get_client(_department_allowed_models(dept))
    except OdooError as e:
        return {"ok": False, "error": str(e), "answer": ""}

    tools = _department_tools(dept)
    tool_impl = {t.name: t.impl for t in tools}
    tools_spec = [t.to_spec() for t in tools]

    lang = _detect_query_language(question)
    lang_note = _language_instruction(lang)
    system_content = _build_system_prompt(dept)
    if lang_note:
        system_content = f"{system_content}\n\n{lang_note}"

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_content}]
    for h in (history or []):
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"]})
    # 再在用户问题上贴一次语言指令，降低被历史/工具结果语言带偏的概率
    user_content = question if not lang_note else f"{question}\n\n[{lang_note}]"
    messages.append({"role": "user", "content": user_content})

    trace: List[Dict[str, Any]] = []

    for step in range(MAX_STEPS):
        try:
            msg = _openai_chat(messages, tools=tools_spec)
        except Exception as e:
            return {"ok": False, "error": f"LLM 调用失败: {type(e).__name__}: {e}",
                    "answer": "", "trace": trace}

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            return {"ok": True, "answer": msg.get("content") or "", "trace": trace, "steps": step}

        messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": tool_calls})

        for tc in tool_calls:
            fn = tc["function"]["name"]
            try:
                fargs = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                fargs = {}
            impl = tool_impl.get(fn)
            if impl is None:
                result = {"error": f"unknown tool {fn}"}
            else:
                try:
                    result = impl(client, **fargs)
                except OdooError as e:
                    result = {"error": str(e)}
                except TypeError as e:
                    result = {"error": f"bad arguments: {e}"}
                except Exception as e:
                    result = {"error": f"{type(e).__name__}: {e}"}
            trace.append({"tool": fn, "args": fargs, "result": result})
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(result, ensure_ascii=False, default=str),
            })

    return {"ok": True, "answer": "（已达到最大工具调用步数，请把问题拆细一点再问。）",
            "trace": trace, "steps": MAX_STEPS}


# 触发各部门模块的 register()
from functions.odoo_skill import departments as _departments  # noqa: E402,F401
