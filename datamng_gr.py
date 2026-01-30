# datamng_gr.py
from __future__ import annotations

import html
import io
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

import gradio as gr
from PIL import Image

import config
import core  # 复用 core.py 里的 load_ocr_page_assets / parse_page_from_meta / pg_store / RlsContext
from functions.translatefunc import translate_doc_pages


_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
def _is_arabic_text(text: str) -> bool:
    return bool(_ARABIC_RE.search(text or ""))


def _format_html_for_display(text: str) -> str:
    text = text or ""
    safe = html.escape(text)
    safe = safe.replace("\n", "<br>")
    if _is_arabic_text(text):
        return f'<div dir="rtl" style="text-align:right; white-space:pre-wrap">{safe}</div>'
    return f'<div dir="ltr" style="text-align:left; white-space:pre-wrap">{safe}</div>'


def _format_md_for_display(text: str) -> str:
    text = text or ""
    safe = html.escape(text)
    safe = safe.replace("\n", "<br>")
    if _is_arabic_text(text):
        return f'<div dir="rtl" style="text-align:right; white-space:pre-wrap">{safe}</div>'
    return f'<div dir="ltr" style="text-align:left; white-space:pre-wrap">{safe}</div>'


def _png_bytes_to_pil(png_bytes: bytes) -> Optional[Image.Image]:
    if not png_bytes:
        return None
    try:
        return Image.open(io.BytesIO(png_bytes)).convert("RGB")
    except Exception:
        return None


