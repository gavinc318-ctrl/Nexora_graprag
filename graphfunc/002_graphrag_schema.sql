-- ============================================
-- GraphRAG 统一数据库表结构定义
-- 包含：RAG 原有表 + Graph 新增表 + 统一 RLS
-- 
-- 执行：psql -h 10.55.223.100 -U graphrag_owner -d graphrag_db -f 002_graphrag_schema.sql
-- ============================================

-- ============================================
-- 第1部分：RAG 原有表
-- ============================================

-- 文档主表
CREATE TABLE IF NOT EXISTS docs (
  doc_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  app_id        text NOT NULL,
  title         text,
  source_uri    text,                   -- 原始文件路径/对象存储key/业务系统URL
  classification smallint NOT NULL DEFAULT 0,
  created_at    timestamptz NOT NULL DEFAULT now(),
  created_by    text NOT NULL DEFAULT current_user
);

CREATE INDEX idx_docs_app_class ON docs(app_id, classification);
CREATE INDEX idx_docs_created_at ON docs(app_id, created_at DESC);

\echo '✅ 表 docs 已创建'

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

CREATE INDEX idx_doc_versions_doc_id ON doc_versions(doc_id);
CREATE INDEX idx_doc_versions_created_at ON doc_versions(created_at DESC);

\echo '✅ 表 doc_versions 已创建'

-- 向量chunk（可存 chunk_text；如果你以后上 MinIO，就存 object_key + offset）
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  doc_id          uuid NOT NULL REFERENCES docs(doc_id) ON DELETE CASCADE,
  version_id      uuid NOT NULL REFERENCES doc_versions(version_id) ON DELETE CASCADE,
  
  app_id          text NOT NULL,
  classification  smallint NOT NULL DEFAULT 0,
  
  chunk_index     int NOT NULL,
  chunk_text      text NOT NULL,
  chunk_hash      text NOT NULL,
  
  embedding       vector(1024),          -- 向量维度：必须与 config.EMBED_DIM 一致（qwen3-embedding:0.6b = 1024）
  created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_chunks_app_class ON chunks(app_id, classification);
CREATE INDEX idx_chunks_doc_ver ON chunks(doc_id, version_id);
CREATE INDEX idx_chunks_created_at ON chunks(app_id, created_at DESC);
CREATE INDEX idx_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops);

\echo '✅ 表 chunks 已创建'

-- 审计：变更日志（写入/更新/删除）
CREATE TABLE IF NOT EXISTS audit_mutations (
  audit_id      bigserial PRIMARY KEY,
  ts            timestamptz NOT NULL DEFAULT now(),
  actor         text NOT NULL DEFAULT current_user,
  action        text NOT NULL,         -- INSERT/UPDATE/DELETE
  table_name    text NOT NULL,
  row_pk        text,
  request_id    text,                  -- 由应用层透传
  old_data      jsonb,
  new_data      jsonb
);

CREATE INDEX idx_audit_mutations_ts ON audit_mutations(ts DESC);
CREATE INDEX idx_audit_mutations_table ON audit_mutations(table_name);

\echo '✅ 表 audit_mutations 已创建'

-- 审计：查询行为日志
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

CREATE INDEX idx_audit_search_ts ON audit_search(ts DESC);
CREATE INDEX idx_audit_search_app ON audit_search(app_id, ts DESC);

\echo '✅ 表 audit_search 已创建'

-- ============================================
-- 第2部分：Graph 新增表
-- ============================================

-- 实体表
CREATE TABLE IF NOT EXISTS entity (
  entity_id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  app_id             text NOT NULL,
  name               text NOT NULL,
  type               text NOT NULL,
  aliases            jsonb DEFAULT '[]'::jsonb,
  description        text,
  confidence         text NOT NULL DEFAULT 'medium',
  classification     smallint NOT NULL DEFAULT 0,
  first_occurrence   timestamptz NOT NULL DEFAULT now(),
  last_occurrence    timestamptz NOT NULL DEFAULT now(),
  occurrence_count   int DEFAULT 1,
  is_active          boolean NOT NULL DEFAULT true,
  created_at         timestamptz NOT NULL DEFAULT now(),
  created_by         text DEFAULT current_user,
  updated_at         timestamptz NOT NULL DEFAULT now(),
  updated_by         text DEFAULT current_user,
  embedding          vector(1024),          -- 实体向量：hybrid 召回用，维度同 chunks

  UNIQUE(app_id, name, type, classification)
);

