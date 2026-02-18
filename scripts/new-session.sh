#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/new-session.sh <session-name> [--with-builder-b] [--create-worktrees] [--bootstrap-bus] [--base-branch <name>]

Notes:
  - mac/Linux native alternative to ./scripts/new-session.ps1 (no pwsh needed).
  - Creates sessions/<sid>/... using docs/templates + docs/prompts.
  - Optional: creates git worktrees under ../wk-<sid>/ and records them in sessions/<sid>/SESSION.md.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

SESSION_NAME="${1:-}"
shift || true

WITH_BUILDER_B=0
CREATE_WORKTREES=0
BOOTSTRAP_BUS=0
BASE_BRANCH="main"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-builder-b) WITH_BUILDER_B=1; shift ;;
    --create-worktrees) CREATE_WORKTREES=1; shift ;;
    --bootstrap-bus) BOOTSTRAP_BUS=1; shift ;;
    --base-branch) BASE_BRANCH="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "${SESSION_NAME:-}" ]]; then
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

# Always create sessions under the main worktree so all role worktrees share one sessions/<id>/ tree.
MAIN="$(resolve_main_worktree "$TOP")"

sid="$(printf '%s' "$SESSION_NAME" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9-]+/-/g; s/^-+//; s/-+$//')"
if [[ -z "${sid:-}" ]]; then
  echo "SessionName contains no valid characters." >&2
  exit 2
fi

SESSION_ROOT="$MAIN/sessions/$sid"
if [[ -e "$SESSION_ROOT" ]]; then
  echo "Session already exists: $SESSION_ROOT" >&2
  exit 1
fi

TEMPLATE_ROOT="$MAIN/docs/templates"
PROMPT_ROOT="$MAIN/docs/prompts"

created_at="$(date '+%Y-%m-%d %H:%M:%S')"

substitute() {
  # Usage: substitute <in> <out> KEY=VAL ...
  python3 - "$@" <<'PY'
import io
import sys
from pathlib import Path

in_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])
vars = {}
for kv in sys.argv[3:]:
    if "=" not in kv:
        continue
    k, v = kv.split("=", 1)
    vars[k] = v

text = in_path.read_text(encoding="utf-8")
for k, v in vars.items():
    text = text.replace("{{" + k + "}}", v)

out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(text, encoding="utf-8")
PY
}

roles=(lead builder-a reviewer tester)
if [[ "$WITH_BUILDER_B" == "1" ]]; then
  roles+=(builder-b)
fi

mkdir -p "$SESSION_ROOT/shared/chat/messages" "$SESSION_ROOT/roles" "$SESSION_ROOT/artifacts"

# Bus + state for unattended execution
mkdir -p "$SESSION_ROOT/bus/outbox" "$SESSION_ROOT/bus/inbox" "$SESSION_ROOT/bus/deadletter"
mkdir -p "$SESSION_ROOT/state/processing" "$SESSION_ROOT/state/done" "$SESSION_ROOT/state/archive" "$SESSION_ROOT/state/tasks" "$SESSION_ROOT/state/memory"

if [[ ! -f "$SESSION_ROOT/state/tasks/tasks.json" ]]; then
  cat >"$SESSION_ROOT/state/tasks/tasks.json" <<EOF
{
  "version": 1,
  "created_at": "$created_at",
  "updated_at": "$created_at",
  "tasks": []
}
EOF
fi

for r in "${roles[@]}"; do
  mkdir -p "$SESSION_ROOT/bus/inbox/$r" "$SESSION_ROOT/bus/deadletter/$r" "$SESSION_ROOT/state/archive/$r"
done

# Shared templates
for name in task decision verify pitfalls journal chat; do
  substitute "$TEMPLATE_ROOT/$name.md" "$SESSION_ROOT/shared/$name.md" \
    SESSION_ID="$sid" SESSION_ROOT="$SESSION_ROOT" CREATED_AT="$created_at"
done

