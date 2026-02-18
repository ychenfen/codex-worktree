#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/bus-send.sh --session <sid> --from <role> --to <role> --intent <intent> --message "<text>" [--task <task_id>] [--accept "<line>"]... [--risk <low|medium|high>] [--id <id>]

Example:
  ./scripts/bus-send.sh --session demo --from lead --to builder-a --intent implement \
    --message "请实现 xxx" --accept "pytest -q" --risk medium
EOF
}

SESSION=""
FROM=""
TO=""
INTENT="message"
MESSAGE=""
RISK="low"
ID=""
TASK_ID=""
ACCEPT_LINES=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session) SESSION="$2"; shift 2 ;;
    --from) FROM="$2"; shift 2 ;;
    --to) TO="$2"; shift 2 ;;
    --intent) INTENT="$2"; shift 2 ;;
    --message) MESSAGE="$2"; shift 2 ;;
    --risk) RISK="$2"; shift 2 ;;
    --id) ID="$2"; shift 2 ;;
    --task) TASK_ID="$2"; shift 2 ;;
    --accept) ACCEPT_LINES+=("$2"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$SESSION" || -z "$FROM" || -z "$TO" || -z "$MESSAGE" ]]; then
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

INBOX_DIR="$MAIN/sessions/$SESSION/bus/inbox/$TO"
mkdir -p "$INBOX_DIR"

if [[ -z "${ID:-}" ]]; then
  TS_FILE="$(date '+%Y%m%d-%H%M%S')"
  RAND="$(python3 -c 'import secrets; print(secrets.token_hex(3))' 2>/dev/null || true)"
  if [[ -z "${RAND:-}" ]]; then
    RAND="$(date +%s%N | tail -c 7)"
  fi
  ID="$TS_FILE-$RAND"
fi

TMP="$INBOX_DIR/.tmp.$ID.$$"
OUT="$INBOX_DIR/$ID.md"

{
  echo "---"
  echo "id: $ID"
  echo "from: $FROM"
  echo "to: $TO"
  echo "intent: $INTENT"
  echo "thread: $SESSION"
  echo "risk: $RISK"
  if [[ -n "${TASK_ID:-}" ]]; then
    echo "task_id: $TASK_ID"
  fi
  if [[ ${#ACCEPT_LINES[@]} -gt 0 ]]; then
    echo "acceptance:"
    for a in "${ACCEPT_LINES[@]}"; do
      # naive YAML escaping: wrap in quotes, replace " with '
      aa="${a//\"/\'}"
      echo "  - \"$aa\""
    done
  fi
  echo "---"
  echo "$MESSAGE"
  echo ""
} >"$TMP"

mv "$TMP" "$OUT"
echo "Enqueued: $OUT"

if [[ -n "${TASK_ID:-}" && -f "$MAIN/scripts/tasks.py" ]]; then
  python3 "$MAIN/scripts/tasks.py" dispatch \
    --session "$SESSION" \
    --task "$TASK_ID" \
    --from-role "$FROM" \
    --to-role "$TO" \
    --intent "$INTENT" \
    --message-id "$ID" >/dev/null 2>&1 || true
fi