CREATE INDEX idx_entity_app_id ON entity(app_id);
CREATE INDEX idx_entity_name ON entity USING GIN(name gin_trgm_ops);        -- 模糊搜索索引
CREATE INDEX idx_entity_type ON entity(app_id, type);
CREATE INDEX idx_entity_confidence ON entity(app_id, confidence);
CREATE INDEX idx_entity_occurrence ON entity(app_id, occurrence_count DESC);
CREATE INDEX idx_entity_created_at ON entity(app_id, created_at DESC);
CREATE INDEX idx_entity_embedding ON entity USING hnsw (embedding vector_cosine_ops);

\echo '✅ 表 entity 已创建'

-- ============================================
-- 第2部分：实体-chunk 关联表（entity_chunk）
-- ============================================

CREATE TABLE IF NOT EXISTS entity_chunk (
  app_id             text NOT NULL,
  entity_id          uuid NOT NULL REFERENCES entity(entity_id) ON DELETE CASCADE,
  chunk_id           uuid NOT NULL,
  mention_count      int DEFAULT 1,
  char_position      int,
  extracted_context  text,
  confidence         text DEFAULT 'medium',
  classification     smallint NOT NULL DEFAULT 0,
  created_at         timestamptz NOT NULL DEFAULT now(),
  created_by         text DEFAULT current_user,
  
  PRIMARY KEY (app_id, entity_id, chunk_id)
);

CREATE INDEX idx_entity_chunk_chunk_id ON entity_chunk(chunk_id);
CREATE INDEX idx_entity_chunk_confidence ON entity_chunk(app_id, confidence);
CREATE INDEX idx_entity_chunk_created_at ON entity_chunk(app_id, created_at DESC);

-- FK: entity_chunk.chunk_id -> chunks.chunk_id（删除 chunk 时自动清理关联，避免脏引用）
DO $$
BEGIN
  ALTER TABLE entity_chunk
    ADD CONSTRAINT fk_entity_chunk_chunks
    FOREIGN KEY (chunk_id)
    REFERENCES chunks(chunk_id)
    ON DELETE CASCADE;
  RAISE NOTICE '✅ FK entity_chunk.chunk_id -> chunks.chunk_id 已创建';
EXCEPTION WHEN duplicate_object THEN
  RAISE NOTICE '⚠️  FK fk_entity_chunk_chunks 已存在（跳过）';
END
$$;

\echo '✅ 表 entity_chunk 已创建'

-- ============================================
-- 第3部分：实体关系表（entity_edge）
-- ============================================

CREATE TABLE IF NOT EXISTS entity_edge (
  app_id             text NOT NULL,
  src_entity_id      uuid NOT NULL REFERENCES entity(entity_id) ON DELETE CASCADE,
  dst_entity_id      uuid NOT NULL REFERENCES entity(entity_id) ON DELETE CASCADE,
  edge_type          text NOT NULL,
  weight             numeric(5, 3) NOT NULL DEFAULT 0.5,
  confidence         text NOT NULL DEFAULT 'medium',
  classification     smallint NOT NULL DEFAULT 0,
  evidence_count     int DEFAULT 1,
  evidence_chunk_ids jsonb DEFAULT '[]'::jsonb,
  edge_notes         text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  created_by         text DEFAULT current_user,
  updated_at         timestamptz NOT NULL DEFAULT now(),
  updated_by         text DEFAULT current_user,
  
  PRIMARY KEY (app_id, src_entity_id, dst_entity_id, edge_type)
);

-- 确保不创建自环
ALTER TABLE entity_edge ADD CONSTRAINT no_self_loop 
  CHECK (src_entity_id != dst_entity_id);

CREATE INDEX idx_entity_edge_src_dst ON entity_edge(app_id, src_entity_id, dst_entity_id);
CREATE INDEX idx_entity_edge_type ON entity_edge(app_id, edge_type);
CREATE INDEX idx_entity_edge_weight ON entity_edge(app_id, weight DESC);
CREATE INDEX idx_entity_edge_confidence ON entity_edge(app_id, confidence);
CREATE INDEX idx_entity_edge_evidence_count ON entity_edge(app_id, evidence_count DESC);
CREATE INDEX idx_entity_edge_created_at ON entity_edge(app_id, created_at DESC);

\echo '✅ 表 entity_edge 已创建'

-- ============================================
-- 第4部分：实体摘要表（entity_summary）
-- ============================================

