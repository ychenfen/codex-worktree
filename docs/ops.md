# Ops Quick Runbook

## Minimal Start/Stop/Status

```bash
./scripts/autopilot.sh stop demo-team-20260213 || true
./scripts/autopilot.sh start demo-team-20260213 2 --model gpt-5.2-codex
./scripts/autopilot.sh status demo-team-20260213
```

## Trigger Bootstrap

```bash
./scripts/team.sh demo-team-20260213 <<'EOF'
/bootstrap
/exit
EOF
```

## One-Page Diagnostics

```bash
./scripts/diag.sh demo-team-20260213
```

## Local Validation (No New Deps)

macOS sandboxing can block writing `.pyc` under `~/Library/Caches/...`.
Use `PYTHONPYCACHEPREFIX` to force bytecode to a writable location.

```bash
PYTHONPYCACHEPREFIX=/tmp/codex_pycache python3 -m py_compile scripts/*.py
bash -n scripts/*.sh
PYTHONDONTWRITEBYTECODE=1 python3 scripts/tests/test_router_errno22.py
```
