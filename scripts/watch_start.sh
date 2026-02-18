#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  ./scripts/watch_start.sh <sid> [poll=2] [--model <model>]
USAGE
}

session="${1:-}"
if [[ -z "${session:-}" ]]; then
  usage
  exit 2
fi
shift || true

poll="2"
model=""

if [[ $# -gt 0 && "$1" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
  poll="$1"
  shift
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)
      model="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      usage
      exit 2
      ;;
  esac
done

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
if [[ -z "${MAIN:-}" ]]; then
  MAIN="$TOP"
fi

SESSION_ROOT="$MAIN/sessions/$session"
PIDS_DIR="$SESSION_ROOT/artifacts/autopilot"
PIDS_FILE="$PIDS_DIR/pids.txt"
EXIT_LOG="/tmp/watch_${session}.exit.log"

if [[ ! -d "$SESSION_ROOT" ]]; then
  echo "Session not found: $SESSION_ROOT" >&2
  exit 1
fi

mkdir -p "$PIDS_DIR"
: >"$PIDS_FILE"
: >"$EXIT_LOG"

roles() {
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

ps_snapshot() {
  local pid="$1"
  ps -o pid,ppid,pgid,sid,tty,stat,etime,command -p "$pid" 2>/dev/null \
    || ps -o pid,ppid,pgid,sess,tty,stat,etime,command -p "$pid" 2>/dev/null \
    || true
}

ROLE_LIST=()
PID_LIST=()
LOG_LIST=()
DONE_LIST=()
REMAINING=0

spawn_daemon() {
  local name="$1"
  shift
  local log="$1"
  shift
  local cmd=("$@")
  local pid=""

  nohup env PYTHONUNBUFFERED=1 "${cmd[@]}" >>"$log" 2>&1 &
  pid="$!"
  sleep 0.1
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "failed to start daemon name=$name pid=$pid log=$log cmd=${cmd[*]}" >&2
    tail -n 80 "$log" || true
    exit 1
  fi

  echo "$name $pid" >>"$PIDS_FILE"
  ROLE_LIST+=("$name")
  PID_LIST+=("$pid")
  LOG_LIST+=("$log")
  DONE_LIST+=("0")

  echo "spawned name=$name pid=$pid log=$log cmd=${cmd[*]}"
  echo "--- ps initial role=$name pid=$pid ---"
  ps_snapshot "$pid"
}

record_exit() {
  local idx="$1"
  local ts="$2"
  local role="${ROLE_LIST[$idx]}"
  local pid="${PID_LIST[$idx]}"
  local log="${LOG_LIST[$idx]}"
  local rc="0"
  local sig=""

  echo "ts=$ts role=$role pid=$pid 进程已消失"
  echo "ts=$ts role=$role pid=$pid process_disappeared" | tee -a "$EXIT_LOG"
  echo "--- ps on disappear role=$role pid=$pid ---"
  ps_snapshot "$pid"
  echo "--- tail -n 80 $log ---"
  tail -n 80 "$log" || true

  set +e
  wait "$pid"
  rc=$?
  set -e

  if (( rc > 128 )); then
    sig="$((rc - 128))"
    echo "EXIT ts=$ts role=$role pid=$pid rc=$rc sig=$sig" | tee -a "$EXIT_LOG"
  else
    echo "EXIT ts=$ts role=$role pid=$pid rc=$rc" | tee -a "$EXIT_LOG"
  fi

  echo "--- tail -n 80 $log (after wait role=$role pid=$pid rc=$rc) ---"
  tail -n 80 "$log" || true

  DONE_LIST[$idx]="1"
  REMAINING=$((REMAINING - 1))
}

echo "=== watch_start session=$session poll=$poll model=${model:--} ts=$(date '+%Y-%m-%d %H:%M:%S') ==="
"$MAIN/scripts/autopilot.sh" stop "$session" || true

echo "=== starting daemons (watch_start-owned children) ==="
log="$PIDS_DIR/router.log"
router_cmd=(python3 "$MAIN/scripts/router.py" daemon --session "$session" --poll "$poll")
spawn_daemon "router" "$log" "${router_cmd[@]}"

while read -r role; do
  log="$PIDS_DIR/$role.log"
  worker_cmd=(python3 "$MAIN/scripts/autopilot.py" daemon --session "$session" --role "$role" --poll "$poll")
  if [[ -n "${model:-}" ]]; then
    worker_cmd+=(--model "$model")
  fi
  spawn_daemon "$role" "$log" "${worker_cmd[@]}"
done < <(roles)

echo "=== pids.txt ==="
cat "$PIDS_FILE"

REMAINING="${#PID_LIST[@]}"
if [[ "$REMAINING" -eq 0 ]]; then
  echo "No daemons launched; exiting."
  exit 1
fi

echo "=== monitoring (1s) ==="
while (( REMAINING > 0 )); do
  ts="$(date '+%Y-%m-%d %H:%M:%S')"
  for i in "${!PID_LIST[@]}"; do
    if [[ "${DONE_LIST[$i]}" -eq 1 ]]; then
      continue
    fi
    pid="${PID_LIST[$i]}"
    if ! kill -0 "$pid" 2>/dev/null; then
      record_exit "$i" "$ts"
    fi
  done
  if (( REMAINING > 0 )); then
    sleep 1
  fi
done

echo "=== all monitored daemons exited ts=$(date '+%Y-%m-%d %H:%M:%S') ==="
echo "exit log: $EXIT_LOG"
