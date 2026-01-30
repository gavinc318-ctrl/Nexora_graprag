-- ============================================
-- GraphRAG 数据库扩展安装
--
-- 执行：psql -h 10.55.223.100 -U graphrag_owner -d graphrag_db -f 001_graphrag_extensions.sql
-- ============================================

-- 安装必要扩展

-- 向量存储扩展（pgvector - 用于 entity embedding）
CREATE EXTENSION IF NOT EXISTS vector;
\echo '✅ 扩展 vector 已安装'

-- UUID 和加密函数
CREATE EXTENSION IF NOT EXISTS pgcrypto;
\echo '✅ 扩展 pgcrypto 已安装'

-- 文本搜索和模糊匹配（实体名称模糊搜索）
CREATE EXTENSION IF NOT EXISTS pg_trgm;
\echo '✅ 扩展 pg_trgm 已安装'

-- UUID 生成函数（备用）
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
\echo '✅ 扩展 uuid-ossp 已安装'

-- 可选：JSON Web Token（用于安全认证；多数环境默认没有，缺失时跳过）
DO $$
BEGIN
  EXECUTE 'CREATE EXTENSION IF NOT EXISTS pgjwt';
  RAISE NOTICE '✅ 扩展 pgjwt 已安装';
EXCEPTION
  WHEN undefined_file THEN
    RAISE NOTICE '⚠️  可选扩展 pgjwt 不存在（已跳过）';
  WHEN insufficient_privilege THEN
    RAISE NOTICE '⚠️  无权限安装可选扩展 pgjwt（已跳过）';
END
$$;


-- ============================================
-- 验证扩展安装
-- ============================================

\echo ''
\echo '现有扩展列表：'
SELECT 
  extname as 扩展名,
  extversion as 版本,
  extnamespace::regnamespace as schema
FROM pg_extension
WHERE extname IN ('vector', 'pgcrypto', 'pg_trgm', 'uuid-ossp', 'pgjwt')
ORDER BY extname;

\echo ''
\echo '✅ 所有必要扩展已安装！'
