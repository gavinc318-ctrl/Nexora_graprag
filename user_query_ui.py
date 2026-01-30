"""User Query UI (Standalone)

A standalone Gradio app for end-users to query documents (English UI).
- Does NOT modify or depend on the existing gradio_ui.py.
- Reuses business logic from core.py only.

Run:
    python user_query_ui.py

Notes:
- Requires core.py to provide:
    - AppState
    - chat_send(state, user_text, graph_enabled, rag_app_id, rag_clearance) -> dict
    - parse_page_from_meta(chunk_text) -> Optional[int]
    - load_ocr_page_assets(rag_app_id, doc_dir, page_no) -> dict with key: png_bytes
- If your chat_send doesn't return "hits" / "sources", the right-side source viewer may be empty.
"""

from __future__ import annotations

import io
import re
from typing import Any, Dict, List, Optional, Tuple

import gradio as gr
from PIL import Image

import config
from core import AppState, chat_send, parse_page_from_meta, load_ocr_page_assets

_DOC_DIR_RE = re.compile(r"(?:^|/)(?P<app>[^/]+)/(?P<docdir>[^/]+)/", re.IGNORECASE)
_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")
_RTL_START = "\u202B"
_RTL_END = "\u202C"


def _parse_doc_dir_from_source_uri(source_uri: str, rag_app_id: str) -> Optional[str]:
    """Best-effort: extract <doc_dir> from a MinIO-like URI/path.

    Expected:
      .../<app_id>/<doc_dir>/ocr/...
      .../<app_id>/<doc_dir>/pdf/...
    """
    source_uri = (source_uri or "").strip()
    rag_app_id = (rag_app_id or "").strip()
    if not source_uri or not rag_app_id:
        return None

    anchor = f"/{rag_app_id}/"
    if anchor in source_uri:
        tail = source_uri.split(anchor, 1)[1]
        parts = [p for p in tail.split("/") if p]
        return parts[0] if parts else None

    m = _DOC_DIR_RE.search(source_uri)
    if not m:
        return None
    if (m.group("app") or "").strip() != rag_app_id:
        return None
    return (m.group("docdir") or "").strip() or None


def _png_bytes_to_pil(png_bytes: bytes) -> Optional[Image.Image]:
    if not png_bytes:
        return None
    try:
        return Image.open(io.BytesIO(png_bytes)).convert("RGB")
    except Exception:
        return None


def _is_arabic_text(text: str) -> bool:
    return bool(_ARABIC_RE.search(text or ""))


def _wrap_rtl(text: str) -> str:
    if not text:
        return text
    # Force RTL direction and right alignment for Arabic replies in Chatbot.
    return f'<div dir="rtl" style="text-align:right">{text}</div>'


