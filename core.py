"""Nexora 核心逻辑（与 UI/接口解耦）

目标：
- 让 Gradio UI 与 FastAPI API 复用同一套核心能力
- 核心模块不依赖 gradio
"""

from __future__ import annotations
import config
import json
import asyncio
import fitz  # PyMuPDF
import re, uuid, os, requests, tempfile
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path
from functions.vlmfunc import call_vllm_chat
from functions.rag_pg_store import RagPgStore, PgConfig, RlsContext
from graphfunc.graph_pg_store import GraphPgStore, PgConfig as GraphPgConfig, RlsContext as GraphRlsContext
from functions.object_store import ObjectStore
from functions.rerank_client import ensure_rerank_service, rerank_hits
from functions.chunkfunc import (
    build_chunks_with_meta,
    build_chunks_with_meta1,
    merge_hits_by_page_or_caption,
    sliding_chunk_text,
)

# =========================
# OCR 校对台：MinIO Key 约定 + Chunk 按页过滤
# =========================

_META_PAGE_RE = re.compile(r"\[\[META[^\]]*?\bpage\s*=\s*(\d+)", re.IGNORECASE)


def parse_page_from_meta(chunk_text: str) -> Optional[int]:
    """从 chunk_text 的 META 头中解析 page=N；解析不到返回 None。"""
    if not chunk_text:
        return None
    first_line = chunk_text.splitlines()[0] if chunk_text else ""
    m = _META_PAGE_RE.search(first_line)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def build_ocr_object_keys(rag_app_id: str, doc_dir: str, page_no: int) -> Dict[str, str]:
    """按你当前 MinIO 目录规则，计算某页 OCR 产物的对象 Key。"""
    rag_app_id = (rag_app_id or config.RAG_APP_ID).strip()
    doc_dir = (doc_dir or "").strip()
    page_no = int(page_no)
    if not rag_app_id or not doc_dir or page_no <= 0:
        raise ValueError("rag_app_id/doc_dir/page_no invalid")

    base = f"{rag_app_id}/{doc_dir}/ocr"
    file_base = f"{doc_dir}_page{page_no}"
    return {
        "img": f"{base}/img/{file_base}.png",
        "text": f"{base}/text/{file_base}.txt",
        "tab": f"{base}/tab/{file_base}table.txt",
        "figure": f"{base}/figure/{file_base}figure.txt",
        "log": f"{base}/log/{file_base}log.txt",
    }


def build_ocr_trans_object_keys(rag_app_id: str, doc_dir: str, page_no: int) -> Dict[str, str]:
    """按翻译产物目录规则，计算某页翻译文本的对象 Key（最近一次写入）。"""
    rag_app_id = (rag_app_id or config.RAG_APP_ID).strip()
    doc_dir = (doc_dir or "").strip()
    page_no = int(page_no)
    if not rag_app_id or not doc_dir or page_no <= 0:
        raise ValueError("rag_app_id/doc_dir/page_no invalid")

    base = f"{rag_app_id}/{doc_dir}/ocr"
    file_base = f"{doc_dir}_page{page_no}"
    return {
        "text_trans": f"{base}/text_trans/{file_base}.txt",
        "table_trans": f"{base}/table_trans/{file_base}table.txt",
        "figure_trans": f"{base}/figure_trans/{file_base}figure.txt",
    }


def load_ocr_page_assets(
    rag_app_id: str,
    doc_dir: str,
    page_no: int,
) -> Dict[str, Any]:
    """从 MinIO 读取某页的 png/text/table/log。读取不到则返回空。"""
    keys = build_ocr_object_keys(rag_app_id, doc_dir, page_no)

    def _safe_get_bytes(k: str) -> bytes:
        try:
            return obj_store.get_bytes(k)
        except Exception:
            return b""

    def _safe_get_text(k: str) -> str:
        try:
            return obj_store.get_text(k)
        except Exception:
            return ""

    return {
        "object_keys": keys,
        "png_bytes": _safe_get_bytes(keys["img"]),
        "ocr_text": _safe_get_text(keys["text"]),
        "ocr_table": _safe_get_text(keys["tab"]),
        "ocr_figure": _safe_get_text(keys.get("figure", "")) if keys.get("figure") else "",
        "ocr_log": _safe_get_text(keys["log"]),
    }


def load_ocr_page_trans_assets(
    rag_app_id: str,
    doc_dir: str,
    page_no: int,
) -> Dict[str, Any]:
    """从 MinIO 读取某页翻译文本（最近一次写入）。"""
    keys = build_ocr_trans_object_keys(rag_app_id, doc_dir, page_no)

    def _safe_get_text(k: str) -> str:
        try:
            return obj_store.get_text(k)
        except Exception:
            return ""

    return {
        "object_keys": keys,
        "text_trans": _safe_get_text(keys["text_trans"]),
        "table_trans": _safe_get_text(keys["table_trans"]),
        "figure_trans": _safe_get_text(keys["figure_trans"]),
    }


