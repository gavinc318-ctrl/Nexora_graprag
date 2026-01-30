CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 0=公开, 1=内部, 2=敏感, 3=机密 （你可以按你们标准改）
CREATE TABLE IF NOT EXISTS docs (
  doc_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  app_id        text NOT NULL,
  title         text,
  source_uri    text,        -- 原始文件路径/对象存储key/业务系统URL
  classification smallint NOT NULL DEFAULT 0,
  created_at    timestamptz NOT NULL DEFAULT now(),
  created_by    text NOT NULL DEFAULT current_user
);

-- 文档版本（用于回溯/再解析/重切分/重向量化）
CREATE TABLE IF NOT EXISTS doc_versions (
  version_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  doc_id        uuid NOT NULL REFERENCES docs(doc_id) ON DELETE CASCADE,
  version_no    int  NOT NULL,
  content_hash  text NOT NULL,           -- 原文/解析结果hash（SHA256）
  parser_ver    text,                    -- 解析器版本（例如 pymupdf-x.y / ocr-v1）
  embed_model   text NOT NULL,           -- 向量模型版本（例如 bge-m3@2025-xx）
  created_at    timestamptz NOT NULL DEFAULT now(),
  created_by    text NOT NULL DEFAULT current_user,
  UNIQUE(doc_id, version_no)
);

-- 向量chunk（可存 chunk_text；如果你以后上 MinIO，就存 object_key + offset）
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  doc_id        uuid NOT NULL REFERENCES docs(doc_id) ON DELETE CASCADE,
  version_id    uuid NOT NULL REFERENCES doc_versions(version_id) ON DELETE CASCADE,

  app_id        text NOT NULL,
  classification smallint NOT NULL DEFAULT 0,

  chunk_index   int NOT NULL,
  chunk_text    text NOT NULL,
  chunk_hash    text NOT NULL,

  embedding     vector(4096),            -- 这里改成你实际 embedding 维度
  created_at    timestamptz NOT NULL DEFAULT now(),
  embedding_b   bit
);

-- 向量索引（小规模够用；大一点可换 ivfflat/hnsw）
-- 建议：先不建复杂索引，等你确定维度与查询方式再建
CREATE INDEX IF NOT EXISTS idx_chunks_app_class ON chunks(app_id, classification);
CREATE INDEX IF NOT EXISTS idx_chunks_doc_ver ON chunks(doc_id, version_id);

-- ======================
-- RLS：按 app_id + 密级 强隔离
-- ======================
ALTER TABLE docs ENABLE ROW LEVEL SECURITY;
ALTER TABLE doc_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;

-- 会话变量：app.current_app, app.clearance
-- app.clearance 表示当前调用方允许访问的最高密级（0~3）

CREATE POLICY docs_rls ON docs
USING (
  app_id = current_setting('app.current_app', true)
  AND classification <= COALESCE(NULLIF(current_setting('app.clearance', true), '')::int, -1)
);

CREATE POLICY versions_rls ON doc_versions
USING (
  EXISTS (
    SELECT 1 FROM docs d
    WHERE d.doc_id = doc_versions.doc_id
      AND d.app_id = current_setting('app.current_app', true)
      AND d.classification <= COALESCE(NULLIF(current_setting('app.clearance', true), '')::int, -1)
  )
);

CREATE POLICY chunks_rls ON chunks
USING (
  app_id = current_setting('app.current_app', true)
  AND classification <= COALESCE(NULLIF(current_setting('app.clearance', true), '')::int, -1)
);

-- ======================
-- 审计：写入/更新/删除 变更日志（可追责）
-- ======================
CREATE TABLE IF NOT EXISTS audit_mutations (
  audit_id      bigserial PRIMARY KEY,
  ts            timestamptz NOT NULL DEFAULT now(),
  actor         text NOT NULL DEFAULT current_user,
  action        text NOT NULL,         -- INSERT/UPDATE/DELETE
  table_name    text NOT NULL,
  row_pk        text,
  request_id    text,                  -- 由应用层透传：SET app.request_id='...'
  old_data      jsonb,
  new_data      jsonb
);

CREATE OR REPLACE FUNCTION audit_row_change() RETURNS trigger AS $$
DECLARE
  rid text;
BEGIN
  rid := current_setting('app.request_id', true);

  IF (TG_OP = 'DELETE') THEN
    INSERT INTO audit_mutations(action, table_name, row_pk, request_id, old_data, new_data)
    VALUES ('DELETE', TG_TABLE_NAME, to_jsonb(OLD)->>TG_ARGV[0], rid, to_jsonb(OLD), NULL);
    RETURN OLD;
  ELSIF (TG_OP = 'UPDATE') THEN
    INSERT INTO audit_mutations(action, table_name, row_pk, request_id, old_data, new_data)
    VALUES ('UPDATE', TG_TABLE_NAME, to_jsonb(NEW)->>TG_ARGV[0], rid, to_jsonb(OLD), to_jsonb(NEW));
    RETURN NEW;
  ELSE
    INSERT INTO audit_mutations(action, table_name, row_pk, request_id, old_data, new_data)
    VALUES ('INSERT', TG_TABLE_NAME, to_jsonb(NEW)->>TG_ARGV[0], rid, NULL, to_jsonb(NEW));
    RETURN NEW;
  END IF;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_docs ON docs;
CREATE TRIGGER trg_audit_docs
AFTER INSERT OR UPDATE OR DELETE ON docs
FOR EACH ROW EXECUTE FUNCTION audit_row_change('doc_id');

DROP TRIGGER IF EXISTS trg_audit_versions ON doc_versions;
CREATE TRIGGER trg_audit_versions
AFTER INSERT OR UPDATE OR DELETE ON doc_versions
FOR EACH ROW EXECUTE FUNCTION audit_row_change('version_id');

DROP TRIGGER IF EXISTS trg_audit_chunks ON chunks;
CREATE TRIGGER trg_audit_chunks
AFTER INSERT OR UPDATE OR DELETE ON chunks
FOR EACH ROW EXECUTE FUNCTION audit_row_change('chunk_id');

-- ======================
-- 审计：检索行为日志（最关键：谁查了什么，命中哪些chunk）
-- ======================
CREATE TABLE IF NOT EXISTS audit_search (
  search_id     bigserial PRIMARY KEY,
  ts            timestamptz NOT NULL DEFAULT now(),
  actor         text NOT NULL DEFAULT current_user,
  app_id        text NOT NULL,
  clearance     int  NOT NULL,
  request_id    text,
  query_text    text,          -- 可按需要脱敏/只存hash
  top_k         int NOT NULL,
  filters       jsonb,
  hit_chunk_ids uuid[],
  hit_doc_ids   uuid[],
  score_min     real,
  score_max     real
);
