# Changelog

## 0.3.1 - 2026-02-10

- Normalized multi-line `--action`/header values into single-line bus headers to avoid thread truncation.
- Added `--dangerously-bypass-approvals-and-sandbox` passthrough for `auto` and `orchestrate`.
- `auto` now explicitly requests `workspace-write` sandbox when not in dangerous bypass mode.
- Updated docs with no-touch orchestration and PowerShell input caveats.

## 0.3.0 - 2026-02-10

- Added `orchestrate` command to start/stop background auto workers by context.
- Added `orchestrate --stop` to terminate all worker processes and persist stop state.
- Updated `up` internals with `launchTerminal` option so orchestration can prepare worktrees without opening panes.
- Updated docs with orchestrated startup flow for non-manual role execution.

## 0.2.0 - 2026-02-10

- Added `broadcast` command for multi-role fan-out dispatch.
- Added `thread --context` to view message timeline by context/thread id.
- Added `done --latest/--oldest --me <role>` to remove manual message filename selection.
- Extended message types with `PROPOSE` and `COMPARE` for structured A/B competition.
- Enhanced `watch` with optional `--type` and `--context` filters.
- Added `auto` worker mode to process NEW bus messages via `codex exec` and auto-complete with `done`.

## 0.1.0 - 2026-02-10

- Added cross-platform `codex-team` TypeScript CLI under `packages/codex-team`.
- Added commands: `init`, `up`, `send`, `inbox`, `done`.
- Added `watch` command for continuous bus inbox polling by role.
- Added Windows Terminal and macOS iTerm2 adapters.
- Kept existing `scripts/*.ps1` as legacy prototype (no new features).
- Added examples and publishing guidance.
