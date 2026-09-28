#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -x "$project_dir/.venv/bin/python" ]]; then
  printf '未找到项目虚拟环境，请先运行 bash setup-dgx.sh\n' >&2
  exit 1
fi
export SKILLPULSE_OUTBOX_DIR="${SKILLPULSE_OUTBOX_DIR:-$project_dir/workspace/outbox}"
exec "$project_dir/.venv/bin/python" -m uvicorn app:app \
  --app-dir "$project_dir/backend" --host 127.0.0.1 --port "${SKILLPULSE_PORT:-8000}"
