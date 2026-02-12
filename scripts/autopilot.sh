#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/autopilot.sh start <session-id> [poll_seconds]
  ./scripts/autopilot.sh stop <session-id>
  ./scripts/autopilot.sh status <session-id>

Notes:
  - Starts a router daemon plus 1 daemon per role.
  - Workers run `codex exec` when triggered by bus inbox updates.
  - Workers serialize execution via a global lock under sessions/<id>/artifacts/locks/.
EOF
}

cmd="${1:-}"
session="${2:-}"
poll="${3:-2}"

if [[ -z "${cmd:-}" || -z "${session:-}" ]]; then
  usage
  exit 2
fi

TOP="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "${TOP:-}" ]]; then
  echo "Not in a git repo." >&2
  exit 1
fi

MAIN="$(git -C "$TOP" worktree list --porcelain | awk '/^worktree /{print $2; exit}')"
if [[ -z "${MAIN:-}" ]]; then
  MAIN="$TOP"
fi

SESSION_ROOT="$MAIN/sessions/$session"
PIDS_DIR="$SESSION_ROOT/artifacts/autopilot"
PIDS_FILE="$PIDS_DIR/pids.txt"

mkdir -p "$PIDS_DIR"

roles() {
  # Derive roles from the session directory.
  if [[ ! -d "$SESSION_ROOT/roles" ]]; then
    return 0
  fi
  for d in "$SESSION_ROOT/roles"/*; do
    [[ -d "$d" ]] || continue
    b="$(basename "$d")"
    case "$b" in
      lead|builder-a|builder-b|reviewer|tester) echo "$b" ;;
      *) ;;
    esac
  done | sort -u
}

case "$cmd" in
  start)
    if [[ ! -d "$SESSION_ROOT" ]]; then
      echo "Session not found: $SESSION_ROOT" >&2
      exit 1
    fi

    : >"$PIDS_FILE"

    # Router: forwards outbox receipts into inbox messages (lead + requester).
    log="$PIDS_DIR/router.log"
    nohup python3 "$MAIN/scripts/router.py" daemon --session "$session" --poll "$poll" \
      >"$log" 2>&1 &
    echo "router $!" >>"$PIDS_FILE"

    while read -r role; do
      log="$PIDS_DIR/$role.log"
      nohup python3 "$MAIN/scripts/autopilot.py" daemon --session "$session" --role "$role" --poll "$poll" \
        >"$log" 2>&1 &
      echo "$role $!" >>"$PIDS_FILE"
    done < <(roles)
    echo "Started daemons. PIDs: $PIDS_FILE"
    ;;

  stop)
    if [[ ! -f "$PIDS_FILE" ]]; then
      echo "No pids file: $PIDS_FILE" >&2
      exit 1
    fi
    while read -r role pid; do
      if [[ -n "${pid:-}" ]]; then
        kill "$pid" 2>/dev/null || true
      fi
    done <"$PIDS_FILE"
    rm -f "$PIDS_FILE"
    echo "Stopped."
    ;;

  status)
    if [[ ! -f "$PIDS_FILE" ]]; then
      echo "No pids file: $PIDS_FILE" >&2
      exit 1
    fi
    while read -r role pid; do
      if kill -0 "$pid" 2>/dev/null; then
        echo "$role RUNNING pid=$pid"
      else
        echo "$role DEAD pid=$pid"
      fi
    done <"$PIDS_FILE"
    ;;

  *)
    usage
    exit 2
    ;;
esac
