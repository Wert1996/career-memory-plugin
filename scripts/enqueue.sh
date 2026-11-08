#!/bin/bash
# Stop hook. Runs after every Claude reply, so it must stay fast:
# record the transcript path, and start the worker in the background if a run is due.

# Never capture the worker's own `claude -p` calls.
[ -n "$CAREER_MEMORY_WORKER" ] && exit 0

CM_HOME="${CAREER_MEMORY_HOME:-$HOME/.career-memory}"
# Not opted in (no /career-memory:setup yet): capture nothing.
[ -f "$CM_HOME/config.json" ] || exit 0

# Hook input is one JSON object on stdin; store it as one line.
input=$(cat | tr -d '\n')
[ -n "$input" ] && printf '%s\n' "$input" >> "$CM_HOME/queue.jsonl"

now=$(date +%s)
next=$(cat "$CM_HOME/next_run" 2>/dev/null || echo 0)
case "$next" in ''|*[!0-9]*) next=0 ;; esac
if [ "$now" -ge "$next" ]; then
  ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
  # Detach fully so the hook returns immediately.
  ( CAREER_MEMORY_WORKER=1 nohup python3 "$ROOT/worker/cm.py" run \
      </dev/null >>"$CM_HOME/logs/worker.log" 2>&1 & ) 
fi
exit 0
