# Changelog

## 0.2.0 - 2026-02-10

- Added `broadcast` command for multi-role fan-out dispatch.
- Added `thread --context` to view message timeline by context/thread id.
- Added `done --latest/--oldest --me <role>` to remove manual message filename selection.
- Extended message types with `PROPOSE` and `COMPARE` for structured A/B competition.
- Enhanced `watch` with optional `--type` and `--context` filters.

## 0.1.0 - 2026-02-10

- Added cross-platform `codex-team` TypeScript CLI under `packages/codex-team`.
- Added commands: `init`, `up`, `send`, `inbox`, `done`.
- Added `watch` command for continuous bus inbox polling by role.
- Added Windows Terminal and macOS iTerm2 adapters.
- Kept existing `scripts/*.ps1` as legacy prototype (no new features).
- Added examples and publishing guidance.
