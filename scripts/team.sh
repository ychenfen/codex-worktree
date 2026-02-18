#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/team.sh <session-id> [--new] [--with-builder-b] [--model <model>] [--poll <seconds>] [--parallel|--serial]

What it does:
  - (optional) create session (mac native) using new-session.sh
  - start autopilot daemons if not running
  - open an interactive REPL that sends tasks/bootstraps and prints receipts

Examples:
  ./scripts/team.sh demo-team-20260213
  ./scripts/team.sh demo2 --new --poll 2 --model gpt-5.2-codex
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

session="${1:-}"
shift || true

do_new=0
with_builder_b=0
poll="2"
model=""
lock_mode=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --new) do_new=1; shift ;;
    --with-builder-b) with_builder_b=1; shift ;;
    --poll) poll="${2:-2}"; shift 2 ;;
    --model) model="${2:-}"; shift 2 ;;
    --parallel) lock_mode="--parallel"; shift ;;
    --serial) lock_mode="--serial"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "${session:-}" ]]; then
  usage
  exit 2
fi

TOP="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "${TOP:-}" ]]; then
  echo "Not in a git repo." >&2
  exit 1
fi

resolve_main_worktree() {
  local top="$1"
  local common
  common="$(git -C "$top" rev-parse --git-common-dir 2>/dev/null || true)"
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

MAIN="$(resolve_main_worktree "$TOP")"

SESSION_ROOT="$MAIN/sessions/$session"

if [[ "$do_new" == "1" && ! -d "$SESSION_ROOT" ]]; then
  args=("$session" "--create-worktrees" "--bootstrap-bus")
  if [[ "$with_builder_b" == "1" ]]; then
    args+=("--with-builder-b")
  fi
  "$MAIN/scripts/new-session.sh" "${args[@]}"
fi

if [[ ! -d "$SESSION_ROOT" ]]; then
  echo "Session not found: $SESSION_ROOT" >&2
  echo "Tip: ./scripts/team.sh $session --new" >&2
  exit 1
fi

# Start daemons if not running.
PIDS_FILE="$SESSION_ROOT/artifacts/autopilot/pids.txt"
start_args=("$session" "$poll")
if [[ -n "${model:-}" ]]; then
  start_args+=("--model" "$model")
fi
if [[ -n "${lock_mode:-}" ]]; then
  start_args+=("$lock_mode")
fi

if [[ -f "$PIDS_FILE" ]]; then
  status_out="$("$MAIN/scripts/autopilot.sh" status "$session" 2>/dev/null || true)"
  if echo "$status_out" | grep -q " DEAD "; then
    "$MAIN/scripts/autopilot.sh" stop "$session" || true
    "$MAIN/scripts/autopilot.sh" start "${start_args[@]}"
  fi
else
  "$MAIN/scripts/autopilot.sh" start "${start_args[@]}"
fi

python3 "$MAIN/scripts/team.py" repl --session "$session"