def load_page_chunks_for_review(
    rag_app_id: str,
    rag_clearance: int,
    doc_dir: str,
    page_no: int,
) -> Dict[str, Any]:
    """为“OCR 校对台”加载指定文档指定页的 chunks（按 META page 过滤）。"""
    rag_app_id = (rag_app_id or config.RAG_APP_ID)
    rag_clearance = int(rag_clearance or config.RAG_CLEARANCE)
    doc_dir = (doc_dir or "").strip()
    page_no = int(page_no)

    ctx = RlsContext(app_id=rag_app_id, clearance=rag_clearance, request_id=str(uuid.uuid4()))

    # 1) doc_dir -> doc_id（在 docs.title/source_uri 里模糊匹配）
    docs = pg_store.find_docs_by_doc_dir(ctx=ctx, doc_dir=doc_dir, limit=10)
    if not docs:
        return {"ok": False, "error": f"doc not found by doc_dir={doc_dir}", "chunks": []}

    # 默认取最相关的第一个（你也可以在 UI 里让用户选择）
    doc_id = docs[0]["doc_id"]
    title = docs[0].get("title")
    source_uri = docs[0].get("source_uri")

    # 2) latest version
    version_id = pg_store.get_latest_version_id(ctx=ctx, doc_id=doc_id)
    if not version_id:
        return {"ok": False, "error": f"no version for doc_id={doc_id}", "chunks": []}

    # 3) 拉所有 chunks，再按 page 过滤
    all_chunks = pg_store.list_chunks(ctx=ctx, doc_id=doc_id, version_id=version_id)
    page_chunks: List[Dict[str, Any]] = []
    for ch in all_chunks:
        p = parse_page_from_meta(ch.get("chunk_text") or "")
        if p == page_no:
            ch2 = dict(ch)
            ch2["page_no"] = p
            page_chunks.append(ch2)

    return {
        "ok": True,
        "doc_id": doc_id,
        "version_id": version_id,
        "title": title,
        "source_uri": source_uri,
        "page_no": page_no,
        "chunks": page_chunks,
        "candidates": docs,  # 如果你未来想让 UI 选择 doc，这里给到候选
    }


def save_reviewed_chunk(
    rag_app_id: str,
    rag_clearance: int,
    chunk_id: str,
    new_chunk_text: str,
    reembed: bool = False,
) -> Dict[str, Any]:
    """把人工修改后的 chunk 写回 PG；可选重算向量。"""
    rag_app_id = (rag_app_id or config.RAG_APP_ID)
    rag_clearance = int(rag_clearance or config.RAG_CLEARANCE)
    ctx = RlsContext(app_id=rag_app_id, clearance=rag_clearance, request_id=str(uuid.uuid4()))

    try:
        emb = embed_text(new_chunk_text) if reembed else None
        pg_store.update_chunk_text(ctx=ctx, chunk_id=chunk_id, new_chunk_text=new_chunk_text, new_embedding=emb)
        return {"ok": True, "chunk_id": chunk_id, "reembed": bool(reembed)}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "chunk_id": chunk_id}

