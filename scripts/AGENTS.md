# Agent instructions (scope: this directory and subdirectories)

## Scope and layout
- **This AGENTS.md applies to:** `scripts/` and below.
- **What lives here:** PowerShell automation for creating and operating sessions under `sessions/<id>/...`.

## Commands
- Run scripts via PowerShell 7:
  - `pwsh ./scripts/new-session.ps1 -SessionName <id> -CreateWorktrees`
  - `pwsh ./scripts/dispatch.ps1 -SessionName <id> -Role builder-a -Message "<...>"`
  - `pwsh ./scripts/log-entry.ps1 -SessionName <id> -Role lead -Channel worklog -Message "<...>"`
  - `pwsh ./scripts/check-session.ps1 -SessionName <id>`
- mac/Linux without PowerShell:
  - `./scripts/new-session.sh <id> --create-worktrees`
- Autopilot (mac):
  - `./scripts/autopilot.sh start <session-id>`
  - `python3 ./scripts/autopilot.py daemon --session <session-id> --role lead`
- Team terminal (mac):
  - `./scripts/team.sh <session-id> --new`

## Conventions
- Cross-platform paths only:
  - use `Join-Path` with **segments** (avoid embedding `\` in strings).
- Main-worktree resolution:
  - scripts must resolve `RepoRoot` to the **main worktree** so `sessions/` is shared.
- Error handling:
  - `Set-StrictMode -Version Latest`
  - `$ErrorActionPreference = "Stop"`
  - prefer clear `throw "..."` messages that include the resolved path.

## Do not
- Do not write outside repo root (except git-managed worktrees created by `git worktree`).
- Do not run privileged operations (`sudo`) from scripts.
