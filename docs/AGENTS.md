# Agent instructions (scope: this directory and subdirectories)

## Scope and layout
- **This AGENTS.md applies to:** `docs/` and below.
- **Key directories:**
  - `docs/prompts/`: role prompts to paste into Codex/Claude Code.
  - `docs/templates/`: Markdown templates rendered into `sessions/<id>/...`.
  - `docs/protocol.md`: workflow rules and boundaries.
  - `docs/team-mode.md`: task classification and routing guide.

## Conventions
- Keep docs **operational**: short rules, explicit file paths, explicit "done" signals.
- Every role prompt must:
  - state **scope** (what to read/write)
  - state **forbidden actions**
  - define a **handoff format** (what goes into outbox / verify / decision)
- Templates should be "fillable" and script-friendly:
  - keep section headers stable (so checks can be automated)
  - keep placeholders obvious (`暂无...`, `待执行`, `待评审`)

## Common pitfalls
- Avoid copying OS-specific paths (prefer `./scripts/...` and logical paths like `sessions/<id>/...`).
- If you add a new required section to a template, update:
  - the corresponding role prompt(s)
  - `scripts/check-session.ps1` checks (if applicable)

## Do not
- Do not document secrets or credentials.
- Do not add long essays; link to deeper docs only if necessary.

