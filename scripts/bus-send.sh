#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/bus-send.sh --session <sid> --from <role> --to <role|all|r1,r2> --intent <intent> --message "<text>" [--task <task_id>] [--accept "<line>"]... [--risk <low|medium|high>] [--id <id>]

Example:
  ./scripts/bus-send.sh --session demo --from lead --to builder-a --intent implement \
    --message "请实现 xxx" --accept "pytest -q" --risk medium
EOF
}

SESSION=""
FROM=""
TO_ROLES=()
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
    --to) TO_ROLES+=("$2"); shift 2 ;;
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

if [[ -z "$SESSION" || -z "$FROM" || ${#TO_ROLES[@]} -eq 0 || -z "$MESSAGE" ]]; then
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

SESSION_ROOT="$MAIN/sessions/$SESSION"

list_session_roles() {
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

expand_targets() {
  local out=()
  local raw part p
  local roles=()
  while read -r p; do
    [[ -n "${p:-}" ]] && roles+=("$p")
  done < <(list_session_roles || true)

  for raw in "${TO_ROLES[@]}"; do
    IFS=',' read -ra part <<<"$raw"
    for p in "${part[@]}"; do
      p="$(printf '%s' "$p" | tr -d '[:space:]')"
      [[ -n "$p" ]] || continue
      if [[ "${p,,}" == "all" ]]; then
        if [[ ${#roles[@]} -eq 0 ]]; then
          echo "error: --to all requires an existing session with roles/: $SESSION_ROOT" >&2
          exit 2
        fi
        for r in "${roles[@]}"; do
          [[ "$r" == "$FROM" ]] && continue
          if [[ ! " ${out[*]} " =~ " ${r} " ]]; then
            out+=("$r")
          fi
        done
      else
        if [[ ! " ${out[*]} " =~ " ${p} " ]]; then
          out+=("$p")
        fi
      fi
    done
  done
  printf '%s\n' "${out[@]}"
}

targets=()
while read -r t; do
  [[ -n "${t:-}" ]] && targets+=("$t")
done < <(expand_targets)

if [[ ${#targets[@]} -eq 0 ]]; then
  echo "error: no targets resolved" >&2
  exit 2
fi

if [[ -z "${ID:-}" ]]; then
  TS_FILE="$(date '+%Y%m%d-%H%M%S')"
  RAND="$(python3 -c 'import secrets; print(secrets.token_hex(3))' 2>/dev/null || true)"
  if [[ -z "${RAND:-}" ]]; then
    RAND="$(date +%s%N | tail -c 7)"
  fi
  ID="$TS_FILE-$RAND"
fi

base_id="$ID"

if [[ -n "${TASK_ID:-}" && ${#targets[@]} -ne 1 ]]; then
  echo "warn: --task is set but multiple --to targets resolved; skipping task dispatch update" >&2
fi

{
  # no-op placeholder so shellcheck doesn't complain about group
  :
}

for to_role in "${targets[@]}"; do
  INBOX_DIR="$SESSION_ROOT/bus/inbox/$to_role"
  mkdir -p "$INBOX_DIR"

  msg_id="$base_id"
  if [[ ${#targets[@]} -gt 1 ]]; then
    msg_id="${base_id}-${to_role}"
  fi

  TMP="$INBOX_DIR/.tmp.$msg_id.$$"
  OUT="$INBOX_DIR/$msg_id.md"

  {
    echo "---"
    echo "id: $msg_id"
    echo "from: $FROM"
    echo "to: $to_role"
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

  if [[ -n "${TASK_ID:-}" && ${#targets[@]} -eq 1 && -f "$MAIN/scripts/tasks.py" ]]; then
    python3 "$MAIN/scripts/tasks.py" dispatch \
      --session "$SESSION" \
      --task "$TASK_ID" \
      --from-role "$FROM" \
      --to-role "$to_role" \
      --intent "$INTENT" \
      --message-id "$msg_id" >/dev/null 2>&1 || true
  fi
done
