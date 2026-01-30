"""Backfill missing entity embeddings."""

from __future__ import annotations

import argparse
import json
from typing import Any, List, Sequence, Tuple

import config
from core import embed_text
from graphfunc.graph_pg_store import GraphPgStore, PgConfig as GraphPgConfig, RlsContext as GraphRlsContext


def _normalize_aliases(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, str):
        try:
            data = json.loads(value)
            if isinstance(data, list):
                return [str(v) for v in data if str(v).strip()]
        except Exception:
            pass
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def _build_entity_embedding_text(name: str, aliases: Sequence[str]) -> str:
    parts = [name.strip()] if name else []
    parts.extend(a.strip() for a in aliases if a and a.strip())
    return " | ".join(p for p in parts if p)


def _fetch_missing_embeddings(
    store: GraphPgStore,
    ctx: GraphRlsContext,
    batch_size: int,
) -> List[Tuple[str, str, Any]]:
    with store._connect() as conn:  # noqa: SLF001
        store._set_rls(conn, ctx)  # noqa: SLF001
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT entity_id::text, name, aliases
                FROM entity
                WHERE app_id = %s
                  AND is_active = true
                  AND embedding IS NULL
                ORDER BY occurrence_count DESC
                LIMIT %s;
                """,
                (ctx.app_id, batch_size),
            )
            rows = cur.fetchall()
    return [(str(r[0]), r[1] or "", r[2]) for r in rows]


def _update_entity_embedding(
    store: GraphPgStore,
    ctx: GraphRlsContext,
    entity_id: str,
    embedding: Sequence[float],
) -> None:
    vec_literal = store._vec_literal(embedding)
    with store._connect() as conn:  # noqa: SLF001
        store._set_rls(conn, ctx)  # noqa: SLF001
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE entity
                SET embedding = %s::vector,
                    updated_at = now(),
                    updated_by = current_user
                WHERE app_id = %s AND entity_id = %s;
                """,
                (vec_literal, ctx.app_id, entity_id),
            )
        conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill missing entity embeddings")
    parser.add_argument("--app-id", default=config.RAG_APP_ID)
    parser.add_argument("--clearance", type=int, default=config.RAG_CLEARANCE)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--max-total", type=int, default=0, help="0 means no limit")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    store = GraphPgStore(
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
    ctx = GraphRlsContext(app_id=args.app_id, clearance=int(args.clearance), request_id="backfill")

    total = 0
    while True:
        rows = _fetch_missing_embeddings(store, ctx, args.batch_size)
        if not rows:
            break
        if args.dry_run:
            for entity_id, name, aliases_raw in rows:
                aliases = _normalize_aliases(aliases_raw)
                text = _build_entity_embedding_text(name, aliases)
                print(f"[DRY] {entity_id} -> {text}")
            print("[DRY] stopping after one batch")
            break
        for entity_id, name, aliases_raw in rows:
            aliases = _normalize_aliases(aliases_raw)
            text = _build_entity_embedding_text(name, aliases)
            if not text:
                continue
            try:
                emb = embed_text(text)
                _update_entity_embedding(store, ctx, entity_id, emb)
                total += 1
                if total % 20 == 0:
                    print(f"[OK] updated {total} entities")
            except Exception as e:
                print(f"[WARN] {entity_id} embed failed: {type(e).__name__}: {e}")
            if args.max_total and total >= args.max_total:
                print(f"[STOP] reached max_total={args.max_total}")
                return 0

    print(f"[DONE] updated {total} entities")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
