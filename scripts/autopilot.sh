#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/autopilot.sh start <session-id> [poll_seconds] [--model <model>] [--dry-run] [--parallel|--serial]
  ./scripts/autopilot.sh stop <session-id>
  ./scripts/autopilot.sh status <session-id>

Notes:
  - Starts a router daemon plus 1 daemon per role.
  - Workers run `codex exec` when triggered by bus inbox updates.
  - By default, workers run in parallel (Claude-style). Use --serial to enable a global lock under sessions/<id>/artifacts/locks/.
EOF
}

cmd="${1:-}"
session="${2:-}"

if [[ -z "${cmd:-}" || -z "${session:-}" ]]; then
  usage
  exit 2
fi

shift 2 || true

poll="2"
model=""
dry_run=0
global_lock="${AUTOPILOT_GLOBAL_LOCK:-0}"

if [[ "$cmd" == "start" ]]; then
  # Optional positional poll seconds.
  if [[ $# -gt 0 && "$1" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    poll="$1"
    shift
  fi

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --model) model="${2:-}"; shift 2 ;;
      --dry-run) dry_run=1; shift ;;
      --parallel) global_lock="0"; shift ;;
      --serial) global_lock="1"; shift ;;
      -h|--help) usage; exit 0 ;;
      *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
    esac
  done
else
  if [[ $# -gt 0 ]]; then
    echo "Unknown arg: $1" >&2
    usage
    exit 2
  fi
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

spawn_daemon() {
  local name="$1"
  shift
  local log="$1"
  shift
  local cmd=("$@")
  local pid=""

  nohup env PYTHONUNBUFFERED=1 AUTOPILOT_GLOBAL_LOCK="$global_lock" "${cmd[@]}" >>"$log" 2>&1 &
  pid="$!"
  sleep 0.1
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "failed to start daemon name=$name pid=$pid log=$log cmd=${cmd[*]}" >&2
    return 1
  fi
  echo "$name $pid" >>"$PIDS_FILE"
  echo "spawned name=$name pid=$pid log=$log cmd=${cmd[*]}" | tee -a "$log"
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
    router_cmd=(python3 "$MAIN/scripts/router.py" daemon --session "$session" --poll "$poll")
    if [[ "$dry_run" == "1" ]]; then
      router_cmd+=(--dry-run)
    fi
    spawn_daemon "router" "$log" "${router_cmd[@]}"

    while read -r role; do
      log="$PIDS_DIR/$role.log"
      worker_cmd=(python3 "$MAIN/scripts/autopilot.py" daemon --session "$session" --role "$role" --poll "$poll")
      if [[ "$dry_run" == "1" ]]; then
        worker_cmd+=(--dry-run)
      fi
      if [[ -n "${model:-}" ]]; then
        worker_cmd+=(--model "$model")
      fi
      spawn_daemon "$role" "$log" "${worker_cmd[@]}"
    done < <(roles)
    echo "Started daemons. PIDs: $PIDS_FILE"
    ;;

  stop)
    if [[ ! -f "$PIDS_FILE" ]]; then
      echo "No pids file: $PIDS_FILE" >&2
      exit 1
    fi
    still_alive=()
    while read -r role pid; do
      if [[ -n "${pid:-}" ]]; then
        kill "$pid" 2>/dev/null || true
      fi
    done <"$PIDS_FILE"
    sleep 2
    while read -r role pid; do
      [[ -n "${pid:-}" ]] || continue
      if kill -0 "$pid" 2>/dev/null; then
        still_alive+=("$role:$pid")
      fi
    done <"$PIDS_FILE"
    rm -f "$PIDS_FILE"
    if [[ ${#still_alive[@]} -gt 0 ]]; then
      printf 'Still alive after stop (2s): %s\n' "${still_alive[@]}" >&2
    fi
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
        ps -o pid=,ppid=,state=,etime=,command= -p "$pid" 2>/dev/null || true
      else
        echo "$role DEAD pid=$pid"
        log="$PIDS_DIR/$role.log"
        if [[ -f "$log" ]]; then
          echo "----- tail $role.log (last 30) -----"
          tail -n 30 "$log" || true
        else
          echo "(missing log) $log"
        fi
      fi
    done <"$PIDS_FILE"
    ;;

  *)
    usage
    exit 2
    ;;
esac
