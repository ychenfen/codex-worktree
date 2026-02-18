#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  ./scripts/repro_reaper.sh <sid> [poll=2] [--model <model>] [observe_s=30]
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
observe_s="30"

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
      if [[ "$1" =~ ^[0-9]+$ ]]; then
        observe_s="$1"
        shift
      else
        echo "Unknown arg: $1" >&2
        usage
        exit 2
      fi
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
PIDS_FILE="$SESSION_ROOT/artifacts/autopilot/pids.txt"
LOG_DIR="$SESSION_ROOT/artifacts/autopilot"

if [[ ! -d "$SESSION_ROOT" ]]; then
  echo "Session not found: $SESSION_ROOT" >&2
  exit 1
fi

ps_snapshot() {
  local pid="$1"
  ps -o pid,ppid,pgid,sid,tty,stat,etime,command -p "$pid" 2>/dev/null \
    || ps -o pid,ppid,pgid,sess,tty,stat,etime,command -p "$pid" 2>/dev/null \
    || true
}

mark_first_dead() {
  local role="$1"
  local ts="$2"
  case "$role" in
    router)
      if [[ -z "${FIRST_DEAD_ROUTER:-}" ]]; then FIRST_DEAD_ROUTER="$ts"; fi
      ;;
    lead)
      if [[ -z "${FIRST_DEAD_LEAD:-}" ]]; then FIRST_DEAD_LEAD="$ts"; fi
      ;;
    builder-a)
      if [[ -z "${FIRST_DEAD_BUILDER_A:-}" ]]; then FIRST_DEAD_BUILDER_A="$ts"; fi
      ;;
    builder-b)
      if [[ -z "${FIRST_DEAD_BUILDER_B:-}" ]]; then FIRST_DEAD_BUILDER_B="$ts"; fi
      ;;
    reviewer)
      if [[ -z "${FIRST_DEAD_REVIEWER:-}" ]]; then FIRST_DEAD_REVIEWER="$ts"; fi
      ;;
    tester)
      if [[ -z "${FIRST_DEAD_TESTER:-}" ]]; then FIRST_DEAD_TESTER="$ts"; fi
      ;;
    *) ;;
  esac
}

print_first_dead() {
  local role="$1"
  local ts=""
  case "$role" in
    router) ts="${FIRST_DEAD_ROUTER:-}" ;;
    lead) ts="${FIRST_DEAD_LEAD:-}" ;;
    builder-a) ts="${FIRST_DEAD_BUILDER_A:-}" ;;
    builder-b) ts="${FIRST_DEAD_BUILDER_B:-}" ;;
    reviewer) ts="${FIRST_DEAD_REVIEWER:-}" ;;
    tester) ts="${FIRST_DEAD_TESTER:-}" ;;
    *) ts="" ;;
  esac

  if [[ -n "$ts" ]]; then
    echo "role=$role first_disappeared_ts=$ts"
  else
    echo "role=$role observe_s=${observe_s} no_disappear_observed"
  fi
}

FIRST_DEAD_ROUTER=""
FIRST_DEAD_LEAD=""
FIRST_DEAD_BUILDER_A=""
FIRST_DEAD_BUILDER_B=""
FIRST_DEAD_REVIEWER=""
FIRST_DEAD_TESTER=""

echo "=== repro_reaper start session=$session poll=$poll model=${model:--} observe_s=$observe_s ts=$(date '+%Y-%m-%d %H:%M:%S') ==="
"$MAIN/scripts/autopilot.sh" stop "$session" || true

start_cmd=("$MAIN/scripts/autopilot.sh" start "$session" "$poll")
if [[ -n "${model:-}" ]]; then
  start_cmd+=(--model "$model")
fi

quoted_start_cmd=""
for part in "${start_cmd[@]}"; do
  printf -v q '%q' "$part"
  quoted_start_cmd+="$q "
done

child_script="${quoted_start_cmd}; echo child_shell_exit ts=\$(date '+%Y-%m-%d %H:%M:%S'); exit 0"
echo "=== spawn child shell that starts daemons then exits ==="
bash -lc "$child_script"

interval_s=2
elapsed=0
round=0
while (( elapsed < observe_s )); do
  round=$((round + 1))
  ts="$(date '+%Y-%m-%d %H:%M:%S')"
  echo
  echo "=== sample#$round ts=$ts elapsed=${elapsed}s/${observe_s}s ==="
  "$MAIN/scripts/autopilot.sh" status "$session" || true

  if [[ -f "$PIDS_FILE" ]]; then
    while read -r role pid; do
      [[ -n "${pid:-}" ]] || continue
      echo "--- ps role=$role pid=$pid ---"
      ps_snapshot "$pid"
      if ! kill -0 "$pid" 2>/dev/null; then
        mark_first_dead "$role" "$ts"
      fi
    done <"$PIDS_FILE"
  else
    echo "pids_file_missing path=$PIDS_FILE"
  fi

  sleep "$interval_s"
  elapsed=$((elapsed + interval_s))
done

echo
echo "=== log matches (SIGNAL|EXIT|FATAL) last 200 lines per role ==="
for role in router lead builder-a reviewer tester; do
  log="$LOG_DIR/$role.log"
  echo "--- role=$role log=$log ---"
  if [[ -f "$log" ]]; then
    tail -n 200 "$log" | rg -n "SIGNAL|EXIT|FATAL" || true
  else
    echo "(missing log) $log"
  fi
done

echo
echo "=== conclusion (observed only) ==="
print_first_dead router
print_first_dead lead
print_first_dead builder-a
print_first_dead reviewer
print_first_dead tester

if [[ -n "${FIRST_DEAD_ROUTER:-}${FIRST_DEAD_LEAD:-}${FIRST_DEAD_BUILDER_A:-}${FIRST_DEAD_REVIEWER:-}${FIRST_DEAD_TESTER:-}" ]]; then
  echo "summary=at_least_one_disappeared_within_observe_window"
else
  echo "summary=no_disappear_within_observe_window"
fi
