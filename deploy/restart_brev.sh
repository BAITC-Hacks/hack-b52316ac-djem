#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
project_dir="$(pwd -P)"
mkdir -p logs
if [[ -f logs/app.pid ]]; then
  app_pid="$(cat logs/app.pid)"
  if [[ "$app_pid" =~ ^[0-9]+$ ]] && kill -0 "$app_pid" 2>/dev/null; then
    if [[ "$(readlink -f "/proc/$app_pid/cwd")" != "$project_dir" ]] || ! tr '\0' ' ' < "/proc/$app_pid/cmdline" | grep -q 'run.py'; then
      echo 'PID belongs to another process. Check logs/app.pid and ss -ltnp before restarting.' >&2
      exit 1
    fi
    kill "$app_pid"
    for attempt in {1..20}; do
      if ! kill -0 "$app_pid" 2>/dev/null; then break; fi
      sleep 0.5
    done
  fi
fi
.venv/bin/python - <<'PY'
import socket
with socket.socket() as s:
    try:s.bind(('0.0.0.0',8080))
    except OSError:raise SystemExit('Port 8080 is occupied. Run ss -ltnp and check the existing service.')
PY
nohup env HOST=0.0.0.0 PORT=8080 .venv/bin/python run.py > logs/app.log 2>&1 &
echo $! > logs/app.pid
for attempt in {1..20}; do
  if curl -fsS http://127.0.0.1:8080/health; then
    echo
    echo 'Site started on port 8080.'
    exit 0
  fi
  sleep 0.5
done
tail -n 40 logs/app.log
exit 1
