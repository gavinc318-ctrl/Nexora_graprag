#!/usr/bin/env bash
set -euo pipefail

trap 'kill 0' SIGINT SIGTERM

python3 -m uvicorn api_server:app --host 0.0.0.0 --port 19000 &
python3 gradio_ui.py &
python3 datamng_gr.py &
python3 user_query_ui.py &

wait -n