# Role templates + prompts
for r in "${roles[@]}"; do
  rr="$SESSION_ROOT/roles/$r"
  mkdir -p "$rr"
  for f in inbox outbox worklog; do
    substitute "$TEMPLATE_ROOT/$f.md" "$rr/$f.md" \
      SESSION_ID="$sid" SESSION_ROOT="$SESSION_ROOT" CREATED_AT="$created_at" ROLE="$r"
  done
  if [[ -f "$PROMPT_ROOT/$r.md" ]]; then
    substitute "$PROMPT_ROOT/$r.md" "$rr/prompt.md" \
      SESSION_ID="$sid" SESSION_ROOT="$SESSION_ROOT" CREATED_AT="$created_at" ROLE="$r"
  fi
done

# Session guide (kept compatible with scripts/autopilot.py parse_role_worktrees()).
{
  echo "# Session Guide - $sid"
  echo
  echo "## Paths"
  echo
  echo "- Session root: $SESSION_ROOT"
  echo "- Shared context: $SESSION_ROOT/shared"
  echo
  echo "## Role prompt files"
  echo
  for r in "${roles[@]}"; do
    echo "- $r: \"$SESSION_ROOT/roles/$r/prompt.md\""
  done
  echo
  echo "## Suggested terminal boot"
  echo
  for r in "${roles[@]}"; do
    echo "- $r: \"cd <worktree-for-$r>; codex\""
  done
  echo
} >"$SESSION_ROOT/SESSION.md"

if [[ "$BOOTSTRAP_BUS" == "1" ]]; then
  ts="$(date '+%Y%m%d-%H%M%S')"
  boot_id="${ts}-bootstrap"
  cat >"$SESSION_ROOT/bus/inbox/lead/${boot_id}.md" <<EOF
---
id: ${boot_id}
from: system
to: lead
intent: bootstrap
thread: ${sid}
risk: low
acceptance:
  - "If shared/task.md is empty, ask for missing info (do not guess)."
  - "If shared/task.md is filled, break down and dispatch to roles via bus-send.sh."
---
Bootstrap autopilot for session ${sid}.

Read:
- shared/task.md
- docs/team-mode.md
- docs/bus.md

Then:
- If task is actionable: dispatch messages to bus/inbox/<role>/ using ./scripts/bus-send.sh.
- Otherwise: write what is missing and ask for clarification.
EOF
fi

resolve_base_branch() {
  local preferred="$1"
  if git -C "$MAIN" show-ref --verify --quiet "refs/heads/$preferred"; then
    echo "$preferred"
    return 0
  fi
  cur="$(git -C "$MAIN" branch --show-current 2>/dev/null || true)"
  if [[ -n "${cur:-}" ]]; then
    echo "$cur"
    return 0
  fi
  if git -C "$MAIN" show-ref --verify --quiet "refs/heads/master"; then
    echo "master"
    return 0
  fi
  git -C "$MAIN" for-each-ref --format='%(refname:short)' refs/heads | head -n 1
}

add_worktree() {
  local role="$1"
  local base="$2"
  local root="$3"
  local branch="session/$sid/$role"
  local target="$root/$role"

  if [[ -e "$target" ]]; then
    echo "[skip] worktree exists: $target" >&2
    return 0
  fi

  if git -C "$MAIN" show-ref --verify --quiet "refs/heads/$branch"; then
    git -C "$MAIN" worktree add "$target" "$branch" >/dev/null
  else
    git -C "$MAIN" worktree add -b "$branch" "$target" "$base" >/dev/null
  fi
}

if [[ "$CREATE_WORKTREES" == "1" ]]; then
  base="$(resolve_base_branch "$BASE_BRANCH")"
  wk_root="$(cd "$(dirname "$MAIN")" && pwd)/wk-$sid"
  mkdir -p "$wk_root"
  for r in "${roles[@]}"; do
    add_worktree "$r" "$base" "$wk_root"
  done

  {
    echo
    echo "## Worktree root"
    echo "- $wk_root"
    echo
    echo "## Role worktrees"
    for r in "${roles[@]}"; do
      echo "- $r: $wk_root/$r"
    done
    echo
  } >>"$SESSION_ROOT/SESSION.md"
fi

echo "Session created: $SESSION_ROOT"
echo "Open: $SESSION_ROOT/SESSION.md"