def _format_ui_messages_for_display(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    if not messages:
        return []
    formatted: List[Dict[str, str]] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content") or ""
        if role == "assistant" and _is_arabic_text(content):
            content = _wrap_rtl(content)
        formatted.append({"role": role, "content": content})
    return formatted


def _build_citations_from_hits(
    hits: List[Dict[str, Any]],
    sources: List[Dict[str, Any]],
    rag_app_id: str,
) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
    """Return (dropdown_choices, citation_map).

    citation_map[label] => {title, source_uri, doc_dir, page_no}
    """
    src_by_doc: Dict[str, Dict[str, str]] = {}
    for s in sources or []:
        did = str(s.get("doc_id") or "").strip()
        if not did:
            continue
        src_by_doc[did] = {
            "title": (s.get("title") or "").strip(),
            "source_uri": (s.get("source_uri") or "").strip(),
        }

    seen = set()
    choices: List[str] = []
    cmap: Dict[str, Dict[str, Any]] = {}

    for h in hits or []:
        did = str(h.get("doc_id") or "").strip()
        chunk_text = (h.get("chunk_text") or "").strip()
        page_no = parse_page_from_meta(chunk_text)
        if not did or not page_no:
            continue

        key = (did, int(page_no))
        if key in seen:
            continue
        seen.add(key)

        meta = src_by_doc.get(did, {})
        title = meta.get("title") or did
        source_uri = meta.get("source_uri") or ""
        doc_dir = _parse_doc_dir_from_source_uri(source_uri, rag_app_id)

        label = f"{title}  |  page {page_no}"
        choices.append(label)
        cmap[label] = {
            "doc_id": did,
            "title": title,
            "source_uri": source_uri,
            "doc_dir": doc_dir,
            "page_no": int(page_no),
        }

    return choices, cmap


def _select_citation(label: str, rag_app_id: str, citation_map: Dict[str, Any]):
    """Update right panel: PNG + doc info row."""
    label = (label or "").strip()
    if not label or not isinstance(citation_map, dict) or label not in citation_map:
        return None, gr.update(value=[])

    item = citation_map[label] or {}
    doc_dir = (item.get("doc_dir") or "").strip()
    page_no = int(item.get("page_no") or 0)

    img = None
    if doc_dir and page_no > 0:
        try:
            assets = load_ocr_page_assets(rag_app_id=rag_app_id, doc_dir=doc_dir, page_no=page_no)
            img = _png_bytes_to_pil(assets.get("png_bytes") or b"")
        except Exception:
            img = None

    doc_table = [[
        item.get("title") or "",
        item.get("source_uri") or "",
        str(page_no) if page_no else "",
        doc_dir or "(unknown)",
    ]]

    return img, gr.update(value=doc_table)


def on_send_query(
    user_text: str,
    graph_enabled: bool,
    rag_app_id: str,
    rag_clearance: int,
    state: AppState,
):
    """Send message, update chat, and refresh PNG viewer."""
    if state is None:
        state = AppState.new()

    user_text = (user_text or "").strip()
    if not user_text:
        return (
            state,
            _format_ui_messages_for_display(state.ui_messages if getattr(state, "ui_messages", None) else []),
            gr.update(value=""),
            {},
            gr.update(choices=[], value=None),
            None,
            gr.update(value=[]),
        )

    res = chat_send(
        state=state,
        user_text=user_text,
        graph_enabled=graph_enabled,
        rag_app_id=rag_app_id,
        rag_clearance=rag_clearance,
    )
    if not res.get("ok"):
        return (
            state,
            _format_ui_messages_for_display(state.ui_messages),
            gr.update(value=""),
            {},
            gr.update(choices=[], value=None),
            None,
            gr.update(value=[]),
        )

    hits = res.get("hits", []) or []
    sources = res.get("sources", []) or []
    choices, cmap = _build_citations_from_hits(hits=hits, sources=sources, rag_app_id=rag_app_id)

    selected = choices[0] if choices else None
    img, doc_table = _select_citation(selected or "", rag_app_id, cmap)

    return (
        state,
        _format_ui_messages_for_display(state.ui_messages),
        gr.update(value=""),
        cmap,
        gr.update(choices=choices, value=selected),
        img,
        doc_table,
    )


def on_change_citation(label: str, rag_app_id: str, citation_map: Dict[str, Any]):
    return _select_citation(label, rag_app_id, citation_map)


def on_clear(state: AppState):
    try:
        state = AppState.new()
    except Exception:
        state = None
    return state, [], "", {}, gr.update(choices=[], value=None), None, gr.update(value=[])


def build_app():
    with gr.Blocks(
        title="User Query",
        theme=gr.themes.Soft(primary_hue="green"),
    ) as demo:

        gr.Markdown(
            "# User Query\n"
            "Enter **App ID** and **Clearance**, ask questions, and inspect source pages.\n"
        )

        state = gr.State(AppState.new())
        citation_map_state = gr.State({})

        with gr.Row():
            rag_app_id = gr.Textbox(label="Knowledge Base ID", value=getattr(config, "RAG_APP_ID", "default"))
            rag_clearance = gr.Number(
                label="Clearance",
                value=int(getattr(config, "RAG_CLEARANCE", 0)),
                precision=0,
            )
            graph_enabled = gr.Checkbox(label="Graph Enabled", value=config.GRAPH_ENABLED)

        with gr.Row():
            # Left: Chat
            with gr.Column(scale=5):
                chatbot = gr.Chatbot(label="Chat", height=560)

                gr.Markdown("**Your question**")

                with gr.Row(equal_height=True):
                    # Input box
                    with gr.Column(scale=9):
                        user_text = gr.Textbox(
                            label=None,
                            placeholder="Type your question and press Enter...",
                            lines=1,
                            container=True,
                        )

                    # Send button (same row, same baseline)
                    with gr.Column(scale=1, min_width=140):
                        gr.Markdown("&nbsp;")  # 对齐到 textbox 的输入区域
                        send_btn = gr.Button(
                            "Send",
                            size="lg",
                            variant="primary",
                        )
                clear_btn = gr.Button("Clear history", variant="secondary")

            # Right: Source Viewer (PNG only)
            with gr.Column(scale=5):
                citation_dd = gr.Dropdown(label="Sources", choices=[], value=None, interactive=True)
                page_img = gr.Image(label="Source Page (PNG)", height=420)
                doc_table = gr.Dataframe(
                    headers=["Document", "Source URI", "Page", "doc_dir"],
                    row_count=1,
                    column_count=4,
                    interactive=False,
                    label="Selected document",
                )

        send_btn.click(
            on_send_query,
            inputs=[user_text, graph_enabled, rag_app_id, rag_clearance, state],
            outputs=[
                state,
                chatbot,
                user_text,
                citation_map_state,
                citation_dd,
                page_img,
                doc_table,
            ],
        )
        user_text.submit(
            on_send_query,
            inputs=[user_text, graph_enabled, rag_app_id, rag_clearance, state],
            outputs=[
                state,
                chatbot,
                user_text,
                citation_map_state,
                citation_dd,
                page_img,
                doc_table,
            ],
        )
        citation_dd.change(
            on_change_citation,
            inputs=[citation_dd, rag_app_id, citation_map_state],
            outputs=[page_img, doc_table],
        )
        clear_btn.click(
            on_clear,
            inputs=[state],
            outputs=[
                state,
                chatbot,
                user_text,
                citation_map_state,
                citation_dd,
                page_img,
                doc_table,
            ],
        )

    return demo


if __name__ == "__main__":
    app = build_app()
    # Use a different port from your admin/ingest UI to avoid conflict
    app.launch(server_name="0.0.0.0", server_port=7862)
