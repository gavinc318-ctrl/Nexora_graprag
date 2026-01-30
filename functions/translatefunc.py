from __future__ import annotations

import re
import tempfile
import uuid
from typing import Any, Dict, List, Optional, Tuple

import config
from functions.object_store import ObjectStore
from functions.rag_pg_store import RagPgStore, PgConfig, RlsContext
from functions.vlmfunc import call_vllm_chat
from functions.chunkfunc import build_chunks_with_meta1
from core import embed_text


_PAGE_RE = re.compile(r"_page(\d+)\.txt$")


def _list_page_numbers(obj_store: ObjectStore, prefix: str) -> List[int]:
    nums: List[int] = []
    for obj in obj_store.client.list_objects(obj_store.bucket, prefix=prefix, recursive=True):
        name = obj.object_name or ""
        m = _PAGE_RE.search(name)
        if m:
            try:
                nums.append(int(m.group(1)))
            except Exception:
                continue
    return sorted(set(nums))


def _upload_text(obj_store: ObjectStore, object_key: str, text: str) -> None:
    text = text or ""
    with tempfile.NamedTemporaryFile(delete=False, mode="w", encoding="utf-8") as f:
        f.write(text)
        tmp = f.name
    try:
        obj_store.upload_file(object_key=object_key, local_path=tmp, content_type="text/plain")
    finally:
        try:
            import os

            os.remove(tmp)
        except Exception:
            pass


def _translate_text(text: str, target_lang: str, preserve_markdown: bool = False) -> str:
    text = (text or "").strip()
    if not text:
        return ""

    lang_map = {"ar": "Arabic", "ch": "Chinese", "en": "English"}
    lang = lang_map.get(target_lang, target_lang)

    extra = ""
    if preserve_markdown:
        extra = "Preserve Markdown table syntax exactly. Only translate cell text."

    system = (
        "You are a professional translator. Preserve the original structure and order. "
        "Do not add or remove information. Do not summarize. "
        + extra
    ).strip()

    user = (
        f"Translate the following text into {lang}. Keep paragraphs and line breaks:\n<<<\n"
        f"{text}\n>>>"
    )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    return (call_vllm_chat(messages) or "").strip()


def translate_doc_pages(
    rag_app_id: str,
    doc_dir: str,
    target_lang: str,
    rag_clearance: Optional[int] = None,
) -> Dict[str, Any]:
    rag_app_id = (rag_app_id or config.RAG_APP_ID).strip()
    doc_dir = (doc_dir or "").strip()
    target_lang = (target_lang or "").strip().lower()
    rag_clearance = int(rag_clearance or config.RAG_CLEARANCE)

    if not rag_app_id or not doc_dir:
        return {"ok": False, "error": "rag_app_id/doc_dir is empty"}
    if target_lang not in ("ar", "ch", "en"):
        return {"ok": False, "error": f"target_lang invalid: {target_lang}"}

    obj_store = ObjectStore()

    # 1) determine pages from ocr/text
    text_prefix = f"{rag_app_id}/{doc_dir}/ocr/text/"
    page_nos = _list_page_numbers(obj_store, text_prefix)
    if not page_nos:
        return {"ok": False, "error": f"no pages found under {text_prefix}"}

    pages_trans: List[Dict[str, Any]] = []
    errors: List[str] = []

    for page_no in page_nos:
        file_base = f"{doc_dir}_page{page_no}"
        base = f"{rag_app_id}/{doc_dir}/ocr"
        text_key = f"{base}/text/{file_base}.txt"
        table_key = f"{base}/tab/{file_base}table.txt"
        figure_key = f"{base}/figure/{file_base}figure.txt"

        text = ""
        tables = ""
        figures = ""
        try:
            if obj_store.exists(text_key):
                text = obj_store.get_text(text_key)
            if obj_store.exists(table_key):
                tables = obj_store.get_text(table_key)
            if obj_store.exists(figure_key):
                figures = obj_store.get_text(figure_key)
        except Exception as e:
            errors.append(f"page {page_no}: load error {type(e).__name__}: {e}")
            continue

        try:
            text_trans = _translate_text(text, target_lang=target_lang, preserve_markdown=False)
            table_trans = _translate_text(tables, target_lang=target_lang, preserve_markdown=True)
            figure_trans = _translate_text(figures, target_lang=target_lang, preserve_markdown=False)
        except Exception as e:
            errors.append(f"page {page_no}: translate error {type(e).__name__}: {e}")
            continue

        # save per-page translations
        _upload_text(obj_store, f"{base}/text_trans/{file_base}.txt", text_trans)
        _upload_text(obj_store, f"{base}/table_trans/{file_base}table.txt", table_trans)
        _upload_text(obj_store, f"{base}/figure_trans/{file_base}figure.txt", figure_trans)

        pages_trans.append(
            {
                "page_no": page_no,
                "text": text_trans,
                "tables": table_trans,
                "figures": figure_trans,
                "png_bytes": None,
            }
        )

    # 2) merge full text
    merged = []
    for p in pages_trans:
        parts = []
        if p.get("text"):
            parts.append(p["text"])
        if p.get("tables"):
            parts.append("[tables]\n" + p["tables"])
        if p.get("figures"):
            parts.append("[figures]\n" + p["figures"])
        merged.append(f"[page {p['page_no']}]\n" + "\n\n".join(parts))
    full_text = "\n\n".join(merged).strip()
    _upload_text(obj_store, f"{rag_app_id}/{doc_dir}/text_trans/{doc_dir}.txt", full_text)

    # 3) write into PG (same doc_id, new version)
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
    ctx = RlsContext(app_id=rag_app_id, clearance=rag_clearance, request_id=str(uuid.uuid4()))

    docs = pg_store.find_docs_by_doc_dir(ctx=ctx, doc_dir=doc_dir, limit=1)
    if not docs:
        return {"ok": False, "error": f"doc not found in PG by doc_dir={doc_dir}"}
    doc_id = docs[0]["doc_id"]

    latest_no = pg_store.get_latest_version_no(ctx=ctx, doc_id=doc_id) or 0
    version_no = latest_no + 1

    chunks = build_chunks_with_meta1(
        pages=pages_trans,
        chunk_size=config.PDF_CHUNK_SIZE,
        overlap=config.PDF_CHUNK_OVERLAP,
    )
    chunk_rows = [(i, ch, embed_text(ch)) for i, ch in enumerate(chunks)]

    version_id = pg_store.add_version_and_chunks(
        ctx=ctx,
        doc_id=doc_id,
        version_no=version_no,
        parser_ver=f"translate/{target_lang}",
        embed_model=config.EMBED_MODEL,
        chunks=chunk_rows,
    )

    return {
        "ok": True,
        "doc_id": doc_id,
        "version_id": version_id,
        "version_no": version_no,
        "pages_total": len(page_nos),
        "pages_done": len(pages_trans),
        "errors": errors,
    }