CREATE TABLE IF NOT EXISTS entity_summary (
  app_id             text NOT NULL,
  entity_id          uuid NOT NULL REFERENCES entity(entity_id) ON DELETE CASCADE,
  summary_text       text NOT NULL,
  summary_type       text DEFAULT 'entity',
  anchor_chunk_ids   jsonb DEFAULT '[]'::jsonb,
  generation_model   text,
  generation_prompt  text,
  confidence         text DEFAULT 'medium',
  classification     smallint NOT NULL DEFAULT 0,
  last_updated_at    timestamptz NOT NULL DEFAULT now(),
  last_updated_by    text DEFAULT current_user,
  created_at         timestamptz NOT NULL DEFAULT now(),
  created_by         text DEFAULT current_user,
  
  PRIMARY KEY (app_id, entity_id)
);

CREATE INDEX idx_entity_summary_type ON entity_summary(app_id, summary_type);
CREATE INDEX idx_entity_summary_confidence ON entity_summary(app_id, confidence);
CREATE INDEX idx_entity_summary_updated_at ON entity_summary(app_id, last_updated_at DESC);

\echo '✅ 表 entity_summary 已创建'

-- ============================================
-- 第5部分：Graph 异步维护任务表（graph_job）
-- ============================================

CREATE TABLE IF NOT EXISTS graph_job (
  job_id        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  app_id        text NOT NULL,
  job_type      text NOT NULL,
  payload       jsonb NOT NULL DEFAULT '{}'::jsonb,
  status        text NOT NULL DEFAULT 'pending',
  created_at    timestamptz NOT NULL DEFAULT now(),
  started_at    timestamptz,
  finished_at   timestamptz,
  error_message text,
  created_by    text DEFAULT current_user
);

CREATE INDEX idx_graph_job_app_status ON graph_job(app_id, status, created_at);

\echo '✅ 表 graph_job 已创建'

-- ============================================
-- 第3部分：统一的 RLS 策略
-- ============================================

-- 启用 RLS
ALTER TABLE docs ENABLE ROW LEVEL SECURITY;
ALTER TABLE doc_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_mutations ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_search ENABLE ROW LEVEL SECURITY;
ALTER TABLE entity ENABLE ROW LEVEL SECURITY;
ALTER TABLE entity_chunk ENABLE ROW LEVEL SECURITY;
ALTER TABLE entity_edge ENABLE ROW LEVEL SECURITY;
ALTER TABLE entity_summary ENABLE ROW LEVEL SECURITY;
ALTER TABLE graph_job ENABLE ROW LEVEL SECURITY;

\echo '✅ 行级安全已启用'

-- RLS 策略（基于 app_id + classification）
-- 会话变量：app.current_app, app.clearance

-- 1. docs 表 RLS
CREATE POLICY docs_select ON docs FOR SELECT
  USING (
    app_id = current_setting('app.current_app', true)
    AND classification <= COALESCE(NULLIF(current_setting('app.clearance', true), '')::int, -1)
  );

CREATE POLICY docs_insert ON docs FOR INSERT
  WITH CHECK (app_id = current_setting('app.current_app', true));

CREATE POLICY docs_update ON docs FOR UPDATE
  USING (app_id = current_setting('app.current_app', true))
  WITH CHECK (app_id = current_setting('app.current_app', true));

CREATE POLICY docs_delete ON docs FOR DELETE
  USING (app_id = current_setting('app.current_app', true));

-- 2. doc_versions 表 RLS
CREATE POLICY doc_versions_select ON doc_versions FOR SELECT
  USING (
    EXISTS (SELECT 1 FROM docs d 
      WHERE d.doc_id = doc_versions.doc_id
      AND d.app_id = current_setting('app.current_app', true)
      AND d.classification <= COALESCE(NULLIF(current_setting('app.clearance', true), '')::int, -1))
  );

CREATE POLICY doc_versions_insert ON doc_versions FOR INSERT
  WITH CHECK (
    EXISTS (SELECT 1 FROM docs d 
      WHERE d.doc_id = doc_versions.doc_id
      AND d.app_id = current_setting('app.current_app', true))
  );

CREATE POLICY doc_versions_update ON doc_versions FOR UPDATE
  USING (
    EXISTS (SELECT 1 FROM docs d 
      WHERE d.doc_id = doc_versions.doc_id
      AND d.app_id = current_setting('app.current_app', true))
  );

