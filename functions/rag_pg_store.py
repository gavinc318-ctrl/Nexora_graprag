# functions/rag_pg_store.py
from __future__ import annotations

import uuid
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple
import psycopg

@dataclass(frozen=True)
class PgConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str
    embed_dim: int
    sslmode: str = "disable"  # 内网开发可 disable；生产建议 require/verify-full
    admin_user: Optional[str] = None 
    admin_password: Optional[str] = None

@dataclass(frozen=True)
class RlsContext:
    app_id: str
    clearance: int
    request_id: str


class RagPgStore:
    """
    只负责 PG/pgvector：写入、检索、清空、审计、RLS session vars。
    UI/解析/embedding 不放这里（embedding 由调用方提供向量）。
    """

    def __init__(self, cfg: PgConfig):
        self.cfg = cfg

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
            cur.execute(
                "SELECT set_config('app.current_app', %s, false)",
                (ctx.app_id,)
            )
            cur.execute(
                "SELECT set_config('app.clearance', %s, false)",
                (str(ctx.clearance),)
            )
            cur.execute(
                "SELECT set_config('app.request_id', %s, false)",
                (ctx.request_id,)
            )

    def debug_pgvector_health(self, ctx: RlsContext) -> Dict[str, Any]:
        """
        Debug helper: checks connection, RLS settings, and pgvector distance.
        Intended for temporary diagnostics.
        """
        out: Dict[str, Any] = {}
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute("SELECT current_database(), current_user;")
                out["db_user"] = cur.fetchone()

                cur.execute("SHOW app.current_app;")
                out["app.current_app"] = cur.fetchone()[0]
                cur.execute("SHOW app.clearance;")
                out["app.clearance"] = cur.fetchone()[0]

                cur.execute("SELECT COUNT(*) FROM chunks;")
                out["chunks_count"] = cur.fetchone()[0]

                cur.execute(
                    """
                    SELECT app_id, classification, COUNT(*)
                    FROM chunks
                    GROUP BY app_id, classification
                    ORDER BY app_id, classification;
                    """
                )
                out["chunks_by_app_class"] = cur.fetchall()

                cur.execute(
                    """
                    WITH v AS (
                      SELECT embedding
                      FROM chunks
                      WHERE embedding IS NOT NULL
                      LIMIT 1
                    )
                    SELECT (c.embedding <=> v.embedding) AS dist
                    FROM chunks c, v
                    ORDER BY dist
                    LIMIT 5;
                    """
                )
                out["distance_sample"] = cur.fetchall()
            conn.commit()
        return out

    """"
    def _vec_literal(self, v: Sequence[float]) -> str:
        if len(v) != self.cfg.embed_dim:
            raise ValueError(f"Embedding dim mismatch: {len(v)} != {self.cfg.embed_dim}")
        return "[" + ",".join(str(float(x)) for x in v) + "]"
    """
    def _vec_literal(self, v: Sequence[float]) -> str:
        if len(v) != self.cfg.embed_dim:
            raise ValueError(f"Embedding dim mismatch: {len(v)} != {self.cfg.embed_dim}")
        # pgvector 需要的是字符串 literal：[1,2,3]
        return "[" + ",".join(f"{float(x):.10g}" for x in v) + "]"


    # -------------------------
    # Ingest
    # -------------------------
    def ingest_pdf(
        self,
        ctx: RlsContext,
        title: str,
        source_uri: str,
        classification: int,
        parser_ver: str,
        embed_model: str,
        chunks: Sequence[Tuple[int, str, Sequence[float]]],
        version_no: int = 1,
    ) -> uuid.UUID:
        """
        chunks: [(chunk_index, chunk_text, embedding_vector)]
        返回 doc_id
        """
        with self._connect() as conn:
            self._set_rls(conn, ctx)

            with conn.cursor() as cur:
                # docs
                cur.execute(
                    """
                    INSERT INTO docs(app_id, title, source_uri, classification)
                    VALUES (%s, %s, %s, %s)
                    RETURNING doc_id;
                    """,
                    (ctx.app_id, title, source_uri, classification),
                )
                row = cur.fetchone()

                if row is not None and row[0] is not None: 
                    doc_id = row[0]
                else:
                    doc_id =uuid.UUID(int=0)



                # version（content_hash：这里用 title|source 做占位，实际可用全文 hash）
                cur.execute(
                    """
                    INSERT INTO doc_versions(doc_id, version_no, content_hash, parser_ver, embed_model)
                    VALUES (
                        %s, %s,
                        encode(digest(%s, 'sha256'), 'hex'),
                        %s, %s
                    )
                    RETURNING version_id;
                    """,
                    (doc_id, version_no, f"{title}|{source_uri}", parser_ver, embed_model),
                )
                row = cur.fetchone()
                version_id = row[0] if row is not None and row[0] is not None else None
                
               # chunks
                for idx, text, emb in chunks:
                    vec_literal = self._vec_literal(emb)  # 形如 "[0.1,0.2,...]"

                    #cur.execute(
                     #   f"""
                      #  INSERT INTO chunks(
                       #     doc_id, version_id, app_id, classification,
                        #    chunk_index, chunk_text, chunk_hash,
                         #   embedding
                        #)
                        #VALUES (
                         #   %s, %s, %s, %s,
                          #  %s, %s,
                           # encode(digest(%s, 'sha256'), 'hex'),
                            #{vec_literal}::vector                           
                        #);
                        #""",
                        #(
                         #   doc_id, version_id, ctx.app_id, classification,
                          #  idx, text, text,
                        #),
                   # )

                    cur.execute(
                        """
                        INSERT INTO chunks(
                            doc_id, version_id, app_id, classification,
                            chunk_index, chunk_text, chunk_hash,
                            embedding
                        )
                        VALUES (
                            %s, %s, %s, %s,
                            %s, %s,
                            encode(digest(%s, 'sha256'), 'hex'),
                            %s::vector
                        )
                         """,
                        (
                            doc_id, version_id, ctx.app_id, classification,
                            idx, text,
                            text,           # 你 digest 用的那个输入
                            vec_literal,    # 形如 "[0.1,0.2,...]"
                        ),
                    )
 
            conn.commit()
            return doc_id

    # -------------------------
    # Search + audit_search
    # -------------------------
    def search_chunks(
        self,
        ctx: RlsContext,
        query_text: str,
        query_embedding: Sequence[float],
        top_k: int = 5,
        return_with_scores: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        返回：[{chunk_id, doc_id, version_id, chunk_text, score}, ...]
        同时写入 audit_search
        """
        if top_k is None or int(top_k) <= 0:
            top_k = 5
        with self._connect() as conn:
            self._set_rls(conn, ctx)

            qv = self._vec_literal(query_embedding)

            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        t.chunk_id,
                        t.doc_id,
                        t.version_id,
                        t.chunk_text,
                        t.bscore
                      FROM (
                        SELECT
                            c.chunk_id,
                            c.doc_id,
                            c.version_id,
                            c.chunk_text,
                            (c.embedding <-> %s::vector) AS bscore
                        FROM chunks c
                        WHERE c.embedding IS NOT NULL
                      ) t
                      ORDER BY t.bscore
                      LIMIT %s;
                    """,
                    (qv, top_k),
                )
                rows = cur.fetchall()

                chunk_ids = [r[0] for r in rows]
                doc_ids = [r[1] for r in rows]
                scores = [float(r[4]) for r in rows]

                # 审计：检索行为
                cur.execute(
                    """
                    INSERT INTO audit_search(
                      app_id, clearance, request_id,
                      query_text, top_k, filters,
                      hit_chunk_ids, hit_doc_ids,
                      score_min, score_max
                    )
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s);
                    """,
                    (
                        ctx.app_id,
                        ctx.clearance,
                        ctx.request_id,
                        query_text,
                        top_k,
                        json.dumps({}, ensure_ascii=False),
                        chunk_ids or None,
                        doc_ids or None,
                        min(scores) if scores else None,
                        max(scores) if scores else None,
                    ),
                )

            conn.commit()

            hits: List[Dict[str, Any]] = []
            for (chunk_id, doc_id, version_id, chunk_text, score) in rows:
                item = {
                    "chunk_id": str(chunk_id),
                    "doc_id": str(doc_id),
                    "version_id": str(version_id),
                    "chunk_text": chunk_text,
                }
                if return_with_scores:
                    item["score"] = float(score)
                hits.append(item)
            return hits

    # -------------------------
    # Admin / UI helpers (新增：OCR 校对台用)
    # -------------------------
    def find_docs_by_doc_dir(
        self,
        ctx: RlsContext,
        doc_dir: str,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        通过 doc_dir（源文件名_uuid）在 docs.title / docs.source_uri 中模糊匹配，返回候选 docs。
        你后续 UI 可以选择第一个，或让用户从候选中选一个。
        """
        doc_dir = (doc_dir or "").strip()
        if not doc_dir:
            raise ValueError("doc_dir is empty")

        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT doc_id::text, title, source_uri, classification
                    FROM docs
                    WHERE app_id = %s
                      AND (title ILIKE %s OR source_uri ILIKE %s)
                    ORDER BY created_at DESC NULLS LAST
                    LIMIT %s;
                    """,
                    (ctx.app_id, f"%{doc_dir}%", f"%{doc_dir}%", limit),
                )
                rows = cur.fetchall()

        out: List[Dict[str, Any]] = []
        for doc_id, title, source_uri, classification in rows:
            out.append(
                {
                    "doc_id": str(doc_id),
                    "title": title,
                    "source_uri": source_uri,
                    "classification": int(classification) if classification is not None else None,
                }
            )
        return out

    def get_latest_version_id(self, ctx: RlsContext, doc_id: str) -> Optional[str]:
        """
        返回最新 version_id（按 created_at / version_no 兜底排序）
        """
        doc_id = (doc_id or "").strip()
        if not doc_id:
            raise ValueError("doc_id is empty")

        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT version_id::text
                    FROM doc_versions
                    WHERE doc_id = %s
                    ORDER BY created_at DESC NULLS LAST, version_no DESC
                    LIMIT 1;
                    """,
                    (doc_id,),
                )
                row = cur.fetchone()
                return str(row[0]) if row else None

    def get_latest_version_no(self, ctx: RlsContext, doc_id: str) -> Optional[int]:
        """
        返回最新 version_no（若无版本则 None）
        """
        doc_id = (doc_id or "").strip()
        if not doc_id:
            raise ValueError("doc_id is empty")

        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT MAX(version_no)
                    FROM doc_versions
                    WHERE doc_id = %s;
                    """,
                    (doc_id,),
                )
                row = cur.fetchone()
                return int(row[0]) if row and row[0] is not None else None

    def add_version_and_chunks(
        self,
        ctx: RlsContext,
        doc_id: str,
        version_no: int,
        parser_ver: str,
        embed_model: str,
        chunks: Sequence[Tuple[int, str, Sequence[float]]],
    ) -> str:
        """
        仅新增 version + chunks（复用已有 doc_id）
        返回 version_id
        """
        doc_id = (doc_id or "").strip()
        if not doc_id:
            raise ValueError("doc_id is empty")

        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO doc_versions(doc_id, version_no, content_hash, parser_ver, embed_model)
                    VALUES (
                        %s, %s,
                        encode(digest(%s, 'sha256'), 'hex'),
                        %s, %s
                    )
                    RETURNING version_id;
                    """,
                    (doc_id, version_no, f"{doc_id}|{version_no}", parser_ver, embed_model),
                )
                row = cur.fetchone()
                version_id = row[0] if row else None

                for idx, text, emb in chunks:
                    vec_literal = self._vec_literal(emb)
                    cur.execute(
                        """
                        INSERT INTO chunks(
                            doc_id, version_id, app_id, classification,
                            chunk_index, chunk_text, chunk_hash,
                            embedding
                        )
                        SELECT
                            %s, %s, %s, d.classification,
                            %s, %s,
                            encode(digest(%s, 'sha256'), 'hex'),
                            %s::vector
                        FROM docs d
                        WHERE d.doc_id = %s;
                        """,
                        (
                            doc_id,
                            version_id,
                            ctx.app_id,
                            idx,
                            text,
                            text,
                            vec_literal,
                            doc_id,
                        ),
                    )

            conn.commit()
            return str(version_id) if version_id else ""

    def list_chunks(
        self,
        ctx: RlsContext,
        doc_id: str,
        version_id: str,
    ) -> List[Dict[str, Any]]:
        """
        拉取指定 doc/version 的 chunks（不做 page 过滤，page 由调用方解析 [[META ... page=...]] 再筛）
        """
        doc_id = (doc_id or "").strip()
        version_id = (version_id or "").strip()
        if not doc_id or not version_id:
            raise ValueError("doc_id/version_id is empty")

        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      chunk_id::text,
                      chunk_index,
                      chunk_text,
                      chunk_hash,
                      created_at
                    FROM chunks
                    WHERE app_id = %s
                      AND doc_id = %s
                      AND version_id = %s
                    ORDER BY chunk_index ASC;
                    """,
                    (ctx.app_id, doc_id, version_id),
                )
                rows = cur.fetchall()

        out: List[Dict[str, Any]] = []
        for chunk_id, chunk_index, chunk_text, chunk_hash, created_at in rows:
            out.append(
                {
                    "chunk_id": str(chunk_id),
                    "chunk_index": int(chunk_index),
                    "chunk_text": chunk_text,
                    "chunk_hash": chunk_hash,
                    "created_at": created_at.isoformat() if created_at else None,
                }
            )
        return out

    def get_chunks_by_ids(
        self,
        ctx: RlsContext,
        chunk_ids: Sequence[str],
    ) -> List[Dict[str, Any]]:
        if not chunk_ids:
            return []
        ids = [str(x) for x in chunk_ids if x]
        if not ids:
            return []
        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                      chunk_id::text,
                      doc_id::text,
                      version_id::text,
                      chunk_text
                    FROM chunks
                    WHERE app_id = %s
                      AND chunk_id = ANY(%s::uuid[])
                    ORDER BY array_position(%s::uuid[], chunk_id);
                    """,
                    (ctx.app_id, ids, ids),
                )
                rows = cur.fetchall()
        out: List[Dict[str, Any]] = []
        for chunk_id, doc_id, version_id, chunk_text in rows:
            out.append(
                {
                    "chunk_id": str(chunk_id),
                    "doc_id": str(doc_id),
                    "version_id": str(version_id),
                    "chunk_text": chunk_text,
                }
            )
        return out

    def update_chunk_text(
        self,
        ctx: RlsContext,
        chunk_id: str,
        new_chunk_text: str,
        new_embedding: Optional[Sequence[float]] = None,
    ) -> None:
        """
        更新单条 chunk 的文本内容；可选同时更新 embedding（用于“保存后重算向量”）。
        - new_embedding=None：只更新 chunk_text/chunk_hash（快）
        - new_embedding!=None：同时更新 embedding
        """
        chunk_id = (chunk_id or "").strip()
        if not chunk_id:
            raise ValueError("chunk_id is empty")

        new_chunk_text = (new_chunk_text or "").strip()

        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                if new_embedding is None:
                    cur.execute(
                        """
                        UPDATE chunks
                        SET
                          chunk_text = %s,
                          chunk_hash = encode(digest(%s, 'sha256'), 'hex')
                        WHERE chunk_id = %s;
                        """,
                        (new_chunk_text, new_chunk_text, chunk_id),
                    )
                else:
                    vec_literal = self._vec_literal(new_embedding)
                    cur.execute(
                        """
                        UPDATE chunks
                        SET
                          chunk_text = %s,
                          chunk_hash = encode(digest(%s, 'sha256'), 'hex'),
                          embedding = %s::vector
                        WHERE chunk_id = %s;
                        """,
                        (new_chunk_text, new_chunk_text, vec_literal, chunk_id),
                    )

            conn.commit()

    # -------------------------
    # Clear
    # -------------------------
    def clear_all_docs(self) -> None:
        with self._connect_admin() as conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE docs CASCADE;")
            conn.commit()

    def clear_docs_by_app(self, app_id: str) -> int:
        """
        仅清空指定 app_id 下的数据（通过删除 docs 行触发级联清理）。
        返回删除的 doc 数量。
        """
        app_id = (app_id or "").strip()
        if not app_id:
            raise ValueError("app_id is empty")

        with self._connect_admin() as conn:
            with conn.cursor() as cur:
                # 先统计要删多少
                cur.execute("SELECT COUNT(*) FROM docs WHERE app_id = %s;", (app_id,))
                row = cur.fetchone()
                n =int(row[0]) if row is not None and row[0] is not None else 0
                # 删除 docs（依赖表若设置 ON DELETE CASCADE 会跟着清）
                cur.execute("DELETE FROM docs WHERE app_id = %s;", (app_id,))

            conn.commit()
            return n

    def delete_doc(self, ctx: RlsContext, doc_id: str) -> int:
        """
        删除单个 doc（依赖 ON DELETE CASCADE 清理版本与 chunks）。
        返回删除的 doc 数量（0 或 1）。
        """
        doc_id = (doc_id or "").strip()
        if not doc_id:
            raise ValueError("doc_id is empty")

        with self._connect() as conn:
            self._set_rls(conn, ctx)
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM docs WHERE app_id = %s AND doc_id = %s;",
                    (ctx.app_id, doc_id),
                )
                n = int(cur.rowcount or 0)
            conn.commit()
            return n

    def delete_doc_admin(self, doc_id: str) -> int:
        """
        管理员删除单个 doc（绕过 RLS）。
        返回删除的 doc 数量（0 或 1）。
        """
        doc_id = (doc_id or "").strip()
        if not doc_id:
            raise ValueError("doc_id is empty")

        with self._connect_admin() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM docs WHERE doc_id = %s;", (doc_id,))
                n = int(cur.rowcount or 0)
            conn.commit()
            return n
