#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/repro_dead.sh <session-id>
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
PIDS_FILE="$SESSION_ROOT/artifacts/autopilot/pids.txt"
LOG_DIR="$SESSION_ROOT/artifacts/autopilot"

echo "=== repro_dead session=$session ts=$(date '+%Y-%m-%d %H:%M:%S') ==="

"$MAIN/scripts/autopilot.sh" stop "$session" || true
"$MAIN/scripts/autopilot.sh" start "$session" 2 --model gpt-5.2-codex

for i in 1 2 3; do
  echo
  echo "=== status round=$i ts=$(date '+%Y-%m-%d %H:%M:%S') ==="
  "$MAIN/scripts/autopilot.sh" status "$session" || true
  for role in router lead builder-a reviewer tester; do
    log="$LOG_DIR/$role.log"
    echo "--- tail $role.log (last 40) ---"
    if [[ -f "$log" ]]; then
      tail -n 40 "$log" || true
    else
      echo "(missing) $log"
    fi
  done
  sleep 2
done

echo
echo "=== pids.txt ==="
if [[ -f "$PIDS_FILE" ]]; then
  cat "$PIDS_FILE"
else
  echo "(missing) $PIDS_FILE"
fi

echo
echo "=== ps for pids ==="
if [[ -f "$PIDS_FILE" ]]; then
  while read -r role pid; do
    [[ -n "${pid:-}" ]] || continue
    echo "--- role=$role pid=$pid ---"
    ps -o pid,ppid,pgid,sid,tty,stat,etime,command -p "$pid" 2>/dev/null || true
  done <"$PIDS_FILE"
fi