CREATE POLICY doc_versions_delete ON doc_versions FOR DELETE
  USING (
    EXISTS (SELECT 1 FROM docs d 
      WHERE d.doc_id = doc_versions.doc_id
      AND d.app_id = current_setting('app.current_app', true))
  );

-- 3. chunks 表 RLS
CREATE POLICY chunks_select ON chunks FOR SELECT
  USING (
    app_id = current_setting('app.current_app', true)
    AND classification <= COALESCE(NULLIF(current_setting('app.clearance', true), '')::int, -1)
  );

CREATE POLICY chunks_insert ON chunks FOR INSERT
  WITH CHECK (app_id = current_setting('app.current_app', true));

CREATE POLICY chunks_update ON chunks FOR UPDATE
  USING (app_id = current_setting('app.current_app', true))
  WITH CHECK (app_id = current_setting('app.current_app', true));

CREATE POLICY chunks_delete ON chunks FOR DELETE
  USING (app_id = current_setting('app.current_app', true));

-- 4. audit_mutations 表 RLS（所有人可以插入自己的操作）
CREATE POLICY audit_mutations_select ON audit_mutations FOR SELECT
  USING (actor = current_user);

CREATE POLICY audit_mutations_insert ON audit_mutations FOR INSERT
  WITH CHECK (true);

-- 5. audit_search 表 RLS
CREATE POLICY audit_search_select ON audit_search FOR SELECT
  USING (
    app_id = current_setting('app.current_app', true)
    AND clearance <= COALESCE(NULLIF(current_setting('app.clearance', true), '')::int, -1)
  );

CREATE POLICY audit_search_insert ON audit_search FOR INSERT
  WITH CHECK (
    app_id = current_setting('app.current_app', true)
    AND clearance <= COALESCE(NULLIF(current_setting('app.clearance', true), '')::int, -1)
  );

-- 6. entity 表 RLS
CREATE POLICY entity_select ON entity FOR SELECT
  USING (
    app_id = current_setting('app.current_app', true)
    AND classification <= COALESCE(NULLIF(current_setting('app.clearance', true), '')::int, -1)
  );

CREATE POLICY entity_insert ON entity FOR INSERT
  WITH CHECK (app_id = current_setting('app.current_app', true));

CREATE POLICY entity_update ON entity FOR UPDATE
  USING (app_id = current_setting('app.current_app', true))
  WITH CHECK (app_id = current_setting('app.current_app', true));

CREATE POLICY entity_delete ON entity FOR DELETE
  USING (app_id = current_setting('app.current_app', true));

-- 7. entity_chunk 表 RLS
CREATE POLICY entity_chunk_select ON entity_chunk FOR SELECT
  USING (
    app_id = current_setting('app.current_app', true)
    AND classification <= COALESCE(NULLIF(current_setting('app.clearance', true), '')::int, -1)
  );

CREATE POLICY entity_chunk_insert ON entity_chunk FOR INSERT
  WITH CHECK (app_id = current_setting('app.current_app', true));

CREATE POLICY entity_chunk_update ON entity_chunk FOR UPDATE
  USING (app_id = current_setting('app.current_app', true))
  WITH CHECK (app_id = current_setting('app.current_app', true));

CREATE POLICY entity_chunk_delete ON entity_chunk FOR DELETE
  USING (app_id = current_setting('app.current_app', true));

-- 8. entity_edge 表 RLS
CREATE POLICY entity_edge_select ON entity_edge FOR SELECT
  USING (
    app_id = current_setting('app.current_app', true)
    AND classification <= COALESCE(NULLIF(current_setting('app.clearance', true), '')::int, -1)
  );

CREATE POLICY entity_edge_insert ON entity_edge FOR INSERT
  WITH CHECK (app_id = current_setting('app.current_app', true));

CREATE POLICY entity_edge_update ON entity_edge FOR UPDATE
  USING (app_id = current_setting('app.current_app', true))
  WITH CHECK (app_id = current_setting('app.current_app', true));

CREATE POLICY entity_edge_delete ON entity_edge FOR DELETE
  USING (app_id = current_setting('app.current_app', true));

-- 9. entity_summary 表 RLS
CREATE POLICY entity_summary_select ON entity_summary FOR SELECT
  USING (
    app_id = current_setting('app.current_app', true)
    AND classification <= COALESCE(NULLIF(current_setting('app.clearance', true), '')::int, -1)
  );

CREATE POLICY entity_summary_insert ON entity_summary FOR INSERT
  WITH CHECK (app_id = current_setting('app.current_app', true));