def _make_doc_choices(docs: List[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """
    docs: [{"doc_id","title","source_uri",...}, ...]
    -> [(label, doc_id), ...]
    """
    out: List[Tuple[str, str]] = []
    for d in docs or []:
        doc_id = d.get("doc_id") or ""
        title = (d.get("title") or "").strip()
        src = (d.get("source_uri") or "").strip()
        label = f"{title or '(no title)'} | {doc_id[:8]} | {src}"
        out.append((label, doc_id))
    return out


def _load_chunks_for_doc(
    app_id: str,
    clearance: int,
    doc_id: str,
    page_no: int,
) -> Tuple[Dict[str, Any], List[Tuple[str, str]], Dict[str, Any], str]:
    """
    返回：
      doc_state, chunk_choices, chunks_state, status_extra
    """
    app_id = (app_id or config.RAG_APP_ID).strip()
    clearance = int(clearance or getattr(config, "RAG_CLEARANCE", 0))
    page_no = int(page_no or 1)
    doc_id = (doc_id or "").strip()
    if not doc_id:
        return {}, [], {}, "❌ not choose doc_id"

    ctx = core.RlsContext(app_id=app_id, clearance=clearance, request_id=str(uuid.uuid4()))

    version_id = core.pg_store.get_latest_version_id(ctx=ctx, doc_id=doc_id)
    if not version_id:
        return {"doc_id": doc_id}, [], {}, f"❌ doc_id={doc_id} not find version"

    all_chunks = core.pg_store.list_chunks(ctx=ctx, doc_id=doc_id, version_id=version_id)

    # 按 META page 过滤
    page_chunks: List[Dict[str, Any]] = []
    for ch in all_chunks:
        p = core.parse_page_from_meta(ch.get("chunk_text") or "")
        if p == page_no:
            ch2 = dict(ch)
            ch2["page_no"] = p
            page_chunks.append(ch2)

    choices: List[Tuple[str, str]] = []
    mapping: Dict[str, Any] = {}
    for ch in page_chunks:
        cid = ch["chunk_id"]
        cidx = ch.get("chunk_index", -1)
        label = f"#{cidx}  {cid[:8]}"
        choices.append((label, cid))
        mapping[cid] = ch

    doc_state = {"doc_id": doc_id, "version_id": version_id, "page_no": page_no}
    status_extra = f"- doc_id={doc_id}\n- version_id={version_id}\n- chunks(current page)={len(page_chunks)}"
    return doc_state, choices, mapping, status_extra


def ui_load_page(
    app_id: str,
    clearance: int,
    doc_dir: str,
    page_no: int,
) -> Tuple[
    Any,     # image
    str,     # ocr_text
    str,     # ocr_table
    str,     # ocr_figure
    str,     # ocr_text_trans
    str,     # ocr_table_trans
    str,     # ocr_figure_trans
    str,     # ocr_log
    Any,     # doc_pick update
    Dict[str, Any],  # doc_state
    Any,     # chunk_sel update
    Dict[str, Any],  # chunks_state
    str,     # status
]:
    app_id = (app_id or config.RAG_APP_ID).strip()
    clearance = int(clearance or getattr(config, "RAG_CLEARANCE", 0))
    doc_dir = (doc_dir or "").strip()
    page_no = int(page_no or 1)

    if not doc_dir:
        return (
            None,
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            gr.update(choices=[], value=None),
            {},
            gr.update(choices=[], value=None),
            {},
            "❌ doc_dir 为空",
        )

    # 1) MinIO 拉页面产物（与 doc_id 无关）
    assets = core.load_ocr_page_assets(app_id, doc_dir, page_no)
    trans_assets = core.load_ocr_page_trans_assets(app_id, doc_dir, page_no)
    img = _png_bytes_to_pil(assets.get("png_bytes", b""))
    ocr_text = _format_md_for_display(assets.get("ocr_text", "") or "")
    ocr_table = _format_md_for_display(assets.get("ocr_table", "") or "")
    ocr_figure = _format_md_for_display(assets.get("ocr_figure", "") or "")
    ocr_text_trans = _format_md_for_display(trans_assets.get("text_trans", "") or "")
    ocr_table_trans = _format_md_for_display(trans_assets.get("table_trans", "") or "")
    ocr_figure_trans = _format_md_for_display(trans_assets.get("figure_trans", "") or "")
    ocr_log = assets.get("ocr_log", "") or ""

    # 2) PG 查 doc 候选（可能多个）
    ctx = core.RlsContext(app_id=app_id, clearance=clearance, request_id=str(uuid.uuid4()))
    docs = core.pg_store.find_docs_by_doc_dir(ctx=ctx, doc_dir=doc_dir, limit=20)
    doc_choices = _make_doc_choices(docs)

    if not docs:
        status = (
            f"⚠️ 页面产物已加载（MinIO），但 PG 未找到匹配的 doc：doc_dir={doc_dir}\n"
            f"- 你可以检查 docs.title/source_uri 是否包含 doc_dir"
        )
        return (
            img, ocr_text, ocr_table, ocr_figure, ocr_text_trans, ocr_table_trans, ocr_figure_trans, ocr_log,
            gr.update(choices=[], value=None),
            {},
            gr.update(choices=[], value=None),
            {},
            status,
        )

    # 只有 1 个候选：自动选中并加载 chunks
    if len(docs) == 1:
        doc_id = docs[0]["doc_id"]
        doc_state, chunk_choices, chunks_state, extra = _load_chunks_for_doc(app_id, clearance, doc_id, page_no)

        status = (
            f"✅ Done：doc_dir={doc_dir} page={page_no}\n"
            f"✅ PG doc auto matching 1 piece，auto chosen and loaded chunks\n"
            f"{extra}"
        )
        return (
            img, ocr_text, ocr_table, ocr_figure, ocr_text_trans, ocr_table_trans, ocr_figure_trans, ocr_log,
            gr.update(choices=doc_choices, value=doc_id),
            doc_state,
            gr.update(choices=chunk_choices, value=(chunk_choices[0][1] if chunk_choices else None)),
            chunks_state,
            status,
        )

    # 多个候选：让用户选 doc，再加载 chunks
    status = (
        f"✅ 页面产物已加载（MinIO）：doc_dir={doc_dir} page={page_no}\n"
        f"⚠️ PG 中匹配到多个 doc（{len(docs)}），请先在下拉框选择 doc，再自动加载该 doc 的本页 chunks。"
    )
    return (
        img, ocr_text, ocr_table, ocr_figure, ocr_text_trans, ocr_table_trans, ocr_figure_trans, ocr_log,
        gr.update(choices=doc_choices, value=None),
        {},
        gr.update(choices=[], value=None),
        {},
        status,
    )


def ui_translate_doc(
    app_id: str,
    clearance: int,
    doc_dir: str,
    target_lang: str,
) -> str:
    app_id = (app_id or config.RAG_APP_ID).strip()
    clearance = int(clearance or getattr(config, "RAG_CLEARANCE", 0))
    doc_dir = (doc_dir or "").strip()
    target_lang = (target_lang or "").strip().lower()

    if not doc_dir:
        return "❌ doc_dir 为空"

    res = translate_doc_pages(
        rag_app_id=app_id,
        doc_dir=doc_dir,
        target_lang=target_lang,
        rag_clearance=clearance,
    )
    if not res.get("ok"):
        return f"❌ 翻译失败：{res.get('error')}"

    msg = (
        f"✅ 翻译完成：doc_id={res.get('doc_id')}\n"
        f"- version_id={res.get('version_id')}\n"
        f"- version_no={res.get('version_no')}\n"
        f"- pages_done={res.get('pages_done')}/{res.get('pages_total')}"
    )
    errs = res.get("errors") or []
    if errs:
        msg += "\n⚠️ 部分页失败：\n" + "\n".join(errs[:10])
    return msg


def ui_doc_pick_changed(
    app_id: str,
    clearance: int,
    page_no: int,
    doc_id: str,
) -> Tuple[Any, Dict[str, Any], str]:
    """
    选中 doc 后加载 chunks
    返回：chunk_sel update, chunks_state, status_extra
    """
    app_id = (app_id or config.RAG_APP_ID).strip()
    clearance = int(clearance or getattr(config, "RAG_CLEARANCE", 0))
    page_no = int(page_no or 1)

    doc_state, chunk_choices, chunks_state, extra = _load_chunks_for_doc(app_id, clearance, doc_id, page_no)
    status = f"✅ Load chunks by chosen doc：\n{extra}"
    return gr.update(choices=chunk_choices, value=(chunk_choices[0][1] if chunk_choices else None)), chunks_state, status


def ui_pick_chunk(chunk_id: str, chunks_state: Dict[str, Any]) -> Tuple[str, str]:
    """
    返回：editor_text, chunk_meta
    """
    if not chunk_id or not chunks_state or chunk_id not in chunks_state:
        return "", ""
    ch = chunks_state[chunk_id]
    meta = (
        f"chunk_id={ch.get('chunk_id')}\n"
        f"chunk_index={ch.get('chunk_index')}\n"
        f"page_no={ch.get('page_no')}\n"
        f"created_at={ch.get('created_at')}"
    )
    chunk_text = ch.get("chunk_text", "") or ""
    return chunk_text, _format_html_for_display(meta)


def ui_save_chunk(
    app_id: str,
    clearance: int,
    doc_dir: str,
    page_no: int,
    doc_id: str,
    chunk_id: str,
    edited_text: str,
    reembed: bool,
) -> Tuple[str, Any, Dict[str, Any], str]:
    """
    保存后刷新本页 chunks（基于选择的 doc_id）
    返回：status, chunk_sel update, chunks_state, chunk_meta
    """
    app_id = (app_id or config.RAG_APP_ID).strip()
    clearance = int(clearance or getattr(config, "RAG_CLEARANCE", 0))
    doc_dir = (doc_dir or "").strip()
    page_no = int(page_no or 1)
    doc_id = (doc_id or "").strip()

    if not doc_id:
        return "❌ Not choose doc（doc_id empty）", gr.update(choices=[], value=None), {}, ""

    if not chunk_id:
        return "❌ Not choose chunk", gr.update(choices=[], value=None), {}, ""

    res = core.save_reviewed_chunk(app_id, clearance, chunk_id, edited_text, reembed=reembed)
    if not res.get("ok"):
        return f"❌ Fail to save：{res.get('error')}", gr.update(choices=[], value=None), {}, ""

    # 刷新本页 chunks
    _, chunk_choices, chunks_state, extra = _load_chunks_for_doc(app_id, clearance, doc_id, page_no)
    # 刷新 meta
    _, meta = ui_pick_chunk(chunk_id, chunks_state)

    status = (
        f"✅ Save successful：chunk_id={chunk_id}（reembed={bool(reembed)}）\n"
        f"✅ Refreshed chunks\n{extra}\n"
        f"（MinIO doc_dir={doc_dir}）"
    )
    return status, gr.update(choices=chunk_choices, value=chunk_id), chunks_state, meta


def ui_find_docs_for_delete(
    app_id: str,
    clearance: int,
    doc_dir: str,
) -> Tuple[Any,  dict[Any | None, Dict[str, Any]], str]:
    app_id = (app_id or config.RAG_APP_ID).strip()
    clearance = int(clearance or getattr(config, "RAG_CLEARANCE", 0))
    doc_dir = (doc_dir or "").strip()

    if not doc_dir:
        return gr.update(choices=[], value=None), {}, "❌ doc_dir 为空"

    ctx = core.RlsContext(app_id=app_id, clearance=clearance, request_id=str(uuid.uuid4()))
    docs = core.pg_store.find_docs_by_doc_dir(ctx=ctx, doc_dir=doc_dir, limit=50)
    choices = _make_doc_choices(docs)
    mapping = {d.get("doc_id"): d for d in docs if d.get("doc_id")}
    status = f"✅ 找到 {len(choices)} 个候选 doc"
    return gr.update(choices=choices, value=None), mapping, status


def ui_pick_doc_for_delete(doc_id: str, docs_state: Dict[str, Any]) -> Tuple[str, str]:
    if not doc_id or not docs_state or doc_id not in docs_state:
        return "", "❌ 未选择 doc"
    d = docs_state[doc_id]
    meta = (
        f"doc_id={d.get('doc_id')}\n"
        f"title={d.get('title') or ''}\n"
        f"source_uri={d.get('source_uri') or ''}\n"
        f"classification={d.get('classification')}"
    )
    return _format_html_for_display(meta), "✅ 已选择 doc"


def ui_delete_doc(
    app_id: str,
    clearance: int,
    doc_id: str,
    confirm_doc_id: str,
    docs_state: Dict[str, Any],
) -> Tuple[Any, Dict[str, Any], str, str, Any, str, str, str, str, str, str, str, Any, Dict[str, Any], str]:
    app_id = (app_id or config.RAG_APP_ID).strip()
    clearance = int(clearance or getattr(config, "RAG_CLEARANCE", 0))
    doc_id = (doc_id or "").strip()
    confirm_doc_id = (confirm_doc_id or "").strip()

    if not doc_id:
        return (
            gr.update(choices=[], value=None),
            docs_state or {},
            "",
            "❌ 未选择 doc",
            None,
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            gr.update(choices=[], value=None),
            {},
            "",
        )

    if confirm_doc_id != doc_id:
        return (
            gr.update(),
            docs_state or {},
            "",
            "❌ 二次确认不匹配，请输入完整 doc_id 以确认删除",
            None,
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            gr.update(choices=[], value=None),
            {},
            "",
        )

    res = core.delete_doc(app_id, clearance, doc_id)
    if not res.get("ok"):
        return (
            gr.update(),
            docs_state or {},
            "",
            f"❌ 删除失败：{res.get('error')}",
            None,
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            gr.update(choices=[], value=None),
            {},
            "",
        )

    if docs_state and doc_id in docs_state:
        docs_state = dict(docs_state)
        docs_state.pop(doc_id, None)

    choices = _make_doc_choices(list(docs_state.values())) if docs_state else []
    status = (
        f"✅ 删除成功：doc_id={doc_id}\n"
        f"- doc_dir={res.get('doc_dir')}\n"
        f"- objects_deleted={res.get('objects_deleted')}"
    )
    return (
        gr.update(choices=choices, value=None),
        docs_state or {},
        "",
        status,
        None,
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        gr.update(choices=[], value=None),
        {},
        "",
    )


def build_app() -> gr.Blocks:
    with gr.Blocks(title="Nexora Data Manager - OCR Verify") as demo:
        gr.Markdown("## Results Verify（Pages from MinIO + PGV chunks verify mannually）")

        with gr.Row():
            app_id = gr.Textbox(label="Knowledge Base ID", value=getattr(config, "RAG_APP_ID", ""), scale=2)
            clearance = gr.Number(label="Clearance", value=int(getattr(config, "RAG_CLEARANCE", 0)), precision=0, scale=1)
            doc_dir = gr.Textbox(label="Doc Name（Source doc name + uuid）", placeholder="For exmpale：test file1_086ec5b7-....", scale=4)
            page_no = gr.Number(label="Page no", value=1, precision=0, scale=1)
            btn_load = gr.Button("Loading", variant="primary", scale=1)

        status = gr.Textbox(label="Status", lines=5)

        with gr.Accordion("逐页翻译（vLLM）", open=False):
            with gr.Row():
                trans_lang = gr.Dropdown(
                    label="Target Language",
                    choices=["ar", "ch", "en"],
                    value="en",
                    interactive=True,
                )
                btn_translate = gr.Button("开始翻译", variant="primary")
            trans_status = gr.Textbox(label="Translation Status", lines=6)

        # 新增：doc 候选选择
        doc_pick = gr.Dropdown(label="PG Doc candidates（matching title/object key）", choices=[], value=None)

        doc_state = gr.State({})     # {"doc_id","version_id","page_no"}（目前主要用于调试/扩展）
        chunks_state = gr.State({})  # chunk_id -> chunk dict

        with gr.Row():
            img = gr.Image(label="Page Image（MinIO）", height=600)
            with gr.Column():
                gr.Markdown("**Text（MinIO）**")
                ocr_text = gr.Markdown(elem_id="ocr_text_html", sanitize_html=False, max_height=240)
                gr.Markdown("**Table（MinIO）**")
                ocr_table = gr.Markdown(elem_id="ocr_table_html", sanitize_html=False, max_height=240)
                gr.Markdown("**Figure（MinIO）**")
                ocr_figure = gr.Markdown(elem_id="ocr_figure_html", sanitize_html=False, max_height=240)
            with gr.Column():
                gr.Markdown("**Text（Translated）**")
                ocr_text_trans = gr.Markdown(elem_id="ocr_text_trans_html", sanitize_html=False, max_height=240)
                gr.Markdown("**Table（Translated）**")
                ocr_table_trans = gr.Markdown(elem_id="ocr_table_trans_html", sanitize_html=False, max_height=240)
                gr.Markdown("**Figure（Translated）**")
                ocr_figure_trans = gr.Markdown(elem_id="ocr_figure_trans_html", sanitize_html=False, max_height=240)

        with gr.Accordion("OCR Log（MinIO）", open=False):
            ocr_log = gr.Textbox(label="OCR Log", lines=1)

        gr.Markdown("### This Chunks（from pgvector， filter by [[META ... page=...]]）")
        with gr.Row():
            chunk_sel = gr.Dropdown(label="Choose chunk", choices=[], value=None, interactive=True, scale=2)
            reembed = gr.Checkbox(label="Save and recalculate the vector (more accurate, but slower).", value=False, scale=1)
            btn_save = gr.Button("Save", variant="primary", scale=1)

        chunk_meta = gr.HTML(label="Chunk Meta")
        chunk_editor = gr.Textbox(label="Chunk Content (Editable)", lines=16)

        # --- events ---
        # 1) 加载：拉 MinIO + 查 doc candidates（若唯一则自动加载 chunks）
        btn_load.click(
            fn=ui_load_page,
            inputs=[app_id, clearance, doc_dir, page_no],
            outputs=[
                img,
                ocr_text,
                ocr_table,
                ocr_figure,
                ocr_text_trans,
                ocr_table_trans,
                ocr_figure_trans,
                ocr_log,
                doc_pick,
                doc_state,
                chunk_sel,
                chunks_state,
                status,
            ],
        )

        btn_translate.click(
            fn=ui_translate_doc,
            inputs=[app_id, clearance, doc_dir, trans_lang],
            outputs=[trans_status],
        )

        # 2) 选择 doc：加载该 doc 的本页 chunks
        doc_pick.change(
            fn=ui_doc_pick_changed,
            inputs=[app_id, clearance, page_no, doc_pick],
            outputs=[chunk_sel, chunks_state, status],
        )

        # 3) 选择 chunk：填充编辑框
        chunk_sel.change(
            fn=ui_pick_chunk,
            inputs=[chunk_sel, chunks_state],
            outputs=[chunk_editor, chunk_meta],
        )

        # 4) 保存：写回 + 刷新本页 chunks（用选择的 doc_id）
        btn_save.click(
            fn=ui_save_chunk,
            inputs=[app_id, clearance, doc_dir, page_no, doc_pick, chunk_sel, chunk_editor, reembed],
            outputs=[status, chunk_sel, chunks_state, chunk_meta],
        )

        gr.Markdown("### 文档删除（仅删除单个 doc 及其对象存储）")
        with gr.Row():
            del_doc_dir = gr.Textbox(label="doc_dir（用于查找）", placeholder="例如：test file1_086ec5b7-....", scale=4)
            del_btn_find = gr.Button("查找 doc", variant="secondary", scale=1)

        del_doc_pick = gr.Dropdown(label="选择要删除的 doc", choices=[], value=None)
        del_docs_state = gr.State({})
        del_doc_meta = gr.HTML(label="Doc Meta")
        del_confirm = gr.Textbox(label="二次确认（输入完整 doc_id）", lines=1)
        del_status = gr.Textbox(label="删除状态", lines=4)
        del_btn = gr.Button("删除该文档（危险）", variant="stop")

        del_btn_find.click(
            fn=ui_find_docs_for_delete,
            inputs=[app_id, clearance, del_doc_dir],
            outputs=[del_doc_pick, del_docs_state, del_status],
        )

        del_doc_pick.change(
            fn=ui_pick_doc_for_delete,
            inputs=[del_doc_pick, del_docs_state],
            outputs=[del_doc_meta, del_status],
        )

        del_btn.click(
            fn=ui_delete_doc,
            inputs=[app_id, clearance, del_doc_pick, del_confirm, del_docs_state],
            outputs=[
                del_doc_pick,
                del_docs_state,
                del_doc_meta,
                del_status,
                img,
                ocr_text,
                ocr_table,
                ocr_figure,
                ocr_text_trans,
                ocr_table_trans,
                ocr_figure_trans,
                ocr_log,
                chunk_sel,
                chunks_state,
                chunk_meta,
            ],
        )

    return demo


if __name__ == "__main__":
    demo = build_app()
    demo.launch(
        server_name="0.0.0.0",
        server_port=7861,
        css=(
            "#ocr_text_html, #ocr_table_html, #ocr_figure_html, "
            "#ocr_text_trans_html, #ocr_table_trans_html, #ocr_figure_trans_html {"
            "border: 1px solid var(--border-color-primary, #333);"
            "padding: 8px;"
            "border-radius: 6px;"
            "}"
        ),
    )
