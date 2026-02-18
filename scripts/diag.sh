#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/diag.sh <session-id>
EOF
}

session="${1:-}"
if [[ -z "${session:-}" ]]; then
  usage
  exit 2
fi

TOP="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "${TOP:-}" ]]; then
  echo "Not in a git repo." >&2
  exit 1
fi

resolve_main_from_common_dir() {
  local top="$1"
  local common="$2"
  if [[ -z "${common:-}" ]]; then
    echo "$top"
    return
  fi
  if [[ "$common" != /* ]]; then
    common="$top/$common"
  fi
  if [[ "$common" == */.git/worktrees/* ]]; then
    dirname "$(dirname "$(dirname "$common")")"
    return
  fi
  if [[ "$common" == */.git ]]; then
    dirname "$common"
    return
  fi
  echo "$top"
}

COMMON_DIR="$(git -C "$TOP" rev-parse --git-common-dir 2>/dev/null || true)"
MAIN="$(resolve_main_from_common_dir "$TOP" "$COMMON_DIR")"
if [[ -z "${MAIN:-}" ]]; then MAIN="$TOP"; fi

SESSION_ROOT="$MAIN/sessions/$session"
LOG_DIR="$SESSION_ROOT/artifacts/autopilot"
OUTBOX_DIR="$SESSION_ROOT/bus/outbox"
TASKS_JSON="$SESSION_ROOT/state/tasks/tasks.json"
LOCKS_DIR="$SESSION_ROOT/artifacts/locks"
PROCESSING_DIR="$SESSION_ROOT/state/processing"

if [[ ! -d "$SESSION_ROOT" ]]; then
  echo "Session not found: $SESSION_ROOT" >&2
  exit 1
fi

echo "=== diag session=$session root=$SESSION_ROOT ==="
echo

echo "== autopilot status =="
"$MAIN/scripts/autopilot.sh" status "$session" || true
echo

echo "== recent outbox (20) =="
if [[ -d "$OUTBOX_DIR" ]]; then
  ls -1t "$OUTBOX_DIR" 2>/dev/null | head -n 20
else
  echo "(missing) $OUTBOX_DIR"
fi
echo

echo "== log tails (40 lines each) =="
for role in router lead builder-a reviewer tester; do
  log="$LOG_DIR/$role.log"
  echo "--- $role.log ---"
  if [[ -f "$log" ]]; then
    tail -n 40 "$log" || true
  else
    echo "(missing) $log"
  fi
done
echo

echo "== task board counts =="
python3 - "$TASKS_JSON" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
counts = {"pending": 0, "in_progress": 0, "completed": 0, "failed": 0}
if not path.exists():
    print(f"tasks_json_missing={path}")
    print("pending=0 in_progress=0 completed=0 failed=0")
    raise SystemExit(0)

try:
    data = json.loads(path.read_text(encoding="utf-8"))
except Exception as e:
    print(f"tasks_json_read_error path={path} err={e}")
    print("pending=0 in_progress=0 completed=0 failed=0")
    raise SystemExit(0)

tasks = data.get("tasks", [])
if isinstance(tasks, list):
    for t in tasks:
        if not isinstance(t, dict):
            continue
        st = str(t.get("status", "")).strip()
        if st in counts:
            counts[st] += 1

print(
    f"pending={counts['pending']} in_progress={counts['in_progress']} "
    f"completed={counts['completed']} failed={counts['failed']}"
)
PY
echo

echo "== artifacts/locks (max 50) =="
if [[ -d "$LOCKS_DIR" ]]; then
  find "$LOCKS_DIR" -maxdepth 3 -print | sed -n '1,50p'
else
  echo "(missing) $LOCKS_DIR"
fi
echo

echo "== state/processing (max 50) =="
if [[ -d "$PROCESSING_DIR" ]]; then
  find "$PROCESSING_DIR" -maxdepth 3 -print | sed -n '1,50p'
else
  echo "(missing) $PROCESSING_DIR"
fi
