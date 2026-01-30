"""Gradio 前端（仅 UI），复用 core.py 核心能力"""

import gradio as gr
import config
import re
from core import AppState, chat_send, clear_chat_state, clear_db, ingest_file


_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")


def _is_arabic_text(text: str) -> bool:
    return bool(_ARABIC_RE.search(text or ""))


def _wrap_rtl(text: str) -> str:
    if not text:
        return text
    # Force RTL direction and right alignment for Arabic replies in Chatbot.
    return f'<div dir="rtl" style="text-align:right">{text}</div>'


def _format_ui_messages_for_display(messages):
    if not messages:
        return []
    formatted = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content") or ""
        if role == "assistant" and _is_arabic_text(content):
            content = _wrap_rtl(content)
        formatted.append({"role": role, "content": content})
    return formatted


def on_upload_pdf(
    pdf_file,
    parse_modes,
    chunk_mode,
    ocr_lang_choice,
    graph_enabled,
    graph_prompt_key,
    rag_app_id,
    rag_clearance,
    state: AppState,
):
    if state is None:
        state = AppState.new()
    if pdf_file is None:
        # gr.File 组件清空必须返回 None（不要返回空字符串，否则会被当成路径去缓存）
        return state, None, gr.update(value="未选择PDF")

    info = ingest_file(
        file_path=pdf_file.name,
        parse_modes=parse_modes,
        chunk_mode=chunk_mode,
        ocr_lang_choice=ocr_lang_choice,
        graph_enabled=graph_enabled,
        graph_prompt_key=graph_prompt_key,
        rag_app_id=rag_app_id,
        rag_clearance=rag_clearance,
    )

    msg = (
        f"Doc Name：{info.get('doc_name')} \nObject key={info.get('source_uri')} \n"
        f"Processing Done：Total {info.get('pdf_chars')} chars,Chunking {info.get('chunks')} pieces（pages={info.get('pages')}）\n"
        f"DB：{info.get('db_msg')}"
    )
    # 入库完成后清空上传控件
    return state, None, gr.update(value=msg)

def on_send(user_text: str, graph_enabled, graph_prompt_key, rag_app_id, rag_clearance, state: AppState):
    if state is None:
        state = AppState.new()

    res = chat_send(
        state=state,
        user_text=user_text,
        graph_enabled=graph_enabled,
        graph_prompt_key=graph_prompt_key,
        rag_app_id=rag_app_id,
        rag_clearance=rag_clearance,
    )
    if not res.get("ok"):
        return state, _format_ui_messages_for_display(state.ui_messages), gr.update(value="")

    answer = res.get("answer", "")
    sources = res.get("sources", []) or []

    # 在 UI 中把 sources 追加到回答末尾（保持你原来的体验）
    if sources:
        lines = ["\n\n---\nSource："]
        for i, s in enumerate(sources, 1):
            title = s.get("title") or s.get("doc_id") or f"doc{i}"
            uri = s.get("source_uri") or ""
            lines.append(f"{i}. {title}  {uri}")
        answer = answer + "\n" + "\n".join(lines)

        # 覆盖 ui_messages 中最后一条 assistant
        state.ui_messages[-1]["content"] = answer

    return state, _format_ui_messages_for_display(state.ui_messages), gr.update(value="")


def on_clear(state: AppState):
    state = clear_chat_state()
    return state, [], None, "no docs uploaded", ""


def on_clear_db(rag_app_id):
    res = clear_db(rag_app_id)
    if res.get("ok"):
        return gr.update(value="\n".join(res.get("messages", [])))
    return gr.update(value=f"Fail Clearance：{res.get('error')}")


def build_app():
    with gr.Blocks(title="Nexora") as demo:
        gr.Markdown("# Nexora")

        state = gr.State(AppState.new())

        with gr.Row():
            rag_app_id = gr.Textbox(label="Knowledge Base ID", value=config.RAG_APP_ID)
            rag_clearance = gr.Number(label="Clearance", value=config.RAG_CLEARANCE, precision=0)
        
        chatbot = gr.Chatbot(label="Chatbot", height=320)
        user_text = gr.Textbox(label="Enter", placeholder="Please enter the questions...", lines=2)
        send_btn = gr.Button("Send")
        clear_btn = gr.Button("Clear Chat")
        
        with gr.Row():
            with gr.Column(scale=1): 
                upload_file = gr.File(label="Upload（PDF / TXT / DOCX / XLSX / JPG）", file_types=[".pdf", ".txt", ".docx", ".xlsx", ".jpg", ".png", ".jpeg"])
            with gr.Column(scale=1): 
                parse_modes = gr.CheckboxGroup(
                    choices=["OCR", "VLM"],
                    value=["OCR", "VLM"],
                    label="OCR Mode",
                )
                ocr_lang_choice = gr.Radio(
                    choices=["ch", "en", "ar"],
                    value="ar",
                    label="OCR lang ",
                )                
            with gr.Column(scale=1): 
                chunk_mode = gr.Radio(
                    choices=["Std. Chunk", "Adv. Chunk"],
                    value="Adv. Chunk",
                    label="Chunk Mode",
                )
                graph_enabled = gr.Checkbox(
                    label="Graph Enabled",
                    value=config.GRAPH_ENABLED, 
                )
                graph_prompt_key = gr.Radio(
                    choices=[
                        ("BC (Strict)", "strict"),
                        ("ICT (Medium)", "medium_it"),
                        ("GEN (Loose)", "loose"),
                    ],
                    value=config.GRAPH_ENTITY_PROMPT_DEFAULT,
                    label="Graph Prompt",
                )
            with gr.Column(scale=3): 
                pdf_status = gr.Textbox(label="Processing Status", value="Docs not loaded", interactive=False, lines=9)
        upload_btn = gr.Button("Process and Enter to DB")

        with gr.Row():
            clear_db_btn = gr.Button("Clear DB（Danger!）", variant="stop")
            clear_db_status = gr.Textbox(label="Result of Clear", interactive=False, lines=3)



        upload_btn.click(
            on_upload_pdf,
            inputs=[upload_file, parse_modes, chunk_mode, ocr_lang_choice, graph_enabled, graph_prompt_key, rag_app_id, rag_clearance, state],
            outputs=[state, upload_file, pdf_status],
        )

        send_btn.click(
            on_send,
            inputs=[user_text, graph_enabled, graph_prompt_key, rag_app_id, rag_clearance, state],
            outputs=[state, chatbot, user_text],
        )

        clear_btn.click(
            on_clear,
            inputs=[state],
            outputs=[state, chatbot, upload_file, pdf_status, user_text],
        )

        clear_db_btn.click(
            on_clear_db,
            inputs=[rag_app_id],
            outputs=[clear_db_status],
        )

    return demo


if __name__ == "__main__":
    app = build_app()
    app.launch(server_name="0.0.0.0", server_port=7867)
