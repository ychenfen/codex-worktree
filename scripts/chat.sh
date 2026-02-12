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

CHAT_INDEX="$MAIN/sessions/$SESSION/shared/chat.md"
if [[ ! -f "$CHAT_INDEX" ]]; then
  echo "Chat file not found: $CHAT_INDEX" >&2
  echo "Did you create the session with new-session.ps1?" >&2
  exit 1
fi

MSG_DIR="$MAIN/sessions/$SESSION/shared/chat/messages"
mkdir -p "$MSG_DIR"

TS="$(date '+%Y-%m-%d %H:%M:%S')"
TS_FILE="$(date '+%Y%m%d-%H%M%S')"
TO=""
if [[ -n "$MENTION" ]]; then
  TO=" -> @$MENTION"
fi

RAND="$(python3 -c 'import secrets; print(secrets.token_hex(3))' 2>/dev/null || true)"
if [[ -z "${RAND:-}" ]]; then
  RAND="$(date +%s%N | tail -c 7)"
fi
MSG_FILE="$MSG_DIR/$TS_FILE-$ROLE-$RAND.md"
cat >"$MSG_FILE" <<EOF
### [$TS] $ROLE$TO

$MESSAGE
EOF

echo "Wrote chat message: $MSG_FILE"
