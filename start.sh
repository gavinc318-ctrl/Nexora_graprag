#!/usr/bin/env bash
# 启动 Nexora 应用层（API + 4 个 Gradio UI）
# 前置：基础服务需已运行，见 ./services.sh status
set -euo pipefail
cd "$(dirname "$0")"

NEXORA_ENV="${NEXORA_ENV:-$HOME/opt/mamba/envs/nexora}"
PY="$NEXORA_ENV/bin/python"

# cv2(opencv-contrib-python) 需要 conda-forge 提供的 libGL.so.1
export LD_LIBRARY_PATH="$NEXORA_ENV/lib:${LD_LIBRARY_PATH:-}"
export DISABLE_MODEL_SOURCE_CHECK=True

trap 'kill 0' SIGINT SIGTERM

"$PY" -m uvicorn api_server:app --host 0.0.0.0 --port 19000 &   # API
"$PY" gradio_ui.py      &   # 文件导入      :7867
"$PY" datamng_gr.py     &   # 数据管理/校准 :7861
"$PY" user_query_ui.py  &   # 用户问答      :7862
"$PY" graphmng_gr.py    &   # Graph 管理    :7863

wait -n
