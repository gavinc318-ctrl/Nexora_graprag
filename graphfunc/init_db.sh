#!/bin/bash

# GraphRAG 数据库初始化脚本

set -e

HOST="10.55.223.100"
PORT=5432
RAG_USER="rag_admin"
RAG_PASS="CHANGE_ME_STRONG"
OWNER_USER="graphrag_owner"
OWNER_PASS="Nexora@123!"

echo "======================================"
echo "GraphRAG 统一数据库初始化"
echo "======================================"

# Step 1
echo ""
echo "[1/3] 执行 000_graphrag_db_setup.sql..."
export PGPASSWORD="$RAG_PASS"
psql -h "$HOST" -p "$PORT" -U "$RAG_USER" -d "rag" -q -f "000_graphrag_db_setup.sql" || {
  echo "❌ 第一步失败"
  exit 1
}
echo "✅ 完成"

# Step 2
echo ""
echo "[2/3] 执行 001_graphrag_extensions.sql..."
export PGPASSWORD="$OWNER_PASS"
psql -h "$HOST" -p "$PORT" -U "$OWNER_USER" -d "graphrag_db" -q -f "001_graphrag_extensions.sql" || {
  echo "❌ 第二步失败"
  exit 1
}
echo "✅ 完成"

# Step 3
echo ""
echo "[3/3] 执行 002_graphrag_schema.sql..."
psql -h "$HOST" -p "$PORT" -U "$OWNER_USER" -d "graphrag_db" -q -f "002_graphrag_schema.sql" || {
  echo "❌ 第三步失败"
  exit 1
}
echo "✅ 完成"

echo ""
echo "======================================"
echo "✅ 所有步骤完成！"
echo "======================================"
echo ""
echo "验证："
export PGPASSWORD="$OWNER_PASS"
psql -h "$HOST" -p "$PORT" -U "$OWNER_USER" -d "graphrag_db" -t -c "SELECT COUNT(*) as table_count FROM information_schema.tables WHERE table_schema = 'public';" 
echo ""
