# graphfunc/graph_pg_store.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple
import psycopg
import config


@dataclass(frozen=True)
class PgConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str
    sslmode: str = "disable"
    admin_user: Optional[str] = None
    admin_password: Optional[str] = None


@dataclass(frozen=True)
class RlsContext:
    app_id: str
    clearance: int
    request_id: str


class GraphPgStore:
    def __init__(self, cfg: PgConfig):
        self.cfg = cfg

    def _vec_literal(self, v: Sequence[float]) -> str:
        embed_dim = getattr(config, "EMBED_DIM", None)
        if embed_dim and len(v) != embed_dim:
            raise ValueError(f"Embedding dim mismatch: {len(v)} != {embed_dim}")
        return "[" + ",".join(f"{float(x):.10g}" for x in v) + "]"

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(
            host=self.cfg.host,
            port=self.cfg.port,
            dbname=self.cfg.dbname,
            user=self.cfg.user,
            password=self.cfg.password,
            sslmode=self.cfg.sslmode,
            autocommit=False,
        )

    def _connect_admin(self) -> psycopg.Connection:
        if not self.cfg.admin_user or not self.cfg.admin_password:
            raise PermissionError("Admin DB credentials not configured")
        return psycopg.connect(
            host=self.cfg.host,
            port=self.cfg.port,
            dbname=self.cfg.dbname,
            user=self.cfg.admin_user,
            password=self.cfg.admin_password,
            sslmode=self.cfg.sslmode,
            autocommit=False,
        )

    def _set_rls(self, conn: psycopg.Connection, ctx: RlsContext) -> None:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.current_app', %s, false)", (ctx.app_id,))
            cur.execute("SELECT set_config('app.clearance', %s, false)", (str(ctx.clearance),))
            cur.execute("SELECT set_config('app.request_id', %s, false)", (ctx.request_id,))

    # -------------------------
    # Entity
    # -------------------------
    def upsert_entity(
        self,
        ctx: RlsContext,
        name: str,
        entity_type: str,
        aliases: Optional[List[str]] = None,
        description: Optional[str] = None,
        confidence: str = "medium",
        classification: int = 0,
        embedding: Optional[Sequence[float]] = None,
    ) -> Dict[str, Any]:
        aliases = aliases or []
        emb_literal = self._vec_literal(embedding) if embedding is not None else None
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO entity(
                        app_id, name, type, aliases, description, confidence, classification, embedding,
                        occurrence_count, first_occurrence, last_occurrence, is_active
                    )
                    VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s, %s::vector, 1, now(), now(), true)
                    ON CONFLICT (app_id, name, type, classification)
                    DO UPDATE SET
                        aliases = EXCLUDED.aliases,
                        description = COALESCE(EXCLUDED.description, entity.description),
                        confidence = EXCLUDED.confidence,
                        embedding = COALESCE(EXCLUDED.embedding, entity.embedding),
                        occurrence_count = entity.occurrence_count + 1,
                        last_occurrence = now(),
                        is_active = true,
                        updated_at = now(),
                        updated_by = current_user
                    RETURNING entity_id, name, type, confidence, occurrence_count, is_active;
                    """,
                    (ctx.app_id, name, entity_type, aliases, description, confidence, classification, emb_literal),
                )
                row = cur.fetchone()
            conn.commit()
        return dict(zip(
            ["entity_id", "name", "type", "confidence", "occurrence_count", "is_active"],
            row,
        ))

    def deactivate_entity(self, ctx: RlsContext, entity_id: str) -> None:
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE entity
                    SET is_active = false,
                        confidence = 'low',
                        updated_at = now(),
                        updated_by = current_user
                    WHERE app_id = %s AND entity_id = %s;
                    """,
                    (ctx.app_id, entity_id),
                )
            conn.commit()

    def search_entities(
        self,
        ctx: RlsContext,
        query: str = "",
        entity_type: Optional[str] = None,
        active_only: bool = True,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        query = (query or "").strip()
        like_q = f"%{query}%"
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT entity_id, name, type, aliases, confidence, is_active, occurrence_count
                    FROM entity
                    WHERE app_id = %s
                      AND (%s = '' OR name ILIKE %s OR aliases::text ILIKE %s)
                      AND (%s::text IS NULL OR type = %s::text)
                      AND (%s = false OR is_active = true)
                    ORDER BY occurrence_count DESC
                    LIMIT %s;
                    """,
                    (
                        ctx.app_id,
                        query,
                        like_q,
                        like_q,
                        entity_type,
                        entity_type,
                        active_only,
                        limit,
                    ),
                )
                rows = cur.fetchall()
        return [
            dict(
                zip(
                    ["entity_id", "name", "type", "aliases", "confidence", "is_active", "occurrence_count"],
                    r,
                )
            )
            for r in rows
        ]

    def update_entity(
        self,
        ctx: RlsContext,
        entity_id: str,
        name: Optional[str] = None,
        entity_type: Optional[str] = None,
        aliases: Optional[List[str]] = None,
        confidence: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> None:
        sets = []
        vals: List[Any] = []
        if name is not None:
            sets.append("name = %s")
            vals.append(name)
        if entity_type is not None:
            sets.append("type = %s")
            vals.append(entity_type)
        if aliases is not None:
            sets.append("aliases = %s::jsonb")
            vals.append(aliases)
        if confidence is not None:
            sets.append("confidence = %s")
            vals.append(confidence)
        if is_active is not None:
            sets.append("is_active = %s")
            vals.append(is_active)
        if not sets:
            return
        vals.extend([ctx.app_id, entity_id])
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE entity
                    SET {", ".join(sets)},
                        updated_at = now(),
                        updated_by = current_user
                    WHERE app_id = %s AND entity_id = %s;
                    """,
                    tuple(vals),
                )
            conn.commit()

    def decrement_entity_occurrence(
        self, ctx: RlsContext, entity_id: str, dec_count: int = 1
    ) -> None:
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE entity
                    SET occurrence_count = GREATEST(occurrence_count - %s, 0),
                        updated_at = now(),
                        updated_by = current_user
                    WHERE app_id = %s AND entity_id = %s;
                    """,
                    (dec_count, ctx.app_id, entity_id),
                )
            conn.commit()

    # -------------------------
    # Entity-Chunk
    # -------------------------
    def upsert_entity_chunk(
        self,
        ctx: RlsContext,
        entity_id: str,
        chunk_id: str,
        mention_count: int = 1,
        char_position: Optional[int] = None,
        extracted_context: Optional[str] = None,
        confidence: str = "medium",
        classification: int = 0,
    ) -> None:
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO entity_chunk(
                        app_id, entity_id, chunk_id, mention_count,
                        char_position, extracted_context, confidence, classification
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (app_id, entity_id, chunk_id)
                    DO UPDATE SET
                        mention_count = entity_chunk.mention_count + EXCLUDED.mention_count,
                        confidence = EXCLUDED.confidence,
                        classification = EXCLUDED.classification,
                        created_at = entity_chunk.created_at,
                        created_by = entity_chunk.created_by;
                    """,
                    (
                        ctx.app_id,
                        entity_id,
                        chunk_id,
                        mention_count,
                        char_position,
                        extracted_context,
                        confidence,
                        classification,
                    ),
                )
            conn.commit()

    # -------------------------
    # Edge
    # -------------------------
    def upsert_edge(
        self,
        ctx: RlsContext,
        src_entity_id: str,
        dst_entity_id: str,
        edge_type: str,
        weight: float = 0.5,
        evidence_chunk_ids: Optional[List[str]] = None,
        confidence: str = "medium",
        classification: int = 0,
        edge_notes: Optional[str] = None,
    ) -> None:
        evidence_chunk_ids = evidence_chunk_ids or []
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO entity_edge(
                        app_id, src_entity_id, dst_entity_id, edge_type,
                        weight, confidence, classification, evidence_count, evidence_chunk_ids, edge_notes
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 1, %s::jsonb, %s)
                    ON CONFLICT (app_id, src_entity_id, dst_entity_id, edge_type)
                    DO UPDATE SET
                        weight = LEAST(entity_edge.weight + EXCLUDED.weight, 1.000),
                        confidence = EXCLUDED.confidence,
                        evidence_count = entity_edge.evidence_count + 1,
                        evidence_chunk_ids = (
                            SELECT jsonb_agg(DISTINCT x)
                            FROM jsonb_array_elements(entity_edge.evidence_chunk_ids || EXCLUDED.evidence_chunk_ids) AS x
                        ),
                        edge_notes = COALESCE(EXCLUDED.edge_notes, entity_edge.edge_notes),
                        updated_at = now(),
                        updated_by = current_user;
                    """,
                    (
                        ctx.app_id,
                        src_entity_id,
                        dst_entity_id,
                        edge_type,
                        weight,
                        confidence,
                        classification,
                        evidence_chunk_ids,
                        edge_notes,
                    ),
                )
            conn.commit()

    def list_edges_by_entity(
        self,
        ctx: RlsContext,
        entity_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      src_entity_id, dst_entity_id, edge_type, weight, confidence,
                      evidence_count, evidence_chunk_ids, edge_notes
                    FROM entity_edge
                    WHERE app_id = %s AND (src_entity_id = %s OR dst_entity_id = %s)
                    ORDER BY weight DESC
                    LIMIT %s;
                    """,
                    (ctx.app_id, entity_id, entity_id, limit),
                )
                rows = cur.fetchall()
        return [
            dict(
                zip(
                    [
                        "src_entity_id",
                        "dst_entity_id",
                        "edge_type",
                        "weight",
                        "confidence",
                        "evidence_count",
                        "evidence_chunk_ids",
                        "edge_notes",
                    ],
                    r,
                )
            )
            for r in rows
        ]

    def update_edge(
        self,
        ctx: RlsContext,
        src_entity_id: str,
        dst_entity_id: str,
        edge_type: str,
        weight: Optional[float] = None,
        confidence: Optional[str] = None,
        evidence_chunk_ids: Optional[List[str]] = None,
        edge_notes: Optional[str] = None,
    ) -> None:
        sets = []
        vals: List[Any] = []
        if weight is not None:
            sets.append("weight = %s")
            vals.append(weight)
        if confidence is not None:
            sets.append("confidence = %s")
            vals.append(confidence)
        if evidence_chunk_ids is not None:
            sets.append("evidence_chunk_ids = %s::jsonb")
            vals.append(evidence_chunk_ids)
        if edge_notes is not None:
            sets.append("edge_notes = %s")
            vals.append(edge_notes)
        if not sets:
            return
        vals.extend([ctx.app_id, src_entity_id, dst_entity_id, edge_type])
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE entity_edge
                    SET {", ".join(sets)},
                        updated_at = now(),
                        updated_by = current_user
                    WHERE app_id = %s
                      AND src_entity_id = %s
                      AND dst_entity_id = %s
                      AND edge_type = %s;
                    """,
                    tuple(vals),
                )
            conn.commit()

    def delete_edge(
        self,
        ctx: RlsContext,
        src_entity_id: str,
        dst_entity_id: str,
        edge_type: str,
    ) -> None:
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    DELETE FROM entity_edge
                    WHERE app_id = %s
                      AND src_entity_id = %s
                      AND dst_entity_id = %s
                      AND edge_type = %s;
                    """,
                    (ctx.app_id, src_entity_id, dst_entity_id, edge_type),
                )
            conn.commit()

    def decrement_edge_evidence(
        self, ctx: RlsContext, src_entity_id: str, dst_entity_id: str, edge_type: str, dec_count: int = 1
    ) -> None:
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE entity_edge
                    SET evidence_count = GREATEST(evidence_count - %s, 0),
                        updated_at = now(),
                        updated_by = current_user
                    WHERE app_id = %s
                      AND src_entity_id = %s
                      AND dst_entity_id = %s
                      AND edge_type = %s;
                    """,
                    (dec_count, ctx.app_id, src_entity_id, dst_entity_id, edge_type),
                )
            conn.commit()

    # -------------------------
    # Retrieval helpers
    # -------------------------
    def find_entities_by_name_or_alias(
        self, ctx: RlsContext, query: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        like_q = f"%{query}%"
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT entity_id, name, type, aliases, confidence, occurrence_count
                    FROM entity
                    WHERE app_id = %s
                      AND is_active = true
                      AND (
                        name ILIKE %s
                        OR aliases::text ILIKE %s
                      )
                    ORDER BY occurrence_count DESC
                    LIMIT %s;
                    """,
                    (ctx.app_id, like_q, like_q, limit),
                )
                rows = cur.fetchall()
        return [
            dict(zip(["entity_id", "name", "type", "aliases", "confidence", "occurrence_count"], r))
            for r in rows
        ]

    def find_entities_by_embedding(
        self,
        ctx: RlsContext,
        query_embedding: Sequence[float],
        limit: int = 10,
        min_similarity: Optional[float] = None,
    ) -> List[Tuple[Dict[str, Any], float]]:
        qv = self._vec_literal(query_embedding)
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                if min_similarity is None:
                    cur.execute(
                        """
                        SELECT entity_id, name, type, aliases, confidence, occurrence_count,
                               1 - (embedding <=> %s::vector) AS similarity
                        FROM entity
                        WHERE app_id = %s
                          AND is_active = true
                          AND embedding IS NOT NULL
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s;
                        """,
                        (qv, ctx.app_id, qv, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT entity_id, name, type, aliases, confidence, occurrence_count,
                               1 - (embedding <=> %s::vector) AS similarity
                        FROM entity
                        WHERE app_id = %s
                          AND is_active = true
                          AND embedding IS NOT NULL
                          AND (1 - (embedding <=> %s::vector)) >= %s
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s;
                        """,
                        (qv, ctx.app_id, qv, float(min_similarity), qv, limit),
                    )
                rows = cur.fetchall()
        out: List[Tuple[Dict[str, Any], float]] = []
        for r in rows:
            entity = dict(zip(
                ["entity_id", "name", "type", "aliases", "confidence", "occurrence_count", "similarity"],
                r,
            ))
            sim = float(entity.pop("similarity") or 0.0)
            out.append((entity, sim))
        return out

    def get_neighbors(
        self,
        ctx: RlsContext,
        entity_ids: Sequence[str],
        edge_type: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        if not entity_ids:
            return []
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                if edge_type:
                    cur.execute(
                        """
                        SELECT src_entity_id, dst_entity_id, edge_type, weight, confidence, evidence_count
                        FROM entity_edge
                        WHERE app_id = %s
                          AND src_entity_id = ANY(%s)
                          AND edge_type = %s
                        ORDER BY weight DESC
                        LIMIT %s;
                        """,
                        (ctx.app_id, list(entity_ids), edge_type, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT src_entity_id, dst_entity_id, edge_type, weight, confidence, evidence_count
                        FROM entity_edge
                        WHERE app_id = %s
                          AND src_entity_id = ANY(%s)
                        ORDER BY weight DESC
                        LIMIT %s;
                        """,
                        (ctx.app_id, list(entity_ids), limit),
                    )
                rows = cur.fetchall()
        return [
            dict(zip(
                ["src_entity_id", "dst_entity_id", "edge_type", "weight", "confidence", "evidence_count"],
                r,
            ))
            for r in rows
        ]

    def get_neighbor_entities(
        self,
        ctx: RlsContext,
        entity_ids: Sequence[str],
        edge_type: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        if not entity_ids:
            return []
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                if edge_type:
                    cur.execute(
                        """
                        SELECT
                          ee.src_entity_id,
                          ee.dst_entity_id,
                          ee.edge_type,
                          ee.weight,
                          ee.confidence,
                          ee.evidence_count,
                          e.name AS dst_name,
                          e.type AS dst_type
                        FROM entity_edge ee
                        JOIN entity e ON ee.dst_entity_id = e.entity_id
                        WHERE ee.app_id = %s
                          AND ee.src_entity_id = ANY(%s)
                          AND ee.edge_type = %s
                          AND e.is_active = true
                        ORDER BY ee.weight DESC
                        LIMIT %s;
                        """,
                        (ctx.app_id, list(entity_ids), edge_type, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT
                          ee.src_entity_id,
                          ee.dst_entity_id,
                          ee.edge_type,
                          ee.weight,
                          ee.confidence,
                          ee.evidence_count,
                          e.name AS dst_name,
                          e.type AS dst_type
                        FROM entity_edge ee
                        JOIN entity e ON ee.dst_entity_id = e.entity_id
                        WHERE ee.app_id = %s
                          AND ee.src_entity_id = ANY(%s)
                          AND e.is_active = true
                        ORDER BY ee.weight DESC
                        LIMIT %s;
                        """,
                        (ctx.app_id, list(entity_ids), limit),
                    )
                rows = cur.fetchall()
        return [
            dict(
                zip(
                    [
                        "src_entity_id",
                        "dst_entity_id",
                        "edge_type",
                        "weight",
                        "confidence",
                        "evidence_count",
                        "dst_name",
                        "dst_type",
                    ],
                    r,
                )
            )
            for r in rows
        ]

    def get_entity_summary(self, ctx: RlsContext, entity_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT entity_id, summary_text, summary_type, anchor_chunk_ids, confidence
                    FROM entity_summary
                    WHERE app_id = %s AND entity_id = %s;
                    """,
                    (ctx.app_id, entity_id),
                )
                row = cur.fetchone()
        if not row:
            return None
        return dict(
            zip(["entity_id", "summary_text", "summary_type", "anchor_chunk_ids", "confidence"], row)
        )

    def upsert_entity_summary(
        self,
        ctx: RlsContext,
        entity_id: str,
        summary_text: str,
        summary_type: str = "entity",
        anchor_chunk_ids: Optional[List[str]] = None,
        confidence: str = "medium",
        classification: int = 0,
    ) -> None:
        anchor_chunk_ids = anchor_chunk_ids or []
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO entity_summary(
                        app_id, entity_id, summary_text, summary_type,
                        anchor_chunk_ids, confidence, classification, last_updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, now())
                    ON CONFLICT (app_id, entity_id)
                    DO UPDATE SET
                        summary_text = EXCLUDED.summary_text,
                        summary_type = EXCLUDED.summary_type,
                        anchor_chunk_ids = EXCLUDED.anchor_chunk_ids,
                        confidence = EXCLUDED.confidence,
                        classification = EXCLUDED.classification,
                        last_updated_at = now(),
                        last_updated_by = current_user;
                    """,
                    (
                        ctx.app_id,
                        entity_id,
                        summary_text,
                        summary_type,
                        anchor_chunk_ids,
                        confidence,
                        classification,
                    ),
                )
            conn.commit()

    def list_isolated_entities(
        self,
        ctx: RlsContext,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT e.entity_id, e.name, e.type, e.confidence, e.is_active
                    FROM entity e
                    LEFT JOIN entity_edge ee
                      ON e.entity_id = ee.src_entity_id
                      OR e.entity_id = ee.dst_entity_id
                    WHERE e.app_id = %s
                      AND ee.src_entity_id IS NULL
                      AND ee.dst_entity_id IS NULL
                    ORDER BY e.created_at DESC
                    LIMIT %s;
                    """,
                    (ctx.app_id, limit),
                )
                rows = cur.fetchall()
        return [
            dict(zip(["entity_id", "name", "type", "confidence", "is_active"], r))
            for r in rows
        ]
    def fetch_chunk_entities(
        self,
        ctx: RlsContext,
        chunk_ids: Sequence[str],
    ) -> List[Dict[str, Any]]:
        if not chunk_ids:
            return []
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT chunk_id::text, entity_id::text, mention_count
                    FROM entity_chunk
                    WHERE app_id = %s
                      AND chunk_id = ANY(%s);
                    """,
                    (ctx.app_id, list(chunk_ids)),
                )
                rows = cur.fetchall()
        return [
            dict(zip(["chunk_id", "entity_id", "mention_count"], r))
            for r in rows
        ]

    def list_chunk_ids_by_entities(
        self,
        ctx: RlsContext,
        entity_ids: Sequence[str],
        limit: int = 50,
    ) -> List[str]:
        if not entity_ids:
            return []
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT chunk_id::text, SUM(mention_count) AS score
                    FROM entity_chunk
                    WHERE app_id = %s
                      AND entity_id = ANY(%s)
                    GROUP BY chunk_id
                    ORDER BY score DESC
                    LIMIT %s;
                    """,
                    (ctx.app_id, list(entity_ids), limit),
                )
                rows = cur.fetchall()
        return [str(r[0]) for r in rows]

    def deactivate_entities_with_zero_occurrence(
        self,
        ctx: RlsContext,
        entity_ids: Sequence[str],
    ) -> None:
        if not entity_ids:
            return
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE entity
                    SET is_active = false,
                        confidence = 'low',
                        updated_at = now(),
                        updated_by = current_user
                    WHERE app_id = %s
                      AND entity_id = ANY(%s)
                      AND occurrence_count <= 0;
                    """,
                    (ctx.app_id, list(entity_ids)),
                )
            conn.commit()

    # -------------------------
    # Graph maintenance jobs
    # -------------------------
    def enqueue_job(
        self, ctx: RlsContext, job_type: str, payload: Dict[str, Any]
    ) -> None:
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO graph_job(app_id, job_type, payload, status)
                    VALUES (%s, %s, %s::jsonb, 'pending');
                    """,
                    (ctx.app_id, job_type, payload),
                )
            conn.commit()

    def fetch_pending_jobs(self, ctx: RlsContext, limit: int = 10) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT job_id, job_type, payload
                    FROM graph_job
                    WHERE app_id = %s AND status = 'pending'
                    ORDER BY created_at ASC
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED;
                    """,
                    (ctx.app_id, limit),
                )
                rows = cur.fetchall()
                cur.execute(
                    """
                    UPDATE graph_job
                    SET status = 'running', started_at = now()
                    WHERE job_id = ANY(%s);
                    """,
                    ([r[0] for r in rows],),
                )
            conn.commit()
        return [dict(zip(["job_id", "job_type", "payload"], r)) for r in rows]

    def mark_job_done(self, ctx: RlsContext, job_id: str, success: bool, error: Optional[str] = None) -> None:
        status = "done" if success else "failed"
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE graph_job
                    SET status = %s,
                        finished_at = now(),
                        error_message = %s
                    WHERE app_id = %s AND job_id = %s;
                    """,
                    (status, error, ctx.app_id, job_id),
                )
            conn.commit()
