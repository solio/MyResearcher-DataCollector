#!/bin/bash
# Double-click on macOS, or pass 36/43 and optional --resume from a terminal.
set -eu
task_root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$task_root"
for task_python in "$task_root/.venv/bin/python" /opt/homebrew/anaconda3/bin/python "$(command -v python3 || true)"; do
  if [ -n "$task_python" ] && [ -x "$task_python" ] && "$task_python" -c 'import playwright.sync_api' >/dev/null 2>&1; then
    exec "$task_python" "$task_root/scripts/ops/verify_http_backfill.py" "$@"
  fi
done
echo '缺少浏览器依赖，请在项目 Python 环境执行：python -m pip install -e ".[browser]"'
exit 1
