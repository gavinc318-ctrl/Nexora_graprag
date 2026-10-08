#!/usr/bin/env bash
# 单独启动 Nexora API 服务（:19000），供前端 dev proxy 使用
set -euo pipefail
cd "$(dirname "$0")"

NEXORA_ENV="${NEXORA_ENV:-$HOME/opt/mamba/envs/nexora}"
export LD_LIBRARY_PATH="$NEXORA_ENV/lib:${LD_LIBRARY_PATH:-}"
export DISABLE_MODEL_SOURCE_CHECK=True
set -a; . ./.env; set +a

exec "$NEXORA_ENV/bin/python" -m uvicorn api_server:app --host 0.0.0.0 --port 19000