CREATE POLICY entity_summary_update ON entity_summary FOR UPDATE
  USING (app_id = current_setting('app.current_app', true))
  WITH CHECK (app_id = current_setting('app.current_app', true));

CREATE POLICY entity_summary_delete ON entity_summary FOR DELETE
  USING (app_id = current_setting('app.current_app', true));

-- 10. graph_job 表 RLS
CREATE POLICY graph_job_select ON graph_job FOR SELECT
  USING (app_id = current_setting('app.current_app', true));

CREATE POLICY graph_job_insert ON graph_job FOR INSERT
  WITH CHECK (app_id = current_setting('app.current_app', true));

CREATE POLICY graph_job_update ON graph_job FOR UPDATE
  USING (app_id = current_setting('app.current_app', true))
  WITH CHECK (app_id = current_setting('app.current_app', true));

CREATE POLICY graph_job_delete ON graph_job FOR DELETE
  USING (app_id = current_setting('app.current_app', true));

\echo '✅ RLS 策略已创建'

-- ============================================
-- 第6部分：视图（可选）
-- ============================================

-- 实体及其所有邻接实体（1-hop）
CREATE OR REPLACE VIEW v_entity_neighborhood AS
SELECT 
  e.entity_id,
  e.app_id,
  e.name as entity_name,
  e.type as entity_type,
  e.confidence,
  ee.dst_entity_id as neighbor_id,
  e2.name as neighbor_name,
  e2.type as neighbor_type,
  ee.edge_type,
  ee.weight
FROM entity e
LEFT JOIN entity_edge ee ON e.entity_id = ee.src_entity_id
LEFT JOIN entity e2 ON ee.dst_entity_id = e2.entity_id
WHERE e.app_id = current_setting('rls.app_id')
  AND (e2.app_id IS NULL OR e2.app_id = current_setting('rls.app_id'));

\echo '✅ 视图 v_entity_neighborhood 已创建'

-- 统计视图：实体统计
CREATE OR REPLACE VIEW v_entity_stats AS
SELECT 
  app_id,
  COUNT(DISTINCT entity_id) as total_entities,
  COUNT(DISTINCT type) as unique_types,
  AVG(occurrence_count) as avg_mention_count,
  COUNT(CASE WHEN confidence = 'high' THEN 1 END) as high_conf_count,
  COUNT(CASE WHEN confidence = 'medium' THEN 1 END) as medium_conf_count,
  COUNT(CASE WHEN confidence = 'low' THEN 1 END) as low_conf_count
FROM entity
WHERE app_id = current_setting('rls.app_id')
GROUP BY app_id;

\echo '✅ 视图 v_entity_stats 已创建'

-- 统计视图：关系统计
CREATE OR REPLACE VIEW v_edge_stats AS
SELECT 
  app_id,
  COUNT(*) as total_edges,
  COUNT(DISTINCT edge_type) as unique_edge_types,
  AVG(weight) as avg_weight,
  COUNT(CASE WHEN confidence = 'high' THEN 1 END) as high_conf_count,
  COUNT(CASE WHEN confidence = 'medium' THEN 1 END) as medium_conf_count,
  COUNT(CASE WHEN confidence = 'low' THEN 1 END) as low_conf_count
FROM entity_edge
WHERE app_id = current_setting('rls.app_id')
GROUP BY app_id;

\echo '✅ 视图 v_edge_stats 已创建'

-- ============================================
-- 第7部分：触发器（自动更新时间戳）
-- ============================================

CREATE OR REPLACE FUNCTION update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  NEW.updated_by = current_user;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER entity_update_timestamp
  BEFORE UPDATE ON entity
  FOR EACH ROW
  EXECUTE FUNCTION update_timestamp();

CREATE TRIGGER entity_edge_update_timestamp
  BEFORE UPDATE ON entity_edge
  FOR EACH ROW
  EXECUTE FUNCTION update_timestamp();

CREATE TRIGGER entity_summary_update_timestamp
  BEFORE UPDATE ON entity_summary
  FOR EACH ROW
  EXECUTE FUNCTION update_timestamp();

\echo '✅ 时间戳自动更新触发器已创建'

-- ============================================
-- 第8部分：审计触发器
-- ============================================

