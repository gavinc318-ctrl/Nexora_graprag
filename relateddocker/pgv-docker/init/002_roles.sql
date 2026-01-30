-- 检索只读账号
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_reader') THEN
    CREATE ROLE rag_reader LOGIN PASSWORD 'CHANGE_ME_READER';
  END IF;
END $$;

-- 写入账号（入库/更新）
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rag_writer') THEN
    CREATE ROLE rag_writer LOGIN PASSWORD 'CHANGE_ME_WRITER';
  END IF;
END $$;

GRANT CONNECT ON DATABASE rag TO rag_reader, rag_writer;
GRANT USAGE ON SCHEMA public TO rag_reader, rag_writer;

-- reader：只能 SELECT + 写审计（检索审计通常由应用写）
GRANT SELECT ON docs, doc_versions, chunks TO rag_reader;
GRANT INSERT ON audit_search TO rag_reader;

-- writer：写 docs/versions/chunks + 审计
GRANT SELECT, INSERT, UPDATE , DELETE ON docs, doc_versions, chunks TO rag_writer;
GRANT INSERT ON audit_search, audit_mutations TO rag_writer;

-- 序列权限（audit_* bigserial）
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO rag_reader, rag_writer;
