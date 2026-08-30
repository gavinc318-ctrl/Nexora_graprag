#!/usr/bin/env bash
# =====================================================================
# Nexora 基础服务管理（无 root 用户态部署）
# 用法: ./services.sh {start|stop|status|logs <name>}
# 组件均由 ~/opt 下的原生二进制提供，不使用 Docker
# =====================================================================
set -uo pipefail

OPT="$HOME/opt"
ENVD="$OPT/mamba/envs/nexora"
LOGS="$OPT/logs"
REPO="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$LOGS"

# 凭据一律从 .env 读取，绝不写死在本脚本里（本仓库是公开仓库）。
# .env 已在 .gitignore 中。
if [ -f "$REPO/.env" ]; then
  set -a; . "$REPO/.env"; set +a
fi
MINIO_USER="${MINIO_ACCESS_KEY:-}"
MINIO_PASS="${MINIO_SECRET_KEY:-}"
REDIS_PASS="${REDIS_PASSWORD:-}"
need() {
  [ -n "$2" ] || { echo "❌ 缺少 $1，请在 $REPO/.env 中设置后重试"; exit 1; }
}

listening() { ss -lnt 2>/dev/null | grep -q ":$1 "; }
wait_port() { local p=$1 n=0; until listening "$p" || [ $n -ge ${2:-40} ]; do sleep 2; n=$((n+1)); done; }

start_all() {
  # --- PostgreSQL 16 + pgvector ---
  if listening 5432; then echo "  postgres  已在运行"; else
    "$ENVD/bin/pg_ctl" -D "$OPT/pgdata" -l "$LOGS/postgres.log" -w start >/dev/null 2>&1 \
      && echo "  postgres  已启动 :5432" || echo "  postgres  启动失败，见 $LOGS/postgres.log"
  fi

  # --- MinIO ---
  if listening 9000; then echo "  minio     已在运行"; else
    need MINIO_ACCESS_KEY "$MINIO_USER"; need MINIO_SECRET_KEY "$MINIO_PASS"
    MINIO_ROOT_USER="$MINIO_USER" MINIO_ROOT_PASSWORD="$MINIO_PASS" \
      setsid nohup "$OPT/bin/minio" server "$OPT/minio-data" \
        --address 0.0.0.0:9000 --console-address 0.0.0.0:9001 \
        > "$LOGS/minio.log" 2>&1 < /dev/null & disown
    wait_port 9000; echo "  minio     已启动 :9000 (控制台 :9001)"
  fi

  # --- Redis ---
  if listening 6379; then echo "  redis     已在运行"; else
    need REDIS_PASSWORD "$REDIS_PASS"
    setsid nohup "$ENVD/bin/redis-server" --port 6379 --bind 127.0.0.1 \
      --dir "$OPT/redis-data" --appendonly yes --requirepass "$REDIS_PASS" \
      > "$LOGS/redis.log" 2>&1 < /dev/null & disown
    wait_port 6379; echo "  redis     已启动 :6379"
  fi

  # --- Ollama (CPU, embedding) ---
  if listening 11434; then echo "  ollama    已在运行"; else
    OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS="$OPT/ollama-home/models" \
      setsid nohup "$OPT/ollama/bin/ollama" serve \
        > "$LOGS/ollama.log" 2>&1 < /dev/null & disown
    wait_port 11434; echo "  ollama    已启动 :11434"
  fi

  # --- Rerank (bge-reranker-base, CPU) ---
  if listening 18010; then echo "  rerank    已在运行"; else
    ( cd "$REPO/relateddocker/rerank-docker" && \
      HF_HOME="$OPT/hf-cache" SENTENCE_TRANSFORMERS_HOME="$OPT/hf-cache" \
      setsid nohup "$ENVD/bin/uvicorn" app:app --host 127.0.0.1 --port 18010 \
        > "$LOGS/rerank.log" 2>&1 < /dev/null & disown )
    wait_port 18010 60; echo "  rerank    已启动 :18010"
  fi

  # --- OCR (PaddleOCR CPU) ---
  # 注意：单 worker 峰值 RSS 约 8GB，本机 15GB 内存下 -w 必须为 1，否则 OOM
  if listening 18000; then echo "  ocr       已在运行"; else
    ( cd "$REPO/relateddocker/ocr" && \
      USE_GPU=0 DISABLE_MODEL_SOURCE_CHECK=True \
      LD_LIBRARY_PATH="$ENVD/lib:${LD_LIBRARY_PATH:-}" \
      setsid nohup "$ENVD/bin/gunicorn" ocr_service:app \
        -k uvicorn.workers.UvicornWorker -w 1 -b 127.0.0.1:18000 --timeout 600 \
        > "$LOGS/ocr.log" 2>&1 < /dev/null & disown )
    wait_port 18000 60; echo "  ocr       已启动 :18000"
  fi
}

stop_all() {
  "$ENVD/bin/pg_ctl" -D "$OPT/pgdata" -m fast stop >/dev/null 2>&1 && echo "  postgres  已停止"
  for pat in "minio server" "redis-server" "ollama serve" "uvicorn app:app" "ocr_service:app"; do
    pids=$(pgrep -f -- "$pat" | grep -v "^$$\$" || true)
    [ -n "$pids" ] && kill $pids 2>/dev/null && echo "  已停止: $pat"
  done
  true
}

status_all() {
  printf "  %-10s %-8s %s\n" 服务 端口 状态
  check() { printf "  %-10s %-8s %s\n" "$1" "$2" "$(listening "$2" && echo '● 运行中' || echo '○ 未运行')"; }
  check postgres 5432; check minio 9000; check redis 6379
  check ollama 11434; check ocr 18000; check rerank 18010
  echo
  printf "  %-10s %-8s %s\n" api 19000 "$(listening 19000 && echo '● 运行中' || echo '○ 未运行')"
  for p in 7867 7861 7862 7863; do
    case $p in 7867) n=导入UI;; 7861) n=数据UI;; 7862) n=问答UI;; 7863) n=图谱UI;; esac
    printf "  %-10s %-8s %s\n" "$n" "$p" "$(listening $p && echo '● 运行中' || echo '○ 未运行')"
  done
}

case "${1:-status}" in
  start)  echo "启动基础服务..."; start_all ;;
  stop)   echo "停止基础服务..."; stop_all ;;
  status) status_all ;;
  logs)   tail -f "$LOGS/${2:-postgres}.log" ;;
  *) echo "用法: $0 {start|stop|status|logs <postgres|minio|redis|ollama|rerank|ocr>}"; exit 1 ;;
esac
