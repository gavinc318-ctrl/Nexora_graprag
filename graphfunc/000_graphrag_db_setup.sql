-- ============================================
-- GraphRAG 数据库初始化脚本
-- 创建独立数据库、角色和权限
-- 
-- 执行：psql -h 10.55.223.100 -U rag_admin -d rag -f 000_graphrag_db_setup.sql
-- ============================================

-- 创建新库（如果不存在）
CREATE DATABASE graphrag_db
  OWNER rag_admin
  ENCODING UTF8
  TEMPLATE template0
  LC_COLLATE 'en_US.UTF-8'
  LC_CTYPE 'en_US.UTF-8';

-- 输出提示
\echo '✅ 数据库 graphrag_db 已创建'

-- ============================================
-- 第2部分：创建专用角色
-- ============================================

-- 库所有者（管理权限）
DO $$
BEGIN
  CREATE ROLE graphrag_owner WITH
    LOGIN
    PASSWORD 'Nexora@123!'
    CREATEDB
    CREATEROLE
    INHERIT;
  RAISE NOTICE '✅ 角色 graphrag_owner 已创建';
EXCEPTION WHEN duplicate_object THEN
  RAISE NOTICE '⚠️  角色 graphrag_owner 已存在';
END
$$;

-- 数据写入用户
DO $$
BEGIN
  CREATE ROLE graphrag_writer WITH
    LOGIN
    PASSWORD 'Nexora@123!'
    NOCREATEDB
    NOCREATEROLE
    INHERIT;
  RAISE NOTICE '✅ 角色 graphrag_writer 已创建';
EXCEPTION WHEN duplicate_object THEN
  RAISE NOTICE '⚠️  角色 graphrag_writer 已存在';
END
$$;

-- 只读查询用户
DO $$
BEGIN
  CREATE ROLE graphrag_reader WITH
    LOGIN
    PASSWORD 'Nexora@123!'
    NOCREATEDB
    NOCREATEROLE
    INHERIT;
  RAISE NOTICE '✅ 角色 graphrag_reader 已创建';
EXCEPTION WHEN duplicate_object THEN
  RAISE NOTICE '⚠️  角色 graphrag_reader 已存在';
END
$$;

-- ============================================
-- 第3部分：修改库和 schema 所有者
-- ============================================

-- 修改库所有者
ALTER DATABASE graphrag_db OWNER TO graphrag_owner;
\echo '✅ graphrag_db 所有者已修改为 graphrag_owner'

-- 连接到新库
\c graphrag_db

-- 修改 public schema 所有者
ALTER SCHEMA public OWNER TO graphrag_owner;
\echo '✅ public schema 所有者已修改'

-- ============================================
-- 第4部分：授予权限（在 graphrag_db 库内执行）
-- ============================================

-- 给 graphrag_writer 写入权限
GRANT USAGE ON SCHEMA public TO graphrag_writer;
GRANT CREATE ON SCHEMA public TO graphrag_writer;

-- 给 graphrag_reader 只读权限
GRANT USAGE ON SCHEMA public TO graphrag_reader;

-- 给 graphrag_owner 完全权限
GRANT USAGE, CREATE ON SCHEMA public TO graphrag_owner;

\echo '✅ Schema 权限已授予'

-- ============================================
-- 第5部分：设置默认权限（新表自动继承）
-- ============================================

-- 新表默认权限
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT ALL PRIVILEGES ON TABLES TO graphrag_owner;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT ALL PRIVILEGES ON TABLES TO graphrag_writer;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT ON TABLES TO graphrag_reader;

-- 新序列默认权限
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT ALL PRIVILEGES ON SEQUENCES TO graphrag_owner;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT ALL PRIVILEGES ON SEQUENCES TO graphrag_writer;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE ON SEQUENCES TO graphrag_reader;

\echo '✅ 默认权限已设置'

-- ============================================
-- 第6部分：授予已存在对象的权限（如有）
-- ============================================

-- 授予表权限
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO graphrag_owner;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO graphrag_writer;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO graphrag_reader;

-- 授予序列权限
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO graphrag_owner;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO graphrag_writer;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO graphrag_reader;

\echo '✅ 现有对象权限已授予'

-- ============================================
-- 完成
-- ============================================

\echo ''
\echo '╔════════════════════════════════════════════════╗'
\echo '║   GraphRAG 数据库初始化完成！                  ║'
\echo '╠════════════════════════════════════════════════╣'
\echo '║ 库名:          graphrag_db                     ║'
\echo '║ 所有者:        graphrag_owner                  ║'
\echo '║ 写入用户:      graphrag_writer                 ║'
\echo '║ 读取用户:      graphrag_reader                 ║'
\echo '║                                                ║'
\echo '║ 下一步:                                        ║'
\echo '║ 1. 修改 000_graphrag_db_setup.sql 中的密码   ║'
\echo '║ 2. 运行 001_graphrag_extensions.sql            ║'
\echo '║ 3. 运行 002_graphrag_schema.sql                ║'
\echo '╚════════════════════════════════════════════════╝'
\echo ''
