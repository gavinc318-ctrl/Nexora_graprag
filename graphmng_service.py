"""Graph management services (non-UI)."""

from __future__ import annotations

import uuid
from typing import Any, List, Tuple

import config
from core import process_graph_jobs_once
from graphfunc.graph_pg_store import GraphPgStore, PgConfig as GraphPgConfig, RlsContext as GraphRlsContext


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


def _ctx(app_id: str, clearance: int) -> GraphRlsContext:
    return GraphRlsContext(app_id=app_id, clearance=int(clearance), request_id=str(uuid.uuid4()))


def set_graph_enabled(enabled: bool) -> str:
    # Note: runtime-only toggle; .env persists separately.
    config.GRAPH_ENABLED = bool(enabled)
    return f"Graph Enabled set to {config.GRAPH_ENABLED}"


def list_jobs(app_id: str, clearance: int, status: str) -> List[List[Any]]:
    ctx = _ctx(app_id, clearance)
    with graph_store._connect() as conn:
        graph_store._set_rls(conn, ctx)  # noqa: SLF001
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT job_id::text, job_type, status, created_at, started_at, finished_at, error_message
                FROM graph_job
                WHERE app_id = %s AND status = %s
                ORDER BY created_at DESC
                LIMIT 50;
                """,
                (app_id, status),
            )
            rows = cur.fetchall()
    return [list(r) for r in rows]


def run_maintenance(app_id: str, clearance: int) -> str:
    n = process_graph_jobs_once(app_id, clearance, limit=10)
    return f"Processed {n} jobs"


def basic_stats(app_id: str, clearance: int) -> List[List[Any]]:
    ctx = _ctx(app_id, clearance)
    with graph_store._connect() as conn:
        graph_store._set_rls(conn, ctx)  # noqa: SLF001
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM entity WHERE app_id = %s) AS entities,
                  (SELECT COUNT(*) FROM entity_edge WHERE app_id = %s) AS edges,
                  (SELECT COUNT(*) FROM entity_chunk WHERE app_id = %s) AS entity_chunks,
                  (SELECT COUNT(*) FROM graph_job WHERE app_id = %s AND status='pending') AS jobs_pending
                """,
                (app_id, app_id, app_id, app_id),
            )
            row = cur.fetchone()
    return [[
        int(row[0] or 0),
        int(row[1] or 0),
        int(row[2] or 0),
        int(row[3] or 0),
    ]]


def search_entities(app_id: str, clearance: int, query: str, entity_type: str, active_only: bool) -> List[List[Any]]:
    ctx = _ctx(app_id, clearance)
    etype = entity_type if entity_type and entity_type != "ALL" else None
    rows = graph_store.search_entities(ctx, query=query, entity_type=etype, active_only=active_only, limit=50)
    out: List[List[Any]] = []
    for r in rows:
        out.append([
            r.get("entity_id"),
            r.get("name"),
            r.get("type"),
            r.get("aliases"),
            r.get("confidence"),
            r.get("is_active"),
            r.get("occurrence_count"),
        ])
    return out


def update_entity(
    app_id: str,
    clearance: int,
    entity_id: str,
    name: str,
    entity_type: str,
    aliases_text: str,
    confidence: str,
    is_active: bool,
) -> str:
    ctx = _ctx(app_id, clearance)
    aliases = []
    if aliases_text:
        aliases = [a.strip() for a in aliases_text.split(",") if a.strip()]
    graph_store.update_entity(
        ctx=ctx,
        entity_id=entity_id,
        name=name,
        entity_type=entity_type,
        aliases=aliases,
        confidence=confidence,
        is_active=is_active,
    )
    return "Entity updated"


def list_edges(app_id: str, clearance: int, entity_id: str) -> List[List[Any]]:
    ctx = _ctx(app_id, clearance)
    rows = graph_store.list_edges_by_entity(ctx, entity_id, limit=100)
    out: List[List[Any]] = []
    for r in rows:
        out.append([
            r.get("src_entity_id"),
            r.get("dst_entity_id"),
            r.get("edge_type"),
            r.get("weight"),
            r.get("confidence"),
            r.get("evidence_count"),
            r.get("evidence_chunk_ids"),
            r.get("edge_notes"),
        ])
    return out


def create_edge(
    app_id: str,
    clearance: int,
    src_entity_id: str,
    dst_entity_id: str,
    edge_type: str,
    weight: float,
    confidence: str,
    evidence_chunks: str,
) -> str:
    ctx = _ctx(app_id, clearance)
    chunk_ids = []
    if evidence_chunks:
        chunk_ids = [c.strip() for c in evidence_chunks.split(",") if c.strip()]
    note = "manual"
    graph_store.upsert_edge(
        ctx=ctx,
        src_entity_id=src_entity_id,
        dst_entity_id=dst_entity_id,
        edge_type=edge_type,
        weight=weight,
        confidence=confidence,
        evidence_chunk_ids=chunk_ids,
        edge_notes=note,
    )
    return "Edge upserted"


def update_edge(
    app_id: str,
    clearance: int,
    src_entity_id: str,
    dst_entity_id: str,
    edge_type: str,
    weight: float,
    confidence: str,
    evidence_chunks: str,
    edge_notes: str,
) -> str:
    ctx = _ctx(app_id, clearance)
    chunk_ids = None
    if evidence_chunks is not None:
        chunk_ids = [c.strip() for c in evidence_chunks.split(",") if c.strip()]
    graph_store.update_edge(
        ctx=ctx,
        src_entity_id=src_entity_id,
        dst_entity_id=dst_entity_id,
        edge_type=edge_type,
        weight=weight,
        confidence=confidence,
        evidence_chunk_ids=chunk_ids,
        edge_notes=edge_notes,
    )
    return "Edge updated"


def delete_edge(app_id: str, clearance: int, src_entity_id: str, dst_entity_id: str, edge_type: str) -> str:
    ctx = _ctx(app_id, clearance)
    graph_store.delete_edge(ctx, src_entity_id, dst_entity_id, edge_type)
    return "Edge deleted"


def get_summary(app_id: str, clearance: int, entity_id: str) -> Tuple[str, str, str, str]:
    ctx = _ctx(app_id, clearance)
    s = graph_store.get_entity_summary(ctx, entity_id)
    if not s:
        return "", "entity", "", "medium"
    return (
        s.get("summary_text") or "",
        s.get("summary_type") or "entity",
        ",".join(s.get("anchor_chunk_ids") or []),
        s.get("confidence") or "medium",
    )


def save_summary(
    app_id: str,
    clearance: int,
    entity_id: str,
    summary_text: str,
    summary_type: str,
    anchor_chunks: str,
    confidence: str,
) -> str:
    ctx = _ctx(app_id, clearance)
    chunk_ids = [c.strip() for c in (anchor_chunks or "").split(",") if c.strip()]
    graph_store.upsert_entity_summary(
        ctx=ctx,
        entity_id=entity_id,
        summary_text=summary_text,
        summary_type=summary_type or "entity",
        anchor_chunk_ids=chunk_ids,
        confidence=confidence or "medium",
    )
    return "Summary saved"


def list_isolated(app_id: str, clearance: int) -> List[List[Any]]:
    ctx = _ctx(app_id, clearance)
    rows = graph_store.list_isolated_entities(ctx, limit=100)
    return [[r.get("entity_id"), r.get("name"), r.get("type"), r.get("confidence"), r.get("is_active")] for r in rows]
