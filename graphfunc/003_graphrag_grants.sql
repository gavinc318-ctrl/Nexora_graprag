-- ============================================
-- GraphRAG 权限补丁
--
-- 背景：000_graphrag_db_setup.sql 在建表【之前】执行，其
--   - GRANT ... ON ALL TABLES  作用于当时还不存在的表 → 无效
--   - ALTER DEFAULT PRIVILEGES 未加 FOR ROLE，只对执行者(rag_admin)生效，
--     而表由 graphrag_owner 在 002 中创建 → 无效
-- 结果：graphrag_writer / graphrag_reader 无任何表权限。
--
-- 本脚本在 002 之后执行，需以 graphrag_owner 身份运行。
-- 执行：psql -U graphrag_owner -d graphrag_db -f 003_graphrag_grants.sql
-- ============================================

-- 1) 对已存在的表/序列/视图补授权
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO graphrag_writer;
GRANT SELECT                         ON ALL TABLES IN SCHEMA public TO graphrag_reader;
GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO graphrag_writer;
GRANT USAGE, SELECT          ON ALL SEQUENCES IN SCHEMA public TO graphrag_reader;

-- 2) 让 graphrag_owner 今后新建的对象自动带上这些权限
ALTER DEFAULT PRIVILEGES FOR ROLE graphrag_owner IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO graphrag_writer;
ALTER DEFAULT PRIVILEGES FOR ROLE graphrag_owner IN SCHEMA public
  GRANT SELECT ON TABLES TO graphrag_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE graphrag_owner IN SCHEMA public
  GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO graphrag_writer;
ALTER DEFAULT PRIVILEGES FOR ROLE graphrag_owner IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO graphrag_reader;

\echo '✅ graphrag_writer / graphrag_reader 权限已补齐'
