#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/chat.sh <session-id> <role> "<message>" [mention]

Roles:
  lead | builder-a | builder-b | reviewer | tester

Notes:
  - This appends to sessions/<id>/shared/chat.md under the main worktree.
  - Use mention to tag a role: e.g. reviewer
EOF
}

if [[ $# -lt 3 ]]; then
  usage
  exit 2
fi

SESSION="$1"
ROLE="$2"
MESSAGE="$3"
MENTION="${4:-}"

case "$ROLE" in
  lead|builder-a|builder-b|reviewer|tester) ;;
  *)
    echo "Invalid role: $ROLE" >&2
    exit 2
    ;;
esac

TOP="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "${TOP:-}" ]]; then
  echo "Not in a git repo." >&2
  exit 1
fi

# First worktree listed is treated as the "main worktree" owning sessions/.
MAIN="$(git -C "$TOP" worktree list --porcelain | awk '/^worktree /{print $2; exit}')"
if [[ -z "${MAIN:-}" ]]; then
  MAIN="$TOP"
fi

CHAT="$MAIN/sessions/$SESSION/shared/chat.md"
if [[ ! -f "$CHAT" ]]; then
  echo "Chat file not found: $CHAT" >&2
  echo "Did you create the session with new-session.ps1?" >&2
  exit 1
fi

TS="$(date '+%Y-%m-%d %H:%M:%S')"
TO=""
if [[ -n "$MENTION" ]]; then
  TO=" -> @$MENTION"
fi

{
  echo ""
  echo "### [$TS] $ROLE$TO"
  echo ""
  echo "$MESSAGE"
} >>"$CHAT"

echo "Appended chat to: $CHAT"

