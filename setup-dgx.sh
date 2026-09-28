#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 -m venv "$project_dir/.venv"
"$project_dir/.venv/bin/python" -m pip install --no-cache-dir -r "$project_dir/backend/requirements.txt"
printf '依赖已安装。运行：bash "%s/start-dgx.sh"\n' "$project_dir"