# =========================
# 基础工具
# =========================
def normalize_text(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def ensure_system_message(messages: List[Dict[str, Any]]) -> None:
    if not messages or messages[0].get("role") != "system":
        messages.insert(0, {"role": "system", "content": config.SYSTEM_PROMPT})

_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def _detect_query_language(text: str) -> str:
    """Return 'ar' for Arabic, 'en' for English, else 'auto'."""
    if _ARABIC_RE.search(text or ""):
        return "ar"
    if _LATIN_RE.search(text or ""):
        return "en"
    return "auto"


def build_user_content(
    text: str,
    pdf_context: Optional[str] = None,
    lang_instruction: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """与 nexora_gr.py 一致的 user content 结构，兼容 vLLM OpenAI-style."""
    content: List[Dict[str, Any]] = []
    if pdf_context:
        content.append(
            {
                "type": "text",
                "text": "以下是从PDF中检索到的相关上下文，请优先基于此回答：\n\n" + pdf_context,
            }
        )
    if lang_instruction:
        content.append({"type": "text", "text": lang_instruction})
    content.append({"type": "text", "text": text})
    return content

def _upload_text(
    obj_store,
    object_key: str,
    text: str,
    ):
    with tempfile.NamedTemporaryFile(delete=False, mode="w", encoding="utf-8") as f:
        f.write(text)
        tmp = f.name
    try:
        obj_store.upload_file(object_key=object_key, local_path=tmp)
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass

def _upload_image(
    obj_store,
    object_key: str,
    png_bytes: bytes,
):
    """把 PNG bytes 按和 _upload_text 同样的逻辑写到对象存储。"""
    if not png_bytes:
        return
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as f:
        f.write(png_bytes)
        tmp = f.name
    try:
        obj_store.upload_file(object_key=object_key, local_path=tmp)
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


# =========================
# Embedding
# =========================
def embed_text(text: str) -> List[float]:
    """走 Ollama embedding。保持与原实现一致。"""
    import requests
    
    payload = {"model": config.EMBED_MODEL, "input": text, "dimensions": config.EMBED_DIM}
    url = f"{config.EMBED_BASE_URL.rstrip('/')}/v1/embeddings"

    r = requests.post(f"{config.EMBED_BASE_URL}/v1/embeddings", json=payload, timeout=120)
    r.raise_for_status()
    resp = r.json()
   
    
    # vLLM / OpenAI 兼容
    if "data" in resp and resp["data"]:
        emb = resp["data"][0].get("embedding")

    # Ollama
    elif "embedding" in resp:
        emb = resp["embedding"]

    else:
        raise RuntimeError(f"未知的 embedding 返回格式: {resp.keys()}")

    if not emb:
        raise RuntimeError("Embedding 返回为空")

    if len(emb) != config.EMBED_DIM:
        print(
            f"[WARN] embedding dim mismatch: got={len(emb)} "
            f"expect={config.EMBED_DIM}"
        )
    return emb


# =========================
# Graph: Entity extraction
# =========================
def _safe_json_from_text(raw: str) -> Optional[Any]:
    if not raw:
        return None
    raw = raw.strip()
    try:
        return json.loads(raw)
    except Exception:
        pass
    m = re.search(r"(\[[\s\S]*\]|\{[\s\S]*\})", raw)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


def _extract_entities_from_text(
    text: str,
    prompt_key: Optional[str] = None,
    prompt_text: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not text or not text.strip():
        return []
    if prompt_text:
        prompt = prompt_text
    else:
        key = (prompt_key or config.GRAPH_ENTITY_PROMPT_DEFAULT or "strict").strip()
        prompt = config.GRAPH_ENTITY_PROMPTS.get(key) or config.GRAPH_ENTITY_PROMPTS.get("strict", "")
    messages = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": text.strip()},
    ]
    try:
        raw = call_vllm_chat(messages, temperature=0, top_p=1)
    except Exception as e:
        print(f"[WARN] entity extract failed: {type(e).__name__}: {e}")
        return []
    data = _safe_json_from_text(raw)
    if not data:
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        etype = (item.get("type") or "").strip()
        if not name or not etype:
            continue
        aliases = item.get("aliases") or []
        if not isinstance(aliases, list):
            aliases = []
        conf = (item.get("confidence") or "medium").strip().lower()
        if conf not in ("high", "medium", "low"):
            conf = "medium"
        out.append(
            {
                "name": name,
                "type": etype,
                "aliases": aliases,
                "confidence": conf,
            }
        )
        # limit is handled in prompt; keep a soft cap to avoid runaway output
        if len(out) >= 12:
            break
    return out


def _find_entities_hybrid(
    gctx: GraphRlsContext,
    query_text: str,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    results: Dict[str, Dict[str, Any]] = {}
    matches = graph_store.find_entities_by_name_or_alias(gctx, query_text, limit=limit)
    for r in matches:
        eid = r.get("entity_id")
        if not eid:
            continue
        occ = int(r.get("occurrence_count") or 0)
        results[str(eid)] = {
            **r,
            "score": 1.0 + 0.2 * math.log(occ + 1),
        }

    if len(results) < limit:
        try:
            q_emb = embed_text(query_text)
            min_sim = getattr(config, "GRAPH_ENTITY_MIN_SIM", None)
            sem = graph_store.find_entities_by_embedding(
                gctx,
                query_embedding=q_emb,
                limit=limit,
                min_similarity=min_sim,
            )
            for r, sim in sem:
                eid = r.get("entity_id")
                if not eid or str(eid) in results:
                    continue
                occ = int(r.get("occurrence_count") or 0)
                results[str(eid)] = {
                    **r,
                    "score": 0.7 * float(sim) + 0.2 * math.log(occ + 1),
                }
        except Exception as e:
            print(f"[WARN] entity embedding recall failed: {type(e).__name__}: {e}")

    return sorted(results.values(), key=lambda x: x.get("score", 0), reverse=True)[:limit]


def _build_entity_embedding_text(name: str, aliases: List[str]) -> str:
    parts = [name.strip()] if name else []
    if aliases:
        parts.extend(a.strip() for a in aliases if a and a.strip())
    return " | ".join(p for p in parts if p)



# =========================
# Stores 初始化（PG + MinIO）
# =========================
pg_store = RagPgStore(
    PgConfig(
        host=config.PG_HOST,
        port=config.PG_PORT,
        dbname=config.PG_DB,
        user=config.PG_USER,
        password=config.PG_PASSWORD,
        admin_user=getattr(config, "PG_ADMIN_USER", None),
        admin_password=getattr(config, "PG_ADMIN_PASSWORD", None),
        embed_dim=config.EMBED_DIM,
        sslmode=getattr(config, "PG_SSLMODE", "disable"),
    )
)

graph_store = GraphPgStore(
    GraphPgConfig(
        host=config.PG_HOST,
        port=config.PG_PORT,
        dbname=config.PG_DB,
        user=config.PG_USER,
        password=config.PG_PASSWORD,
        admin_user=getattr(config, "PG_ADMIN_USER", None),
        admin_password=getattr(config, "PG_ADMIN_PASSWORD", None),
        sslmode=getattr(config, "PG_SSLMODE", "disable"),
    )
)

obj_store = ObjectStore()

# rerank 服务（如果启用）
try:
    if getattr(config, "RERANK_ENABLED", False):
        ensure_rerank_service()
except Exception as e:
    print(f"[WARN] rerank service init failed: {type(e).__name__}: {e}")


# =========================
# 数据结构
# =========================
@dataclass
class AppState:
    api_messages: List[Dict[str, Any]]
    ui_messages: List[Dict[str, str]]

    @staticmethod
    def new() -> "AppState":
        return AppState(api_messages=[], ui_messages=[])


# =========================
# DB 元信息查询
# =========================
def fetch_doc_sources(ctx: RlsContext, doc_ids: List[str]) -> Dict[str, Dict[str, str]]:
    """返回 {doc_id: {title:..., source_uri:...}}"""
    if not doc_ids:
        return {}
    uniq = list(dict.fromkeys([str(x) for x in doc_ids if x]))
    out: Dict[str, Dict[str, str]] = {}
    if not uniq:
        return out
    try:
        with pg_store._connect() as conn:  # noqa: SLF001（内部方法，便于快速集成）
            pg_store._set_rls(conn, ctx)   # noqa: SLF001
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT doc_id::text, title, source_uri
                    FROM docs
                    WHERE doc_id = ANY(%s)
                    """,
                    (uniq,),
                )
                for doc_id, title, source_uri in cur.fetchall():
                    out[str(doc_id)] = {
                        "title": (title or "").strip(),
                        "source_uri": (source_uri or "").strip(),
                    }
    except Exception as e:
        print(f"[WARN] fetch_doc_sources failed: {type(e).__name__}: {e}")
    return out


def fetch_doc_meta(ctx: RlsContext, doc_id: str) -> Optional[Dict[str, Any]]:
    """返回单个 doc 元信息（受 RLS 约束）。"""
    doc_id = (doc_id or "").strip()
    if not doc_id:
        return None
    try:
        with pg_store._connect() as conn:  # noqa: SLF001
            pg_store._set_rls(conn, ctx)   # noqa: SLF001
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT doc_id::text, title, source_uri, classification
                    FROM docs
                    WHERE doc_id = %s;
                    """,
                    (doc_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                doc_id, title, source_uri, classification = row
                return {
                    "doc_id": str(doc_id),
                    "title": (title or "").strip(),
                    "source_uri": (source_uri or "").strip(),
                    "classification": int(classification) if classification is not None else None,
                }
    except Exception as e:
        print(f"[WARN] fetch_doc_meta failed: {type(e).__name__}: {e}")
        return None


def _parse_doc_dir_from_source_uri(source_uri: str, rag_app_id: str) -> Optional[str]:
    """Best-effort parse doc_dir from source_uri/object key."""
    source_uri = (source_uri or "").strip()
    rag_app_id = (rag_app_id or "").strip()
    if not source_uri or not rag_app_id:
        return None

    if source_uri.startswith("s3://"):
        tail = source_uri[5:]  # bucket/key
        parts = tail.split("/", 1)
        if len(parts) != 2:
            return None
        key = parts[1]
        segs = [s for s in key.split("/") if s]
        if len(segs) < 2:
            return None
        if segs[0] != rag_app_id:
            return None
        return segs[1]

    anchor = f"/{rag_app_id}/"
    if anchor in source_uri:
        tail = source_uri.split(anchor, 1)[1]
        segs = [s for s in tail.split("/") if s]
        return segs[0] if segs else None
    return None




# =========================
# 核心能力 0：多格式文件入库
# =========================
def ingest_file(
    file_path: str,
    parse_modes: List[str],
    chunk_mode: str,
    ocr_lang_choice: str,
    rag_app_id: str,
    rag_clearance: int,
    graph_enabled: Optional[bool] = None,
    graph_prompt_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    统一入口：支持 PDF / DOCX / XLSX / TXT / JPG(JPEG/PNG)
    现在先做 Step1.1：分发 + 占位解析器调用（Step1.2 再实现解析器）
    返回 dict，包含 doc_dir、pages_count 等关键信息。
    """
    p = Path(file_path)
    if not p.exists():
        return {"ok": False, "error": f"file not found: {file_path}"}

    ext = p.suffix.lower().lstrip(".")
    # 兼容 jpeg
    if ext == "jpeg":
        ext = "jpg"
        
    modes = set(parse_modes or [])
    use_ocr = "OCR" in modes
    use_vlm = "VLM" in modes

    # ✅ Step1.1：按后缀分发（解析器 Step1.2 再补）
    if ext == "pdf":
        # 你现有的 PDF 入库流程（保持不动）
        from functions.pdffunc import extract_pdf_multimodal_rag
        pages = extract_pdf_multimodal_rag(
            pdf_path=file_path,
            use_ocr=use_ocr,
            use_vlm=use_vlm,
            ocr_lang=ocr_lang_choice
            )
        return ingest_pages_common(
            file_path=file_path,
            pages=pages,
            graph_enabled=graph_enabled,
            graph_prompt_key=graph_prompt_key,
            rag_app_id=rag_app_id,
            rag_clearance=rag_clearance,
            parser_ver=parse_modes,
            chunk_mode=chunk_mode,
        )
  
    elif ext in ("jpg", "png","jpeg"):
        from functions.imgfunc import parse_image_to_pages  # noqa
        pages = parse_image_to_pages(
            file_path=file_path,
            use_ocr=use_ocr,
            use_vlm=use_vlm,
            ocr_lang=ocr_lang_choice           
            )
        return ingest_pages_common(
            file_path=file_path,
            pages=pages,
            graph_enabled=graph_enabled,
            graph_prompt_key=graph_prompt_key,
            rag_app_id=rag_app_id,
            rag_clearance=rag_clearance,
            parser_ver=parse_modes,
            chunk_mode=chunk_mode,
        )
      
    elif ext == "txt":
        # Step1.2 会实现：parse_txt_to_pages
        from functions.txtfunc import parse_txt_to_pages
        pages = parse_txt_to_pages(file_path)
        return ingest_pages_common(
            file_path=file_path,
            pages=pages,
            graph_enabled=graph_enabled,
            graph_prompt_key=graph_prompt_key,
            rag_app_id=rag_app_id,
            rag_clearance=rag_clearance,
            parser_ver=["txt"],
            chunk_mode=chunk_mode
        )

    elif ext == "docx":
        from functions.docxfunc import parse_docx_to_pages  
        pages = parse_docx_to_pages(
            file_path = file_path,
            soft_limit_chars= config.SOFT_LIMIT_CHARS,
            hard_limit_chars= config.HARD_LIMIT_CHARS,
            new_page_on_heading_leq= config.NEW_PAGE_ON_HEADING_LEQ,
            max_heading_level_in_path = config.MAX_HEADING_LEVEL_IN_PATH
        )
        return ingest_pages_common(
            file_path=file_path,
            pages=pages,
            graph_enabled=graph_enabled,
            graph_prompt_key=graph_prompt_key,
            rag_app_id=rag_app_id,
            rag_clearance=rag_clearance,
            parser_ver=["docx"],
            chunk_mode=chunk_mode
        )

    elif ext in ("xlsx", "xls"):
        from functions.xlsxfunc import parse_xlsx_to_pages  
        pages = parse_xlsx_to_pages(file_path)
        return ingest_pages_common(
            file_path=file_path,
            pages=pages,
            graph_enabled=graph_enabled,
            graph_prompt_key=graph_prompt_key,
            rag_app_id=rag_app_id,
            rag_clearance=rag_clearance,
            parser_ver=["xlsx"],
            chunk_mode=chunk_mode
        )

    else:
        return {"ok": False, "error": f"unsupported file type: .{ext}"}

def ingest_pages_common(
    file_path: str,
    pages: List[Dict[str, Any]],
    graph_enabled: Optional[bool],
    graph_prompt_key: Optional[str],
    rag_app_id: str,
    rag_clearance: int,
    parser_ver: List[str],
    chunk_mode: str,
    classification: int = config.DEFAULT_CLASSIFICATION,
) -> Dict[str, Any]:
    """
    通用 pages 入库（TXT / DOCX / XLSX / JPG 共用）
    """
    p = Path(file_path)
    doc_uuid = str(uuid.uuid4())
    doc_dir = f"{p.stem}_{doc_uuid}"

    # 1) 上传原始文件
    try:
        object_key = f"{rag_app_id}/{doc_dir}/source/{doc_dir}{p.suffix}"
        obj_store.upload_file(object_key, file_path)
        source_uri = obj_store.get_uri(object_key)
    except Exception:
        source_uri = file_path  # 兜底

    # 2) 写每页产物（沿用 ocr 目录，哪怕不是 OCR）
    page_texts: List[str] = []

    for page in pages:
        page_no = int(page.get("page_no", 0))
        if page_no <= 0:
            continue

        text = (page.get("text") or "").strip()
        tables = (page.get("tables") or "").strip()
        figures = (page.get("figures") or "").strip()
        png_bytes = page.get("png_bytes")

        base = f"{rag_app_id}/{doc_dir}/ocr"
        if text:
            _upload_text(obj_store, f"{base}/text/{doc_dir}_page{page_no}.txt", text)
        if tables:
            _upload_text(obj_store, f"{base}/tab/{doc_dir}_page{page_no}table.txt", tables)
        if figures:
            _upload_text(obj_store, f"{base}/figure/{doc_dir}_page{page_no}figure.txt", figures)
        if png_bytes:
            _upload_image(obj_store, f"{base}/img/{doc_dir}_page{page_no}.png", png_bytes)

        parts = []
        if text:
            parts.append(text)
        if tables:
            parts.append("[tables]\n" + tables)
        if figures:
            parts.append("[figures]\n" + figures)

        page_texts.append(f"[page {page_no}]\n" + "\n\n".join(parts))

    full_text = "\n\n".join(page_texts).strip()

    # 3) 写整本文本
    if full_text:
        _upload_text(obj_store, f"{rag_app_id}/{doc_dir}/text/{doc_dir}.txt", full_text)

    # 4) chunk + embedding（复用你已有逻辑）
    mode = (chunk_mode or "").strip().lower()
    if mode in ("std. chunk", "std chunk", "std"):
        chunks = sliding_chunk_text(full_text, config.PDF_CHUNK_SIZE, config.PDF_CHUNK_OVERLAP)
    else:
        chunks = build_chunks_with_meta1(
            pages = pages,
            chunk_size=config.PDF_CHUNK_SIZE,
            overlap=config.PDF_CHUNK_OVERLAP,
        )

    ctx = RlsContext(
        app_id=rag_app_id,
        clearance=rag_clearance,
        request_id=str(uuid.uuid4()),
    )

    chunk_rows = []
    for i, ch in enumerate(chunks):
        chunk_rows.append((i, ch, embed_text(ch)))
     
    parserlist = ""
    parserlist = "|".join(parser_ver)

    try:
        doc_id = pg_store.ingest_pdf(
            ctx=ctx,
            title=p.name,
            source_uri=source_uri,
            classification=classification,
            parser_ver=parserlist,
            embed_model=config.EMBED_MODEL,
            chunks=chunk_rows,
        )  
        db_msg = f"Written in DB, doc_id={doc_id}"
    except Exception as e:
        db_msg = f"Fail to write in DB：{type(e).__name__}: {e}"
        doc_id = None

    # 5) Graph build (optional)
    if doc_id and graph_enabled:
        try:
            gctx = GraphRlsContext(app_id=rag_app_id, clearance=rag_clearance, request_id=str(uuid.uuid4()))
            version_id = pg_store.get_latest_version_id(ctx=ctx, doc_id=str(doc_id))
            if version_id:
                chunks_in_db = pg_store.list_chunks(ctx=ctx, doc_id=str(doc_id), version_id=str(version_id))
                for ch in chunks_in_db:
                    chunk_text = (ch.get("chunk_text") or "").strip()
                    chunk_id = ch.get("chunk_id")
                    if not chunk_text or not chunk_id:
                        continue
                    entities = _extract_entities_from_text(chunk_text, prompt_key=graph_prompt_key)
                    entity_ids: List[str] = []
                    for ent in entities:
                        ent_aliases = ent.get("aliases") or []
                        ent_text = _build_entity_embedding_text(ent.get("name") or "", ent_aliases)
                        ent_emb = None
                        if ent_text:
                            try:
                                ent_emb = embed_text(ent_text)
                            except Exception as e:
                                print(f"[WARN] entity embedding failed: {type(e).__name__}: {e}")
                        row = graph_store.upsert_entity(
                            ctx=gctx,
                            name=ent["name"],
                            entity_type=ent["type"],
                            aliases=ent_aliases,
                            confidence=ent.get("confidence") or "medium",
                            classification=classification,
                            embedding=ent_emb,
                        )
                        entity_id = row.get("entity_id")
                        if entity_id:
                            entity_ids.append(entity_id)
                            graph_store.upsert_entity_chunk(
                                ctx=gctx,
                                entity_id=str(entity_id),
                                chunk_id=str(chunk_id),
                                mention_count=1,
                                confidence=ent.get("confidence") or "medium",
                                classification=classification,
                            )
                    # co-occurs edges (bidirectional)
                    for i in range(len(entity_ids)):
                        for j in range(i + 1, len(entity_ids)):
                            a = entity_ids[i]
                            b = entity_ids[j]
                            graph_store.upsert_edge(
                                ctx=gctx,
                                src_entity_id=str(a),
                                dst_entity_id=str(b),
                                edge_type="co_occurs",
                                weight=0.5,
                                evidence_chunk_ids=[str(chunk_id)],
                                classification=classification,
                            )
                            graph_store.upsert_edge(
                                ctx=gctx,
                                src_entity_id=str(b),
                                dst_entity_id=str(a),
                                edge_type="co_occurs",
                                weight=0.5,
                                evidence_chunk_ids=[str(chunk_id)],
                                classification=classification,
                            )
        except Exception as e:
            print(f"[WARN] graph build failed: {type(e).__name__}: {e}")

    return {
        "ok": True,
        "doc_name": doc_dir,
        "db_msg": db_msg,
        "source_uri": source_uri,
        "pages": len(page_texts),
        "pdf_chars": len(full_text or ""),
        "chunks": len(chunk_rows),
    }


# =========================
# 核心能力 2：对话（检索 + LLM）
# =========================
def chat_send(
    state: AppState,
    user_text: str,
    graph_enabled: Optional[bool] = None,
    graph_prompt_key: Optional[str] = None,
    rag_app_id: Optional[str] = None,
    rag_clearance: Optional[int] = None,
) -> Dict[str, Any]:
    if state is None:
        state = AppState.new()

    user_text = (user_text or "").strip()
    if not user_text:
        return {"ok": False, "error": "text is empty", "state": state, "answer": ""}

    rag_app_id = (rag_app_id or config.RAG_APP_ID)
    rag_clearance = int(rag_clearance or config.RAG_CLEARANCE)
    graph_enabled = config.GRAPH_ENABLED if graph_enabled is None else bool(graph_enabled)
    ctx = RlsContext(app_id=rag_app_id, clearance=rag_clearance, request_id=str(uuid.uuid4()))

    # 1) 向量检索
    pdf_context: Optional[str] = None
    hits: List[Dict[str, Any]] = []
    recall_k = int(getattr(config, "RERANK_CANDIDATES", config.PDF_TOP_K_CHUNKS))
    vector_k = int(getattr(config, "VECTOR_CHUNK_CANDIDATES", recall_k))
    graph_k = int(getattr(config, "GRAPH_CHUNK_CANDIDATES", 0))

    try:
        query_text = user_text
        graph_hits: List[Dict[str, Any]] = []
        if graph_enabled:
            gctx = GraphRlsContext(app_id=rag_app_id, clearance=rag_clearance, request_id=str(uuid.uuid4()))
            q_entities = _extract_entities_from_text(user_text, prompt_key=graph_prompt_key)
            seed_ids: List[str] = []
            seed_names: List[str] = []
            for ent in q_entities:
                matches = _find_entities_hybrid(gctx, ent["name"], limit=3)
                for m in matches:
                    mid = m.get("entity_id")
                    if mid:
                        seed_ids.append(str(mid))
                        seed_names.append(m.get("name") or "")
            neighbor_names: List[str] = []
            if seed_ids:
                neighbors = graph_store.get_neighbor_entities(gctx, seed_ids, limit=10)
                for n in neighbors:
                    nm = (n.get("dst_name") or "").strip()
                    if nm:
                        neighbor_names.append(nm)
            extra_terms = " ".join(dict.fromkeys(seed_names + neighbor_names))
            if extra_terms:
                query_text = f"{user_text}\n\n{extra_terms}"
        
            if graph_k > 0 and seed_ids:
                graph_chunk_ids = graph_store.list_chunk_ids_by_entities(gctx, seed_ids, limit=graph_k)
                if graph_chunk_ids:
                    graph_hits = pg_store.get_chunks_by_ids(ctx, graph_chunk_ids)

        vector_hits = pg_store.search_chunks(
            ctx=ctx,
            query_text=query_text,
            query_embedding=embed_text(query_text),
            top_k=vector_k,
            return_with_scores=False,
        )

        hits = []
        seen = set()
        for h in (graph_hits + vector_hits):
            cid = h.get("chunk_id")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            hits.append(h)

        if recall_k and len(hits) > recall_k:
            hits = hits[:recall_k]

        hits = merge_hits_by_page_or_caption(hits)
        if getattr(config, "RERANK_ENABLED", False) and hits:
            hits = rerank_hits(user_text, hits, top_k=int(config.PDF_TOP_K_CHUNKS))

        if hits:
            context = "\n\n".join(h.get("chunk_text", "") for h in hits if h.get("chunk_text"))
            if len(context) > config.MAX_PDF_CONTEXT_CHARS:
                context = context[: config.MAX_PDF_CONTEXT_CHARS] + "\n...(截断)"
            pdf_context = context
    except Exception as e:
        pdf_context = f"(向量库检索失败：{type(e).__name__}: {e})"
        hits = []

    # 2) sources
    sources: List[Dict[str, str]] = []
    if hits:
        doc_ids:list[str] = []
        for h in hits:
            did = h.get("doc_id")
            if isinstance(did, str) and did:
                doc_ids.append(did)
        doc_meta = fetch_doc_sources(ctx=ctx, doc_ids=doc_ids)
        seen = set()
        for h in hits:
            did = h.get("doc_id")
            if not did or did in seen:
                continue
            seen.add(did)
            m = doc_meta.get(str(did), {})
            sources.append(
                {
                    "doc_id": str(did),
                    "title": (m.get("title") or "").strip(),
                    "source_uri": (m.get("source_uri") or "").strip(),
                }
            )

    # 3) LLM
    lang = _detect_query_language(user_text)
    if lang == "ar":
        lang_instruction = "Please answer in Arabic and use right-to-left writing."
    elif lang == "en":
        lang_instruction = "Please answer in English."
    else:
        lang_instruction = None

    ensure_system_message(state.api_messages)
    state.api_messages.append(
        {
            "role": "user",
            "content": build_user_content(user_text, pdf_context, lang_instruction=lang_instruction),
        }
    )

    try:
        assistant_text = call_vllm_chat(state.api_messages)
    except Exception as e:
        assistant_text = f"调用模型失败：{type(e).__name__}: {e}"

    state.api_messages.append({"role": "assistant", "content": assistant_text})
    state.ui_messages.append({"role": "user", "content": user_text})
    state.ui_messages.append({"role": "assistant", "content": assistant_text})

    return {
        "ok": True,
        "state": state,
        "answer": assistant_text,
        "sources": sources,
        "hits": hits,
    }


def clear_chat_state() -> AppState:
    return AppState.new()


# =========================
# 核心能力 3：清空数据库（危险）
# =========================
def clear_db(rag_app_id: Optional[str] = None) -> Dict[str, Any]:
    rag_app_id = (rag_app_id or config.RAG_APP_ID).strip()
    if not rag_app_id:
        return {"ok": False, "error": "rag_app_id is empty"}

    msgs: List[str] = []
    try:
        n = pg_store.clear_docs_by_app(rag_app_id)
        msgs.append(f"PG cleared app_id={rag_app_id}, docs_deleted={n}")
    except Exception as e:
        return {"ok": False, "error": f"PG clear failed: {type(e).__name__}: {e}"}

    try:
        prefix = f"{rag_app_id}/"
        nobj = obj_store.clear_prefix_safe(prefix)
        msgs.append(f"MinIO cleared prefix={prefix}, objects_deleted={nobj}")
    except Exception as e:
        return {"ok": False, "error": f"MinIO clear failed: {type(e).__name__}: {e}"}

    return {"ok": True, "messages": msgs}


# =========================
# Graph 异步维护（job payload 组装 + 处理）
# =========================
def _collect_graph_delete_payload(
    ctx: RlsContext,
    gctx: GraphRlsContext,
    doc_id: str,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"doc_id": doc_id, "chunks": [], "mentions": []}
    try:
        version_id = pg_store.get_latest_version_id(ctx=ctx, doc_id=doc_id)
        if not version_id:
            return payload
        chunks = pg_store.list_chunks(ctx=ctx, doc_id=doc_id, version_id=version_id)
        chunk_ids = [c.get("chunk_id") for c in chunks if c.get("chunk_id")]
        if not chunk_ids:
            return payload
        mentions = graph_store.fetch_chunk_entities(ctx=gctx, chunk_ids=chunk_ids)
        payload["chunks"] = chunk_ids
        payload["mentions"] = mentions
        return payload
    except Exception as e:
        print(f"[WARN] collect graph payload failed: {type(e).__name__}: {e}")
        return payload


def process_graph_jobs_once(rag_app_id: str, rag_clearance: int, limit: int = 10) -> int:
    rag_app_id = (rag_app_id or config.RAG_APP_ID).strip()
    rag_clearance = int(rag_clearance or config.RAG_CLEARANCE)
    gctx = GraphRlsContext(app_id=rag_app_id, clearance=rag_clearance, request_id=str(uuid.uuid4()))
    jobs = graph_store.fetch_pending_jobs(gctx, limit=limit)
    if not jobs:
        return 0

    for job in jobs:
        job_id = job.get("job_id")
        payload = job.get("payload") or {}
        try:
            mentions = payload.get("mentions") or []
            # decrement entity occurrence
            dec_map: Dict[str, int] = {}
            chunk_entity_map: Dict[str, List[str]] = {}
            for m in mentions:
                eid = (m.get("entity_id") or "").strip()
                cid = (m.get("chunk_id") or "").strip()
                cnt = int(m.get("mention_count") or 1)
                if not eid:
                    continue
                dec_map[eid] = dec_map.get(eid, 0) + cnt
                if cid:
                    chunk_entity_map.setdefault(cid, []).append(eid)

            for eid, dec in dec_map.items():
                graph_store.decrement_entity_occurrence(gctx, entity_id=eid, dec_count=dec)

            # decrement co-occurs edge evidence
            for _, eids in chunk_entity_map.items():
                uniq = list(dict.fromkeys([x for x in eids if x]))
                for i in range(len(uniq)):
                    for j in range(i + 1, len(uniq)):
                        a = uniq[i]
                        b = uniq[j]
                        graph_store.decrement_edge_evidence(gctx, a, b, "co_occurs", dec_count=1)
                        graph_store.decrement_edge_evidence(gctx, b, a, "co_occurs", dec_count=1)

            graph_store.deactivate_entities_with_zero_occurrence(gctx, list(dec_map.keys()))
            graph_store.mark_job_done(gctx, job_id, success=True)
        except Exception as e:
            graph_store.mark_job_done(gctx, job_id, success=False, error=f"{type(e).__name__}: {e}")

    return len(jobs)


async def graph_job_worker(
    rag_app_id: str,
    rag_clearance: int,
    interval: Optional[int] = None,
) -> None:
    poll = int(interval or config.GRAPH_JOB_POLL_INTERVAL)
    while True:
        try:
            await asyncio.to_thread(process_graph_jobs_once, rag_app_id, rag_clearance)
        except Exception as e:
            print(f"[WARN] graph job worker failed: {type(e).__name__}: {e}")
        await asyncio.sleep(poll)


# =========================
# 核心能力 4：删除单文档（PG + 对象存储）
# =========================
def delete_doc(
    rag_app_id: Optional[str],
    rag_clearance: Optional[int],
    doc_id: str,
) -> Dict[str, Any]:
    rag_app_id = (rag_app_id or config.RAG_APP_ID).strip()
    rag_clearance = int(rag_clearance or config.RAG_CLEARANCE)
    doc_id = (doc_id or "").strip()

    if not rag_app_id or not doc_id:
        return {"ok": False, "error": "rag_app_id/doc_id is empty"}

    ctx = RlsContext(app_id=rag_app_id, clearance=rag_clearance, request_id=str(uuid.uuid4()))
    gctx = GraphRlsContext(app_id=rag_app_id, clearance=rag_clearance, request_id=str(uuid.uuid4()))
    meta = fetch_doc_meta(ctx, doc_id)
    if not meta:
        return {"ok": False, "error": f"doc not found: {doc_id}", "doc_id": doc_id}

    source_uri = (meta.get("source_uri") or "").strip()
    doc_dir = _parse_doc_dir_from_source_uri(source_uri, rag_app_id)

    graph_payload = _collect_graph_delete_payload(ctx, gctx, doc_id)

    nobj = 0
    if doc_dir:
        prefix = f"{rag_app_id}/{doc_dir}/"
        try:
            nobj = obj_store.clear_prefix_safe(prefix)
        except Exception as e:
            return {
                "ok": False,
                "error": f"MinIO delete failed: {type(e).__name__}: {e}",
                "doc_id": doc_id,
                "doc_dir": doc_dir,
            }

    try:
        n = pg_store.delete_doc(ctx, doc_id)
    except Exception as e:
        return {"ok": False, "error": f"PG delete failed: {type(e).__name__}: {e}", "doc_id": doc_id}

    if n <= 0:
        return {"ok": False, "error": f"doc not deleted: {doc_id}", "doc_id": doc_id}

    # enqueue graph maintenance job
    try:
        graph_store.enqueue_job(gctx, "doc_deleted", graph_payload)
    except Exception as e:
        print(f"[WARN] graph job enqueue failed: {type(e).__name__}: {e}")

    return {"ok": True, "doc_id": doc_id, "doc_dir": doc_dir, "objects_deleted": nobj}
