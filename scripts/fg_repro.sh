#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/fg_repro.sh <session-id> <role> [seconds] [extra daemon args...]

Examples:
  ./scripts/fg_repro.sh demo-team-20260213 router 5
  ./scripts/fg_repro.sh demo-team-20260213 lead 5 --model gpt-5.2-codex
EOF
}

session="${1:-}"
role="${2:-}"
seconds="${3:-5}"

if [[ -z "${session:-}" || -z "${role:-}" ]]; then
  usage
  exit 2
fi

if [[ "$seconds" =~ ^[0-9]+$ ]]; then
  shift 3 || true
else
  seconds="5"
  shift 2 || true
fi

extra_args=("$@")

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
mkdir -p "$LOG_DIR"

case "$role" in
  router)
    cmd=(python3 "$MAIN/scripts/router.py" daemon --session "$session" --poll 2)
    ;;
  lead|builder-a|builder-b|reviewer|tester)
    cmd=(python3 "$MAIN/scripts/autopilot.py" daemon --session "$session" --role "$role" --poll 2)
    ;;
  *)
    echo "Unknown role: $role" >&2
    usage
    exit 2
    ;;
esac

log="$LOG_DIR/$role.log"
extra_print=""
if [[ ${#extra_args[@]} -gt 0 ]]; then
  extra_print=" ${extra_args[*]}"
fi
echo "[fg_repro] session=$session role=$role seconds=$seconds log=$log"
echo "[fg_repro] cmd=${cmd[*]}${extra_print}"

if [[ ${#extra_args[@]} -gt 0 ]]; then
  PYTHONUNBUFFERED=1 "${cmd[@]}" "${extra_args[@]}" \
    > >(tee -a "$log") \
    2> >(tee -a "$log" >&2) &
else
  PYTHONUNBUFFERED=1 "${cmd[@]}" \
    > >(tee -a "$log") \
    2> >(tee -a "$log" >&2) &
fi
pid="$!"
echo "[fg_repro] pid=$pid"

sleep "$seconds"

if kill -0 "$pid" 2>/dev/null; then
  echo "[fg_repro] sending SIGTERM pid=$pid"
  kill -TERM "$pid" 2>/dev/null || true
fi

set +e
wait "$pid"
rc=$?
set -e

echo "[fg_repro] rc=$rc"
echo "[fg_repro] tail -n 80 $log"
tail -n 80 "$log" || true
exit "$rc"
