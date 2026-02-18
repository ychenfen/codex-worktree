#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/launchd.sh install <session-id> [poll_seconds] [--model <model>] [--dry-run] [--serial|--parallel]
  ./scripts/launchd.sh uninstall <session-id>
  ./scripts/launchd.sh status <session-id>

Notes:
  - Installs a user LaunchAgent that runs scripts/supervisor.py (foreground), which keeps router + role daemons alive.
  - This is the closest equivalent to "always-on team mode" on macOS.
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

if [[ "$cmd" == "install" ]]; then
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
if [[ ! -d "$SESSION_ROOT" ]]; then
  echo "Session not found: $SESSION_ROOT" >&2
  exit 1
fi

label="com.ychenfen.codex-worktree.autopilot.${session}"
plist_dir="$HOME/Library/LaunchAgents"
plist="$plist_dir/${label}.plist"

mkdir -p "$plist_dir"
mkdir -p "$SESSION_ROOT/artifacts/autopilot"

out_log="$SESSION_ROOT/artifacts/autopilot/launchd.supervisor.out.log"
err_log="$SESSION_ROOT/artifacts/autopilot/launchd.supervisor.err.log"

PY="$(command -v python3 2>/dev/null || true)"
if [[ -z "${PY:-}" ]]; then
  echo "python3 not found in PATH; cannot create LaunchAgent." >&2
  exit 1
fi

cmd_args=("$PY" "$MAIN/scripts/supervisor.py" "--session" "$session" "--poll" "$poll")
if [[ "$dry_run" == "1" ]]; then
  cmd_args+=("--dry-run")
fi
if [[ -n "${model:-}" ]]; then
  cmd_args+=("--model" "$model")
fi
if [[ "$global_lock" == "1" ]]; then
  cmd_args+=("--serial")
fi

xml_escape() {
  # minimal XML escape: & < >
  printf '%s' "$1" | sed -e 's/&/&amp;/g' -e 's/</&lt;/g' -e 's/>/&gt;/g'
}

write_plist() {
  local tmp="${plist}.tmp.$$"
  {
    echo '<?xml version="1.0" encoding="UTF-8"?>'
    echo '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
    echo '<plist version="1.0">'
    echo '<dict>'
    echo '  <key>Label</key>'
    echo "  <string>$(xml_escape "$label")</string>"
    echo '  <key>RunAtLoad</key><true/>'
    echo '  <key>KeepAlive</key><true/>'
    echo '  <key>WorkingDirectory</key>'
    echo "  <string>$(xml_escape "$MAIN")</string>"
    echo '  <key>StandardOutPath</key>'
    echo "  <string>$(xml_escape "$out_log")</string>"
    echo '  <key>StandardErrorPath</key>'
    echo "  <string>$(xml_escape "$err_log")</string>"
    echo '  <key>ProgramArguments</key>'
    echo '  <array>'
    for a in "${cmd_args[@]}"; do
      echo "    <string>$(xml_escape "$a")</string>"
    done
    echo '  </array>'
    echo '  <key>EnvironmentVariables</key>'
    echo '  <dict>'
    echo '    <key>PYTHONUNBUFFERED</key><string>1</string>'
    echo "    <key>AUTOPILOT_GLOBAL_LOCK</key><string>$(xml_escape "$global_lock")</string>"
    echo '  </dict>'
    echo '</dict>'
    echo '</plist>'
  } >"$tmp"
  mv "$tmp" "$plist"
}

case "$cmd" in
  install)
    write_plist
    launchctl bootout "gui/$UID" "$plist" 2>/dev/null || true
    launchctl bootstrap "gui/$UID" "$plist"
    launchctl enable "gui/$UID/$label" 2>/dev/null || true
    echo "installed: $plist"
    echo "label: $label"
    ;;
  uninstall)
    launchctl bootout "gui/$UID" "$plist" 2>/dev/null || true
    rm -f "$plist"
    echo "uninstalled: $label"
    ;;
  status)
    echo "label: $label"
    launchctl print "gui/$UID/$label" 2>/dev/null | sed -n '1,120p' || true
    ;;
  *)
    usage
    exit 2
    ;;
esac