-- 创建审计函数
CREATE OR REPLACE FUNCTION audit_entity_mutation()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO audit_mutations (actor, action, table_name, row_pk, request_id, old_data, new_data)
  VALUES (
    current_user,
    TG_OP,
    'entity',
    CASE WHEN TG_OP = 'DELETE' THEN OLD.entity_id::text ELSE NEW.entity_id::text END,
    current_setting('app.request_id', true),
    CASE WHEN TG_OP = 'DELETE' THEN row_to_json(OLD) ELSE row_to_json(OLD) END,
    CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE row_to_json(NEW) END
  );
  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION audit_entity_chunk_mutation()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO audit_mutations (actor, action, table_name, row_pk, request_id, old_data, new_data)
  VALUES (
    current_user,
    TG_OP,
    'entity_chunk',
    CASE WHEN TG_OP = 'DELETE' THEN OLD.chunk_id::text ELSE NEW.chunk_id::text END,
    current_setting('app.request_id', true),
    CASE WHEN TG_OP = 'DELETE' THEN row_to_json(OLD) ELSE row_to_json(OLD) END,
    CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE row_to_json(NEW) END
  );
  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION audit_entity_edge_mutation()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO audit_mutations (actor, action, table_name, row_pk, request_id, old_data, new_data)
  VALUES (
    current_user,
    TG_OP,
    'entity_edge',
    CASE
      WHEN TG_OP = 'DELETE' THEN (OLD.src_entity_id::text || '->' || OLD.dst_entity_id::text || ':' || OLD.edge_type)
      ELSE (NEW.src_entity_id::text || '->' || NEW.dst_entity_id::text || ':' || NEW.edge_type)
    END,
    current_setting('app.request_id', true),
    CASE WHEN TG_OP = 'DELETE' THEN row_to_json(OLD) ELSE row_to_json(OLD) END,
    CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE row_to_json(NEW) END
  );
  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION audit_entity_summary_mutation()
RETURNS TRIGGER AS $$
BEGIN
  INSERT INTO audit_mutations (actor, action, table_name, row_pk, request_id, old_data, new_data)
  VALUES (
    current_user,
    TG_OP,
    'entity_summary',
    CASE WHEN TG_OP = 'DELETE' THEN OLD.entity_id::text ELSE NEW.entity_id::text END,
    current_setting('app.request_id', true),
    CASE WHEN TG_OP = 'DELETE' THEN row_to_json(OLD) ELSE row_to_json(OLD) END,
    CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE row_to_json(NEW) END
  );
  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

-- 创建触发器
DROP TRIGGER IF EXISTS entity_audit_trigger ON entity;
DROP TRIGGER IF EXISTS entity_chunk_audit_trigger ON entity_chunk;
DROP TRIGGER IF EXISTS entity_edge_audit_trigger ON entity_edge;
DROP TRIGGER IF EXISTS entity_summary_audit_trigger ON entity_summary;

CREATE TRIGGER entity_audit_trigger
AFTER INSERT OR UPDATE OR DELETE ON entity
FOR EACH ROW
EXECUTE FUNCTION audit_entity_mutation();

CREATE TRIGGER entity_chunk_audit_trigger
AFTER INSERT OR UPDATE OR DELETE ON entity_chunk
FOR EACH ROW
EXECUTE FUNCTION audit_entity_chunk_mutation();

CREATE TRIGGER entity_edge_audit_trigger
AFTER INSERT OR UPDATE OR DELETE ON entity_edge
FOR EACH ROW
EXECUTE FUNCTION audit_entity_edge_mutation();

CREATE TRIGGER entity_summary_audit_trigger
AFTER INSERT OR UPDATE OR DELETE ON entity_summary
FOR EACH ROW
EXECUTE FUNCTION audit_entity_summary_mutation();

\echo '✅ 审计触发器已创建'

-- ============================================
-- 完成
-- ============================================

\echo ''
\echo '╔════════════════════════════════════════════════╗'
\echo '║   GraphRAG 统一数据库初始化完成！             ║'
\echo '╠════════════════════════════════════════════════╣'
\echo '║ 包含 RAG 原有表 (5 个)                        ║'
\echo '║ 包含 Graph 新增表 (4 个)                      ║'
\echo '║ RLS 策略已启用 (基于 app_id + classification) ║'
\echo '║ 审计系统已启用 (记录所有变更)                ║'
\echo '║ 统一用户角色: graphrag_owner/writer/reader    ║'
\echo '╚════════════════════════════════════════════════╝'
\echo ''

-- 显示创建统计
\echo '📊 数据库统计信息:'
SELECT 
  schemaname,
  COUNT(*) as 对象数
FROM pg_tables
WHERE schemaname = 'public'
GROUP BY schemaname;
