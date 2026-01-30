"""Graph Management UI (Standalone).

MVP: Entity/Edge/Summary/Quality + global controls.
"""

from __future__ import annotations

import gradio as gr
from typing import Any, Dict, List, Optional, Tuple

import config
from graphmng_service import (
    basic_stats,
    create_edge,
    delete_edge,
    get_summary,
    list_edges,
    list_isolated,
    list_jobs,
    run_maintenance,
    save_summary,
    search_entities,
    set_graph_enabled,
    update_edge,
    update_entity,
)


def build_app():
    with gr.Blocks(title="Graph Management") as demo:
        gr.Markdown("# Graph Management (MVP)")

        with gr.Row():
            rag_app_id = gr.Textbox(label="App ID", value=config.RAG_APP_ID)
            rag_clearance = gr.Number(label="Clearance", value=config.RAG_CLEARANCE, precision=0)

        with gr.Row():
            graph_enabled = gr.Checkbox(label="Graph Enabled", value=config.GRAPH_ENABLED)
            toggle_status = gr.Textbox(label="Toggle Result", interactive=False)
            graph_enabled.change(set_graph_enabled, inputs=[graph_enabled], outputs=[toggle_status])

        with gr.Tab("Overview"):
            with gr.Row():
                stats_btn = gr.Button("Refresh Stats")
                stats_table = gr.Dataframe(
                    headers=["Entities", "Edges", "Entity-Chunks", "Jobs Pending"],
                    row_count=1,
                    column_count=4,
                    interactive=False,
                    label="Basic Stats",
                )
                stats_btn.click(basic_stats, inputs=[rag_app_id, rag_clearance], outputs=[stats_table])

            with gr.Row():
                run_btn = gr.Button("Run Maintenance Once")
                run_status = gr.Textbox(label="Maintenance Result", interactive=False)
                run_btn.click(run_maintenance, inputs=[rag_app_id, rag_clearance], outputs=[run_status])

            with gr.Row():
                status_choice = gr.Radio(choices=["pending", "running", "failed"], value="pending", label="Job Status")
                refresh_jobs = gr.Button("Refresh Jobs")
                jobs_table = gr.Dataframe(
                    headers=["job_id", "job_type", "status", "created_at", "started_at", "finished_at", "error"],
                    row_count=10,
                    column_count=7,
                    interactive=False,
                    label="Graph Jobs",
                )
                refresh_jobs.click(list_jobs, inputs=[rag_app_id, rag_clearance, status_choice], outputs=[jobs_table])

        with gr.Tab("Entities"):
            with gr.Row():
                q = gr.Textbox(label="Search", placeholder="name/alias")
                etype = gr.Textbox(label="Type (or ALL)", value="ALL")
                active_only = gr.Checkbox(label="Active only", value=True)
                btn = gr.Button("Search")
            table = gr.Dataframe(
                headers=["entity_id", "name", "type", "aliases", "confidence", "is_active", "occurrence_count"],
                row_count=10,
                column_count=7,
                interactive=False,
                label="Entities",
            )
            btn.click(search_entities, inputs=[rag_app_id, rag_clearance, q, etype, active_only], outputs=[table])

            with gr.Row():
                ent_id = gr.Textbox(label="entity_id")
                name = gr.Textbox(label="name")
                et = gr.Textbox(label="type")
            with gr.Row():
                aliases = gr.Textbox(label="aliases (comma)")
                conf = gr.Dropdown(choices=["high", "medium", "low"], value="medium", label="confidence")
                is_active = gr.Checkbox(label="is_active", value=True)
                save_btn = gr.Button("Update Entity")
                status = gr.Textbox(label="Status", interactive=False)
                save_btn.click(
                    update_entity,
                    inputs=[rag_app_id, rag_clearance, ent_id, name, et, aliases, conf, is_active],
                    outputs=[status],
                )

        with gr.Tab("Edges"):
            with gr.Row():
                ent_id = gr.Textbox(label="entity_id (for listing)")
                list_btn = gr.Button("List Edges")
            edge_table = gr.Dataframe(
                headers=["src", "dst", "type", "weight", "confidence", "evidence_count", "evidence_chunks", "notes"],
                row_count=10,
                column_count=8,
                interactive=False,
                label="Edges",
            )
            list_btn.click(list_edges, inputs=[rag_app_id, rag_clearance, ent_id], outputs=[edge_table])

            with gr.Row():
                src = gr.Textbox(label="src_entity_id")
                dst = gr.Textbox(label="dst_entity_id")
                etype = gr.Textbox(label="edge_type", value="co_occurs")
            with gr.Row():
                weight = gr.Number(label="weight", value=0.5)
                conf = gr.Dropdown(choices=["high", "medium", "low"], value="medium", label="confidence")
                evidence = gr.Textbox(label="evidence_chunk_ids (comma)")
                notes = gr.Textbox(label="edge_notes", value="manual")
            with gr.Row():
                create_btn = gr.Button("Create/Upsert Edge")
                update_btn = gr.Button("Update Edge")
                delete_btn = gr.Button("Delete Edge")
                status = gr.Textbox(label="Status", interactive=False)
            create_btn.click(
                create_edge,
                inputs=[rag_app_id, rag_clearance, src, dst, etype, weight, conf, evidence],
                outputs=[status],
            )
            update_btn.click(
                update_edge,
                inputs=[rag_app_id, rag_clearance, src, dst, etype, weight, conf, evidence, notes],
                outputs=[status],
            )
            delete_btn.click(
                delete_edge,
                inputs=[rag_app_id, rag_clearance, src, dst, etype],
                outputs=[status],
            )

        with gr.Tab("Summaries"):
            with gr.Row():
                ent_id = gr.Textbox(label="entity_id")
                load_btn = gr.Button("Load Summary")
            summary_text = gr.Textbox(label="summary_text", lines=8)
            summary_type = gr.Textbox(label="summary_type", value="entity")
            anchor_chunks = gr.Textbox(label="anchor_chunk_ids (comma)")
            conf = gr.Dropdown(choices=["high", "medium", "low"], value="medium", label="confidence")
            save_btn = gr.Button("Save Summary")
            status = gr.Textbox(label="Status", interactive=False)
            load_btn.click(
                get_summary,
                inputs=[rag_app_id, rag_clearance, ent_id],
                outputs=[summary_text, summary_type, anchor_chunks, conf],
            )
            save_btn.click(
                save_summary,
                inputs=[rag_app_id, rag_clearance, ent_id, summary_text, summary_type, anchor_chunks, conf],
                outputs=[status],
            )

        with gr.Tab("Quality"):
            btn = gr.Button("List Isolated Entities")
            table = gr.Dataframe(
                headers=["entity_id", "name", "type", "confidence", "is_active"],
                row_count=10,
                column_count=5,
                interactive=False,
                label="Isolated Entities",
            )
            btn.click(list_isolated, inputs=[rag_app_id, rag_clearance], outputs=[table])

    return demo


if __name__ == "__main__":
    app = build_app()
    app.launch(server_name="0.0.0.0", server_port=7863)
